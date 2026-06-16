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


def _header_type(header: str) -> str | None:
    h = header.strip()
    hl = h.lower()
    for keyword, etype in HEADER_TYPE_MAP.items():
        if keyword in h or keyword in hl:
            return etype
    return None


def _structural_spans_from_mappings(mappings: list[ContextValueMap]) -> list[Span]:
    """One span per labelled-column value — typed by its header. These fill the
    recall gap where the model fails to flag a value the column header already
    declares as PII (e.g. every cell under 学号)."""
    spans: list[Span] = []
    for m in mappings:
        etype = _header_type(m.header)
        if etype is None or not m.original_text.strip():
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
            col: cell.text.strip()
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
                if header in PRIVACY_HEADER_HINTS or any(hint in header for hint in PRIVACY_HEADER_HINTS):
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
    return any(value in PRIVACY_HEADER_HINTS or any(hint in value for hint in PRIVACY_HEADER_HINTS) for value in values)


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
            col: cell.text.strip()
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
                if header in PRIVACY_HEADER_HINTS or any(hint in header for hint in PRIVACY_HEADER_HINTS):
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
