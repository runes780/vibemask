"""
Structured context builders for Privacy Filter.

These builders present Office data in a form the model can understand while
mapping detected spans back to the original document offsets for reversible
replacement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..core.span import EntityType, SourceType, Span


CELL_RE = re.compile(r"^(?P<part>.+):(?P<cell>\$?[A-Z]+\$?\d+)$")
DOCX_CELL_RE = re.compile(r"^(?P<part>.+):(?P<tbl>tbl\d+)\.r(?P<row>\d+)\.c(?P<col>\d+)$")
PRIVACY_HEADER_HINTS = {
    "姓名",
    "个人主页",
    "手机",
    "手机号码",
    "联系电话",
    "电话",
    "邮箱",
    "电子邮箱",
    "身份证",
    "证件号",
    "学号",
    "工号",
}


@dataclass
class ContextValueMap:
    context_start: int
    context_end: int
    original_start: int
    original_end: int
    original_text: str
    header: str = ""  # column header that labelled this value


# Map a column header to the entity type of its values. When a column is headed
# 学号/工号/姓名/手机/..., every value in it IS that kind of PII by definition —
# so we detect them structurally instead of relying on the model to flag each one
# (the model missed ~16% of student IDs on a real 1400-row list).
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




def _structural_spans_from_mappings(mappings: list[ContextValueMap]) -> list[Span]:
    """One span per labelled-column value — typed by its header — but ONLY when
    the value's format matches the claimed type. This fills the model's recall
    gap on identifiers (学号/工号/准考证号/手机/邮箱/身份证/URL) without emitting
    garbage on messy tables: PERSON/ADDRESS/ORG are too ambiguous to trust
    structurally (a 4-CJK cell could be a name or a label like 工作单位), so they
    are left to the heuristic/model layers.

    Accepts any duck-typed mapping exposing ``header``/``original_start``/
    ``original_end``/``original_text`` — in particular the
    :class:`vibemask.core.table_serializer.ContextValueMap` produced by the
    unified serializer.
    """
    spans: list[Span] = []
    for m in mappings:
        etype = _header_type(m.header)
        if etype is None:
            continue
        if not _value_matches_type(m.original_text, etype):
            continue
        try:
            entity = EntityType[etype]
        except KeyError:
            continue
        spans.append(
            Span(
                start=m.original_start,
                end=m.original_end,
                text=m.original_text,
                type=entity,
                source=SourceType.SCHEMA,
                confidence=0.9,
                reason=f"structural_column:{m.header}",
            )
        )
    return spans


# Public alias so hybrid.py can call it without reaching for a private name.
# Kept as a thin wrapper (not a rename) to avoid disturbing legacy callers.
def structural_spans_from_mappings(mappings: list) -> list[Span]:
    """Public entry point for :func:`_structural_spans_from_mappings`."""
    return _structural_spans_from_mappings(mappings)


# Format validators: a structural span is only emitted when the cell value
# actually looks like the type its header claims. Keeps real identifiers, drops
# 序号 (1,2,3), header labels leaked into data, and other table noise.
_VALUE_PATTERNS: dict[str, "re.Pattern"] = {
    "PHONE": re.compile(r"^[\d][\d\-+ ]{6,}$"),
    "EMAIL": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "IDCN": re.compile(r"^\d{15}$|^\d{17}[\dXx]$"),
    "ACCOUNT_NUMBER": re.compile(r"^[A-Za-z0-9]{4,}$"),
    "URL": re.compile(r"^(https?://|www\.|[\w-]+\.[\w-]+)"),
}
# Types we never emit structurally (too ambiguous — left to model/heuristic).
_STRUCTURAL_SKIP_TYPES = {"ADDRESS", "ORG", "DATE_TIME"}


def _value_matches_type(value: str, etype: str) -> bool:
    v = value.strip()
    if not v:
        return False
    if etype == "PERSON":
        return _is_plausible_name(v)
    if etype in _STRUCTURAL_SKIP_TYPES:
        return False
    pat = _VALUE_PATTERNS.get(etype)
    return bool(pat.match(v)) if pat is not None else True


def _is_plausible_name(value: str) -> bool:
    """A 姓名-column cell: trust the header (high recall — the column IS names)
    but reject obvious header labels that leaked into data rows. Real names are
    2-4 CJK chars and never contain label suffixes like 单位/学校/情况/称号."""
    v = value.strip()
    if not (2 <= len(v) <= 4) or not all("一" <= c <= "鿿" for c in v):
        return False
    from .privacy_postprocess import is_false_person, is_field_label

    if is_field_label(v) or is_false_person(v):
        return False
    if any(suf in v for suf in _LABEL_SUFFIXES):
        return False
    return True


# Suffixes that mark a cell as a field label / descriptor, never a person name.
_LABEL_SUFFIXES = (
    "单位", "学校", "情况", "类别", "时间", "称号", "职称", "年限", "代码",
    "业务", "项目", "范围", "内容", "说明", "学科", "教学", "机构", "部门",
    "名称", "事项", "指标", "金额", "日期", "年度", "月份",
)


def _group_docx_rows(segments: list[Any]) -> dict[tuple[str, str], dict[int, dict[int, Any]]]:
    """Group DOCX table-cell segments by (part, table) -> {row: {col: segment}}."""
    rows: dict[tuple[str, str], dict[int, dict[int, Any]]] = {}
    for segment in segments:
        match = DOCX_CELL_RE.match(getattr(segment, "location", "") or "")
        if not match:
            continue
        key = (match.group("part"), match.group("tbl"))
        row = int(match.group("row"))
        col = int(match.group("col"))
        rows.setdefault(key, {}).setdefault(row, {})[col] = segment
    return rows


def detect_table_cell_ranges(processor: Any) -> list[tuple[int, int]]:
    """Offset ranges of every data cell in a table that structural detection
    owns (a table whose header row carries a privacy hint). The model's
    flat-text pass should skip these cells — structural detection (plus the
    model-on-reconstructed-context run inside detect_*_row_context) already
    covers them, and the flat pass on table cells only adds false positives
    (position codes, run-fragmentation). The model still runs on narrative
    text outside tables."""
    suffix = getattr(getattr(processor, "file_path", None), "suffix", "").lower()
    segments = list(getattr(processor, "_segments", []) or [])
    if suffix == ".xlsx":
        grouped = _group_xlsx_rows(segments)  # {(part, row): {col: cell}}
        tables: dict[Any, dict[int, Any]] = {}
        for (part, row), cells in grouped.items():
            tables.setdefault(part, {})[row] = cells
        find_header = _find_header_row
    elif suffix == ".docx":
        tables = _group_docx_rows(segments)  # {(part, tbl): {row: {col: cell}}}
        find_header = _find_header_row_generic
    else:
        return []

    ranges: list[tuple[int, int]] = []
    for table_rows in tables.values():
        header_row = find_header(table_rows)
        if header_row is None:
            continue  # structural didn't own this table — leave it to the model
        for row in sorted(r for r in table_rows if r != header_row):
            for cell in table_rows[row].values():
                if getattr(cell, "text", "").strip():
                    ranges.append((cell.start_offset, cell.end_offset))
    return ranges


def detect_xlsx_row_context(processor: Any, privacy_detector: Any) -> list[Span]:
    if getattr(getattr(processor, "file_path", None), "suffix", "").lower() != ".xlsx":
        return []

    segments = list(getattr(processor, "_segments", []) or [])
    rows = _group_xlsx_rows(segments)
    if not rows:
        return []

    context_text, mappings = _build_row_context(rows)
    if not context_text or not mappings:
        return []

    detected = privacy_detector.detect(context_text)
    # Model-flagged spans mapped back to original offsets, plus one structural
    # span per labelled-column value (fills model recall gaps on identifiers).
    return _map_context_spans(detected, mappings) + _structural_spans_from_mappings(mappings)


def _group_xlsx_rows(segments: list[Any]) -> dict[tuple[str, int], dict[str, Any]]:
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for segment in segments:
        match = CELL_RE.match(getattr(segment, "location", ""))
        if not match:
            continue
        part = match.group("part")
        cell = match.group("cell").replace("$", "")
        col = "".join(ch for ch in cell if ch.isalpha())
        row = int("".join(ch for ch in cell if ch.isdigit()))
        rows.setdefault((part, row), {})[col] = segment
    return rows


def _build_row_context(rows: dict[tuple[str, int], dict[str, Any]]) -> tuple[str, list[ContextValueMap]]:
    lines: list[str] = []
    mappings: list[ContextValueMap] = []
    context_pos = 0

    parts = sorted({part for part, _ in rows})
    for part in parts:
        part_rows = {row: cells for (row_part, row), cells in rows.items() if row_part == part}
        header_row = _find_header_row(part_rows)
        if header_row is None:
            continue

        headers = {
            col: _normalize_header(cell.text)
            for col, cell in part_rows[header_row].items()
            if getattr(cell, "text", "").strip()
        }
        if not _has_privacy_header(headers.values()):
            continue

        for row in sorted(r for r in part_rows if r > header_row):
            cells = part_rows[row]
            parts_for_line: list[str] = []
            pending_mappings: list[tuple[int, Any, str, str]] = []
            for col in sorted(cells, key=_column_number):
                header = headers.get(col)
                value = cells[col].text.strip()
                if not header or not value:
                    continue
                prefix = f"{header}: "
                value_start = sum(len(part) for part in parts_for_line) + len(prefix)
                parts_for_line.append(prefix + value)
                if header in PRIVACY_HEADER_HINTS or any(hint in header for hint in PRIVACY_HEADER_HINTS) or _header_type(header) is not None:
                    pending_mappings.append((value_start, cells[col], value, header))
                parts_for_line.append(" | ")
            if parts_for_line:
                if parts_for_line[-1] == " | ":
                    parts_for_line.pop()
                line = "".join(parts_for_line)
                for value_start, cell, value, header in pending_mappings:
                    context_start = context_pos + value_start
                    mappings.append(
                        ContextValueMap(
                            context_start=context_start,
                            context_end=context_start + len(value),
                            original_start=cell.start_offset,
                            original_end=cell.end_offset,
                            original_text=cell.text,
                            header=header,
                        )
                    )
                lines.append(line)
                context_pos += len(line) + 1

    return "\n".join(lines), mappings


def _find_header_row(rows: dict[int, dict[str, Any]]) -> int | None:
    for row in sorted(rows):
        values = [cell.text.strip() for cell in rows[row].values() if getattr(cell, "text", "").strip()]
        if len(values) >= 2 and _has_privacy_header(values):
            return row
    return None


def _has_privacy_header(values) -> bool:
    # A column is a PII column if its header matches a known privacy hint OR
    # maps to an entity type (covers 准考证号/工号/客户编号 which are PII columns
    # but not in the legacy hint set). Headers are whitespace-normalized.
    for value in values:
        nv = _normalize_header(value)
        if nv in PRIVACY_HEADER_HINTS or any(hint in nv for hint in PRIVACY_HEADER_HINTS):
            return True
        if _header_type(value) is not None:
            return True
    return False


def _find_value_mapping(span: Span, mappings: list[ContextValueMap]) -> ContextValueMap | None:
    for mapping in mappings:
        if mapping.context_start <= span.start and span.end <= mapping.context_end:
            return mapping
    return None


def _map_context_spans(detected: list[Span], mappings: list[ContextValueMap]) -> list[Span]:
    """Map model spans (on the reconstructed context text) back to original
    document offsets via the value maps. Spans that don't land on a value cell
    (e.g. a header word the model mis-flagged) are dropped."""
    mapped: list[Span] = []
    for span in detected:
        value_map = _find_value_mapping(span, mappings)
        if value_map is None:
            continue
        local_start = max(0, span.start - value_map.context_start)
        local_end = min(value_map.context_end - value_map.context_start, span.end - value_map.context_start)
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


def detect_docx_table_context(processor: Any, privacy_detector: Any) -> list[Span]:
    """Reconstruct ``header: value`` pairs from DOCX tables and run the model on
    them — same idea as the XLSX path. Headers act as labels (never fed as
    values to detect), so a header like ``手机号`` is no longer mis-flagged as a
    person, and ambiguous values get strong type priors from their column.
    """
    if getattr(getattr(processor, "file_path", None), "suffix", "").lower() != ".docx":
        return []

    segments = list(getattr(processor, "_segments", []) or [])
    # rows: (part, tbl) -> {row: {col: segment}}
    rows: dict[tuple[str, str], dict[int, dict[int, Any]]] = {}
    for segment in segments:
        match = DOCX_CELL_RE.match(getattr(segment, "location", "") or "")
        if not match:
            continue
        key = (match.group("part"), match.group("tbl"))
        row = int(match.group("row"))
        col = int(match.group("col"))
        rows.setdefault(key, {}).setdefault(row, {})[col] = segment
    if not rows:
        return []

    context_text, mappings = _build_docx_row_context(rows)
    if not context_text or not mappings:
        return []

    detected = privacy_detector.detect(context_text)
    return _map_context_spans(detected, mappings) + _structural_spans_from_mappings(mappings)


def _build_docx_row_context(
    rows: dict[tuple[str, str], dict[int, dict[int, Any]]],
) -> tuple[str, list[ContextValueMap]]:
    lines: list[str] = []
    mappings: list[ContextValueMap] = []
    context_pos = 0

    for key in sorted(rows):
        table_rows = rows[key]
        header_row = _find_header_row_generic(table_rows)
        if header_row is None:
            continue
        headers = {
            col: _normalize_header(cell.text)
            for col, cell in table_rows[header_row].items()
            if getattr(cell, "text", "").strip()
        }
        if not _has_privacy_header(headers.values()):
            continue

        for row in sorted(r for r in table_rows if r > header_row):
            cells = table_rows[row]
            parts_for_line: list[str] = []
            pending_mappings: list[tuple[int, Any, str]] = []
            for col in sorted(cells):
                header = headers.get(col)
                value = cells[col].text.strip()
                if not header or not value:
                    continue
                prefix = f"{header}: "
                value_start = sum(len(part) for part in parts_for_line) + len(prefix)
                parts_for_line.append(prefix + value)
                if header in PRIVACY_HEADER_HINTS or any(hint in header for hint in PRIVACY_HEADER_HINTS) or _header_type(header) is not None:
                    pending_mappings.append((value_start, cells[col], value, header))
                parts_for_line.append(" | ")
            if parts_for_line:
                if parts_for_line[-1] == " | ":
                    parts_for_line.pop()
                line = "".join(parts_for_line)
                for value_start, cell, value, header in pending_mappings:
                    context_start = context_pos + value_start
                    mappings.append(
                        ContextValueMap(
                            context_start=context_start,
                            context_end=context_start + len(value),
                            original_start=cell.start_offset,
                            original_end=cell.end_offset,
                            original_text=cell.text,
                            header=header,
                        )
                    )
                lines.append(line)
                context_pos += len(line) + 1

    return "\n".join(lines), mappings


def _find_header_row_generic(table_rows: dict[int, dict[int, Any]]) -> int | None:
    for row in sorted(table_rows):
        values = [cell.text.strip() for cell in table_rows[row].values() if getattr(cell, "text", "").strip()]
        if len(values) >= 2 and _has_privacy_header(values):
            return row
    return None


def _column_number(column: str) -> int:
    number = 0
    for char in column:
        number = number * 26 + (ord(char.upper()) - ord("A") + 1)
    return number
