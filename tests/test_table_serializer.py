"""Tests for the unified table serializer.

Covers XLSX (``part:A1`` locations) and DOCX (``part:tbl0.r1.c2`` locations)
through the same :func:`serialize_tables` entry point, plus the
header-gating rule (non-privacy tables are skipped) and offset round-tripping.
"""

from vibemask.core.processor import TextSegment
from vibemask.core.span import EntityType, SourceType, Span
from vibemask.core.table_serializer import (
    ContextValueMap,
    map_context_spans,
    serialize_tables,
)


# ---------------------------------------------------------------------------
# XLSX locations: "part:A1"
# ---------------------------------------------------------------------------

def test_serialize_xlsx_builds_header_value_rows():
    segments = [
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("手机号", "xl/worksheets/sheet1.xml:B1", 3, 6),
        TextSegment("杨嘉明", "xl/worksheets/sheet1.xml:A2", 7, 10),
        TextSegment("13812345678", "xl/worksheets/sheet1.xml:B2", 11, 22),
    ]

    context_text, mappings = serialize_tables(segments)

    assert context_text == "姓名: 杨嘉明 | 手机号: 13812345678"
    # One mapping per privacy-column value (姓名 + 手机号).
    assert len(mappings) == 2
    by_header = {m.header: m for m in mappings}
    # 姓名 value "杨嘉明" lives at original offsets 7:10.
    assert (by_header["姓名"].original_start, by_header["姓名"].original_end) == (7, 10)
    assert by_header["姓名"].original_text == "杨嘉明"
    # 手机号 value at 11:22.
    assert (by_header["手机号"].original_start, by_header["手机号"].original_end) == (11, 22)


def test_serialize_xlsx_skips_non_privacy_tables():
    # A financial table: headers like 序号/金额/单位 are not privacy headers.
    segments = [
        TextSegment("序号", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("金额", "xl/worksheets/sheet1.xml:B1", 3, 5),
        TextSegment("1", "xl/worksheets/sheet1.xml:A2", 6, 7),
        TextSegment("万元", "xl/worksheets/sheet1.xml:B2", 8, 10),
    ]

    context_text, mappings = serialize_tables(segments)

    assert context_text == ""
    assert mappings == []


def test_serialize_xlsx_multiple_rows_and_context_positions():
    # Two columns so the table clears the >= 2 header-row gate.
    segments = [
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("专业", "xl/worksheets/sheet1.xml:B1", 3, 5),
        TextSegment("刘重肖", "xl/worksheets/sheet1.xml:A2", 6, 9),
        TextSegment("机器人", "xl/worksheets/sheet1.xml:B2", 10, 13),
        TextSegment("杨嘉明", "xl/worksheets/sheet1.xml:A3", 14, 17),
        TextSegment("自动化", "xl/worksheets/sheet1.xml:B3", 18, 21),
    ]

    context_text, mappings = serialize_tables(segments)

    # Two data rows, joined by newline. Only 姓名 is a privacy column, so the
    # 专业 value carries the header prefix but is not mapped.
    assert context_text == "姓名: 刘重肖 | 专业: 机器人\n姓名: 杨嘉明 | 专业: 自动化"
    # One mapping per privacy-column value (姓名 only), across two rows.
    assert len(mappings) == 2
    assert all(m.header == "姓名" for m in mappings)
    # First-row mapping context position points into the first line.
    assert mappings[0].context_start == len("姓名: ")
    assert mappings[0].context_end == mappings[0].context_start + len("刘重肖")
    # Second-row mapping context position accounts for the first line + newline.
    first_line_len = len("姓名: 刘重肖 | 专业: 机器人")
    assert mappings[1].context_start == first_line_len + 1 + len("姓名: ")


# ---------------------------------------------------------------------------
# DOCX locations: "part:tbl0.r1.c2"
# ---------------------------------------------------------------------------

def test_serialize_docx_builds_header_value_rows():
    segments = [
        TextSegment("姓名", "word/document.xml:tbl0.r0.c0", 0, 2),
        TextSegment("身份证号", "word/document.xml:tbl0.r0.c1", 3, 7),
        TextSegment("王晓明", "word/document.xml:tbl0.r1.c0", 8, 11),
        TextSegment("110101198801011234", "word/document.xml:tbl0.r1.c1", 12, 30),
    ]

    context_text, mappings = serialize_tables(segments)

    assert context_text == "姓名: 王晓明 | 身份证号: 110101198801011234"
    assert len(mappings) == 2
    by_header = {m.header: m for m in mappings}
    assert (by_header["姓名"].original_start, by_header["姓名"].original_end) == (8, 11)
    assert (by_header["身份证号"].original_start, by_header["身份证号"].original_end) == (12, 30)


def test_serialize_docx_skips_non_privacy_tables():
    segments = [
        TextSegment("指标", "word/document.xml:tbl0.r0.c0", 0, 2),
        TextSegment("数值", "word/document.xml:tbl0.r0.c1", 3, 5),
        TextSegment("营业收入", "word/document.xml:tbl0.r1.c0", 6, 10),
        TextSegment("500", "word/document.xml:tbl0.r1.c1", 11, 14),
    ]

    context_text, mappings = serialize_tables(segments)

    assert context_text == ""
    assert mappings == []


def test_serialize_docx_handles_multiple_tables():
    # Two DOCX tables in one document; both are privacy tables (>= 2 columns).
    segments = [
        # Table 0
        TextSegment("姓名", "word/document.xml:tbl0.r0.c0", 0, 2),
        TextSegment("手机号", "word/document.xml:tbl0.r0.c1", 3, 6),
        TextSegment("张三", "word/document.xml:tbl0.r1.c0", 7, 9),
        TextSegment("13812345678", "word/document.xml:tbl0.r1.c1", 10, 21),
        # Table 1
        TextSegment("联系人", "word/document.xml:tbl1.r0.c0", 22, 25),
        TextSegment("邮箱", "word/document.xml:tbl1.r0.c1", 26, 28),
        TextSegment("李四", "word/document.xml:tbl1.r1.c0", 29, 31),
        TextSegment("lisi@example.com", "word/document.xml:tbl1.r1.c1", 32, 48),
    ]

    context_text, mappings = serialize_tables(segments)

    # Tables serialized in (part, tbl_id) order; both have a privacy header.
    assert context_text == (
        "姓名: 张三 | 手机号: 13812345678\n联系人: 李四 | 邮箱: lisi@example.com"
    )
    assert len(mappings) == 4


# ---------------------------------------------------------------------------
# Mixed / edge cases
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Legacy .xls locations: "sheet0:row1:col2"
# ---------------------------------------------------------------------------

def test_serialize_legacy_xls_builds_header_value_rows():
    segments = [
        TextSegment("序号", "sheet0:row0:col0", 0, 2),
        TextSegment("姓名", "sheet0:row0:col1", 3, 5),
        TextSegment("学号", "sheet0:row0:col2", 6, 8),
        TextSegment("1", "sheet0:row1:col0", 9, 10),
        TextSegment("赵铁柱", "sheet0:row1:col1", 11, 14),
        TextSegment("20210101001", "sheet0:row1:col2", 15, 26),
    ]

    context_text, mappings = serialize_tables(segments)

    # 序号 is not a privacy column, so it carries the prefix but is unmapped.
    assert context_text == "序号: 1 | 姓名: 赵铁柱 | 学号: 20210101001"
    by_header = {m.header: m for m in mappings}
    assert (by_header["姓名"].original_start, by_header["姓名"].original_end) == (11, 14)
    assert (by_header["学号"].original_start, by_header["学号"].original_end) == (15, 26)


def test_serialize_legacy_xls_skips_non_privacy_tables():
    segments = [
        TextSegment("指标", "sheet0:row0:col0", 0, 2),
        TextSegment("数值", "sheet0:row0:col1", 3, 5),
        TextSegment("营业收入", "sheet0:row1:col0", 6, 10),
        TextSegment("500", "sheet0:row1:col1", 11, 14),
    ]

    context_text, mappings = serialize_tables(segments)
    assert context_text == ""
    assert mappings == []


# ---------------------------------------------------------------------------
# Mixed / edge cases (xlsx + docx)
# ---------------------------------------------------------------------------

def test_serialize_mixed_xlsx_and_docx_segments():
    # A processor would never emit both grammars, but the serializer must not
    # conflate them: each location matches exactly one parser.
    segments = [
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("手机号", "xl/worksheets/sheet1.xml:B1", 3, 6),
        TextSegment("张三", "xl/worksheets/sheet1.xml:A2", 7, 9),
        TextSegment("13812345678", "xl/worksheets/sheet1.xml:B2", 10, 21),
        TextSegment("姓名", "word/document.xml:tbl0.r0.c0", 22, 24),
        TextSegment("手机号", "word/document.xml:tbl0.r0.c1", 25, 28),
        TextSegment("李四", "word/document.xml:tbl0.r1.c0", 29, 31),
        TextSegment("13987654321", "word/document.xml:tbl0.r1.c1", 32, 43),
    ]

    context_text, mappings = serialize_tables(segments)

    # Tables are sorted by (part, table_id). The DOCX table_id "tbl0" sorts
    # before the XLSX table_id (== the sheet part "xl/worksheets/sheet1.xml"),
    # so the DOCX table is emitted first. Both orders are deterministic.
    assert context_text == (
        "姓名: 李四 | 手机号: 13987654321\n姓名: 张三 | 手机号: 13812345678"
    )
    assert len(mappings) == 4


def test_serialize_empty_segments():
    context_text, mappings = serialize_tables([])
    assert context_text == ""
    assert mappings == []


def test_serialize_row_without_header_row_returns_empty():
    # Privacy values present but no recognizable header row above them.
    segments = [
        TextSegment("张三", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("13812345678", "xl/worksheets/sheet1.xml:B1", 3, 14),
    ]
    context_text, mappings = serialize_tables(segments)
    assert context_text == ""
    assert mappings == []


def test_serialize_skips_empty_value_cells():
    segments = [
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("手机号", "xl/worksheets/sheet1.xml:B1", 3, 6),
        TextSegment("张三", "xl/worksheets/sheet1.xml:A2", 7, 9),
        TextSegment("", "xl/worksheets/sheet1.xml:B2", 10, 10),  # empty phone
    ]

    context_text, mappings = serialize_tables(segments)

    # The 姓名 value is emitted; the empty 手机号 column contributes nothing.
    assert context_text == "姓名: 张三"
    assert len(mappings) == 1
    assert mappings[0].header == "姓名"


def test_columns_ordered_left_to_right_for_xlsx():
    # Columns out of insertion order; serializer must sort A before B.
    segments = [
        TextSegment("手机号", "xl/worksheets/sheet1.xml:B1", 0, 3),
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 4, 6),
        TextSegment("13812345678", "xl/worksheets/sheet1.xml:B2", 7, 18),
        TextSegment("张三", "xl/worksheets/sheet1.xml:A2", 19, 21),
    ]

    context_text, _ = serialize_tables(segments)

    # 姓名 (col A) precedes 手机号 (col B) regardless of segment order.
    assert context_text == "姓名: 张三 | 手机号: 13812345678"


# ---------------------------------------------------------------------------
# map_context_spans — projecting model spans back to original offsets
# ---------------------------------------------------------------------------

def test_map_context_spans_projects_to_original_offsets():
    segments = [
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("手机号", "xl/worksheets/sheet1.xml:B1", 3, 6),
        TextSegment("杨嘉明", "xl/worksheets/sheet1.xml:A2", 7, 10),
        TextSegment("13812345678", "xl/worksheets/sheet1.xml:B2", 11, 22),
    ]
    context_text, mappings = serialize_tables(segments)

    # Simulate the model flagging "杨嘉明" in the serialized context.
    start = context_text.index("杨嘉明")
    detected = [
        Span(
            start=start,
            end=start + len("杨嘉明"),
            text="杨嘉明",
            type=EntityType.PERSON,
            source=SourceType.PRIVACY_FILTER,
            confidence=1.0,
            reason="privacy_filter:private_person",
        )
    ]

    mapped = map_context_spans(detected, mappings)

    assert len(mapped) == 1
    assert (mapped[0].start, mapped[0].end, mapped[0].text) == (7, 10, "杨嘉明")
    assert mapped[0].type == EntityType.PERSON


def test_map_context_spans_drops_header_word():
    segments = [
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("手机号", "xl/worksheets/sheet1.xml:B1", 3, 6),
        TextSegment("杨嘉明", "xl/worksheets/sheet1.xml:A2", 7, 10),
        TextSegment("13812345678", "xl/worksheets/sheet1.xml:B2", 11, 22),
    ]
    context_text, mappings = serialize_tables(segments)

    # Model wrongly flags the header word "姓名" — it has no value mapping.
    start = context_text.index("姓名")
    detected = [
        Span(
            start=start,
            end=start + len("姓名"),
            text="姓名",
            type=EntityType.PERSON,
            source=SourceType.PRIVACY_FILTER,
            confidence=1.0,
            reason="privacy_filter:private_person",
        )
    ]

    assert map_context_spans(detected, mappings) == []
