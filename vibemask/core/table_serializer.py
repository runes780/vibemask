"""
Format-agnostic table serializer.

Document processors extract text segments with a ``location`` string that
encodes the segment's position inside the source document. For tabular formats
the location also carries row/column structure:

* XLSX cell: ``"xl/worksheets/sheet1.xml:A1"``  (part + cell reference)
* DOCX table cell: ``"word/document.xml:tbl0.r1.c2"``  (part + table/row/col)
* Legacy ``.xls`` cell (via xlrd): ``"sheet0:row1:col2"``  (sheet + row/col)

The flat ``get_combined_text()`` path joins every segment with ``\\n``, so the
model loses the header/value relationship that disambiguates a value's type.
This module reconstructs the ``"header: value | header: value"`` form from the
segment locations and produces a bidirectional offset map so model spans on the
reconstructed text can be projected back to the original combined-text offsets
that :class:`DocumentProcessor.replace_spans` consumes.

Only tables whose header row carries a privacy hint are serialized — financial
tables / company lists without privacy headers stay on the flat path (keeping
the existing zero-false-positive guarantee on non-PII tables).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .processor import TextSegment


# Location parsers. Each document family uses a distinct location grammar:
# * XLSX cell:  ``"xl/worksheets/sheet1.xml:A1"``  (part + Excel cell ref)
# * DOCX table cell: ``"word/document.xml:tbl0.r1.c2"``  (part + tbl/row/col)
# * Legacy .xls/.doc (via LibreOffice + xlrd): ``"sheet0:row1:col2"``  (no part)
_CELL_RE = re.compile(r"^(?P<part>.+):(?P<cell>\$?[A-Z]+\$?\d+)$")
_DOCX_CELL_RE = re.compile(r"^(?P<part>.+):(?P<tbl>tbl\d+)\.r(?P<row>\d+)\.c(?P<col>\d+)$")
_LEGACY_CELL_RE = re.compile(r"^(?P<sheet>sheet\d+):row(?P<row>\d+):col(?P<col>\d+)$")


@dataclass
class ContextValueMap:
    """Bidirectional offset bridge between serialized context text and the
    processor's combined-text coordinate space.

    ``context_*`` offsets index into the reconstructed ``"header: value"`` text
    that the model sees; ``original_*`` offsets index into the combined text
    that :meth:`DocumentProcessor.replace_spans` consumes (i.e.
    ``segment.start_offset``/``segment.end_offset``).
    """

    context_start: int
    context_end: int
    original_start: int
    original_end: int
    original_text: str
    header: str = ""


def serialize_tables(
    segments: Iterable[Any],
) -> tuple[str, list[ContextValueMap]]:
    """Reconstruct ``header: value | header: value`` rows for every privacy
    table found in ``segments``.

    Returns ``(context_text, mappings)``. When no privacy table is present both
    are empty — callers fall back to flat-text detection.

    ``segments`` is the processor's ``_segments`` list. Each item must expose
    ``.location`` (str), ``.text`` (str), ``.start_offset`` and ``.end_offset``
    (int) — i.e. :class:`TextSegment` (or a duck-typed equivalent).
    """
    segments = list(segments)
    # Group cells into rows. XLSX and DOCX use different location grammars, so
    # we try both parsers; a segment matches at most one.
    rows = _group_into_rows(segments)
    if not rows:
        return "", []

    lines: list[str] = []
    mappings: list[ContextValueMap] = []
    context_pos = 0

    # ``rows`` is keyed by (part, table_id) -> {row_index: {col_key: segment}}.
    # Iterate in a stable order so output is deterministic across runs.
    for key in sorted(rows, key=lambda k: (str(k[0]), str(k[1]))):
        table_rows = rows[key]
        header_row = _find_header_row(table_rows)
        if header_row is None:
            continue

        headers = {
            col: _normalize_header(seg.text)
            for col, seg in table_rows[header_row].items()
            if getattr(seg, "text", "").strip()
        }
        if not _has_privacy_header(headers.values()):
            continue

        for row in sorted(r for r in table_rows if r > header_row):
            cells = table_rows[row]
            line, line_maps, line_len = _serialize_one_row(cells, headers, context_pos)
            if line is None:
                continue
            lines.append(line)
            mappings.extend(line_maps)
            context_pos += line_len + 1  # +1 for the '\n' join

    return "\n".join(lines), mappings


def _serialize_one_row(
    cells: dict[Any, Any],
    headers: dict[Any, str],
    context_pos: int,
) -> tuple[str | None, list[ContextValueMap], int]:
    """Serialize a single data row into ``"h: v | h: v"`` form.

    Returns ``(line, mappings, line_length)`` or ``(None, [], 0)`` when the row
    has no serializable value. ``line_length`` excludes the trailing newline.
    """
    parts: list[str] = []
    pending: list[tuple[int, Any, str, str]] = []  # (value_start, seg, value, header)

    for col in sorted(cells, key=_column_sort_key):
        header = headers.get(col)
        seg = cells[col]
        value = getattr(seg, "text", "").strip()
        if not header or not value:
            continue
        prefix = f"{header}: "
        # Position of the value within the line being assembled.
        value_start = sum(len(p) for p in parts) + len(prefix)
        parts.append(prefix + value)
        if _is_privacy_column(header):
            pending.append((value_start, seg, value, header))
        parts.append(" | ")

    if not parts:
        return None, [], 0
    if parts[-1] == " | ":
        parts.pop()
    line = "".join(parts)

    row_maps: list[ContextValueMap] = []
    for value_start, seg, value, header in pending:
        ctx_start = context_pos + value_start
        original_text = getattr(seg, "text", value)
        row_maps.append(
            ContextValueMap(
                context_start=ctx_start,
                context_end=ctx_start + len(value),
                original_start=getattr(seg, "start_offset", 0),
                original_end=getattr(seg, "end_offset", getattr(seg, "start_offset", 0)),
                original_text=original_text,
                header=header,
            )
        )
    return line, row_maps, len(line)


# ---------------------------------------------------------------------------
# Row grouping: normalize XLSX and DOCX locations into one row structure.
# ---------------------------------------------------------------------------

def _group_into_rows(
    segments: list[Any],
) -> dict[tuple[str, str], dict[int, dict[Any, Any]]]:
    """Group cell segments into ``{(part, table_id): {row: {col_key: segment}}}``.

    XLSX: ``table_id`` is the sheet part; ``col_key`` is the column letter;
    row comes from the cell reference digits.
    DOCX: ``table_id`` is ``tblN``; ``col_key`` is the column index; both row
    and col come from the ``rN.cN`` suffix.
    """
    rows: dict[tuple[str, str], dict[int, dict[Any, Any]]] = {}
    for seg in segments:
        location = getattr(seg, "location", "") or ""
        xlsx = _CELL_RE.match(location)
        if xlsx is not None:
            part = xlsx.group("part")
            cell = xlsx.group("cell").replace("$", "")
            col = "".join(ch for ch in cell if ch.isalpha())
            row = int("".join(ch for ch in cell if ch.isdigit()))
            key = (part, part)  # one "table" per sheet part
            rows.setdefault(key, {}).setdefault(row, {})[col] = seg
            continue
        docx = _DOCX_CELL_RE.match(location)
        if docx is not None:
            part = docx.group("part")
            tbl = docx.group("tbl")
            row = int(docx.group("row"))
            col = int(docx.group("col"))
            key = (part, tbl)
            rows.setdefault(key, {}).setdefault(row, {})[col] = seg
            continue
        legacy = _LEGACY_CELL_RE.match(location)
        if legacy is not None:
            sheet = legacy.group("sheet")
            row = int(legacy.group("row"))
            col = int(legacy.group("col"))
            # Legacy sheets have no "part"; key on the sheet id itself.
            key = (sheet, sheet)
            rows.setdefault(key, {}).setdefault(row, {})[col] = seg
    return rows


def _column_sort_key(col: Any) -> tuple[int, Any]:
    """Sort columns left-to-right regardless of whether the key is an int
    (DOCX) or a string letter (XLSX). Ints sort numerically; letters sort by
    spreadsheet column order via :func:`_column_number`."""
    if isinstance(col, int):
        return (0, col)
    return (0, _column_number(str(col)))


def _column_number(column: str) -> int:
    number = 0
    for char in column:
        number = number * 26 + (ord(char.upper()) - ord("A") + 1)
    return number


# ---------------------------------------------------------------------------
# Header detection — reused verbatim from privacy_context so behavior is
# identical (which headers count as "privacy", whitespace normalization).
# ---------------------------------------------------------------------------

_PRIVACY_HEADER_HINTS = {
    "姓名", "个人主页", "手机", "手机号码", "联系电话", "电话", "邮箱",
    "电子邮箱", "身份证", "证件号", "学号", "工号",
}

# Header -> entity type. Re-exported so structural span emission can keep using
# one authoritative map.
HEADER_TYPE_MAP: dict[str, str] = {
    "姓名": "PERSON", "名字": "PERSON", "申请人": "PERSON", "联系人": "PERSON",
    "负责人": "PERSON", "经办人": "PERSON", "考生": "PERSON", "申报人": "PERSON", "姓名/名称": "PERSON",
    "手机": "PHONE", "手机号": "PHONE", "手机号码": "PHONE", "电话": "PHONE",
    "联系电话": "PHONE", "联系电话号码": "PHONE", "座机": "PHONE", "办公电话": "PHONE", "办公室电话": "PHONE",
    "邮箱": "EMAIL", "电子邮箱": "EMAIL", "电子邮件": "EMAIL", "email": "EMAIL", "e-mail": "EMAIL",
    "身份证": "IDCN", "身份证号": "IDCN", "身份证号码": "IDCN", "证件号": "IDCN", "证件号码": "IDCN",
    "学号": "ACCOUNT_NUMBER", "学籍号": "ACCOUNT_NUMBER", "工号": "ACCOUNT_NUMBER",
    "员工号": "ACCOUNT_NUMBER", "员工编号": "ACCOUNT_NUMBER", "客户编号": "ACCOUNT_NUMBER",
    "客户号": "ACCOUNT_NUMBER", "会员号": "ACCOUNT_NUMBER", "会员编号": "ACCOUNT_NUMBER",
    "准考证号": "ACCOUNT_NUMBER", "考生号": "ACCOUNT_NUMBER", "考号": "ACCOUNT_NUMBER",
    "卡号": "ACCOUNT_NUMBER", "病历号": "ACCOUNT_NUMBER", "档案号": "ACCOUNT_NUMBER",
    "社保号": "ACCOUNT_NUMBER", "公积金号": "ACCOUNT_NUMBER",
    "地址": "ADDRESS", "住址": "ADDRESS", "通讯地址": "ADDRESS", "联系地址": "ADDRESS",
    "家庭住址": "ADDRESS", "常住地址": "ADDRESS",
    "url": "URL", "主页": "URL", "个人主页": "URL", "网址": "URL", "个人网址": "URL", "网站": "URL",
}


def _normalize_header(text: str) -> str:
    """Collapse whitespace so ``姓 名`` matches ``姓名`` (LibreOffice/Word often
    insert spaces inside CJK headers)."""
    return "".join(text.split())


def _header_type(header: str) -> str | None:
    h = _normalize_header(header)
    hl = h.lower()
    for keyword, etype in HEADER_TYPE_MAP.items():
        if keyword in h or keyword in hl:
            return etype
    return None


def _is_privacy_column(header: str) -> bool:
    """A column is a privacy column if its header matches a known hint or maps
    to an entity type."""
    nv = _normalize_header(header)
    if nv in _PRIVACY_HEADER_HINTS or any(hint in nv for hint in _PRIVACY_HEADER_HINTS):
        return True
    return _header_type(header) is not None


def _has_privacy_header(values: Iterable[str]) -> bool:
    for value in values:
        if _is_privacy_column(value):
            return True
    return False


def _find_header_row(
    table_rows: dict[int, dict[Any, Any]],
) -> int | None:
    """First row with >= 2 non-empty cells whose values include a privacy
    header."""
    for row in sorted(table_rows):
        cells = table_rows[row]
        values = [
            getattr(seg, "text", "").strip()
            for seg in cells.values()
            if getattr(seg, "text", "").strip()
        ]
        if len(values) >= 2 and _has_privacy_header(values):
            return row
    return None


def map_context_spans(
    detected: list[Any],
    mappings: list[ContextValueMap],
) -> list[Any]:
    """Project model spans (on serialized context text) back to original
    combined-text offsets via the value maps.

    Spans that don't land on a value cell (e.g. a header word the model
    mis-flagged) are dropped — this is how "header as label" is enforced.
    ``detected`` items must expose ``.start``/``.end``/``.text``/``.type``/
    ``.source``/``.confidence``/``.reason`` (i.e. :class:`Span`).
    """
    # Local import keeps Span's heavy enum imports out of module load time.
    from .span import Span

    mapped: list[Any] = []
    for span in detected:
        value_map = _find_value_mapping(span, mappings)
        if value_map is None:
            continue
        local_start = max(0, span.start - value_map.context_start)
        local_end = min(
            value_map.context_end - value_map.context_start,
            span.end - value_map.context_start,
        )
        original_text = value_map.original_text[local_start:local_end]
        if not original_text:
            continue
        mapped.append(
            Span(
                start=value_map.original_start + local_start,
                end=value_map.original_start + local_end,
                text=original_text,
                type=span.type,
                source=span.source,
                confidence=span.confidence,
                reason=f"{span.reason}+row_context" if span.reason else "row_context",
            )
        )
    return mapped


def _find_value_mapping(
    span: Any,
    mappings: list[ContextValueMap],
) -> ContextValueMap | None:
    for mapping in mappings:
        if mapping.context_start <= span.start and span.end <= mapping.context_end:
            return mapping
    return None
