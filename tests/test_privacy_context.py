from types import SimpleNamespace

from vibemask.core.processor import TextSegment
from vibemask.core.span import EntityType, SourceType, Span


def test_xlsx_row_context_maps_privacy_filter_span_back_to_original_cell():
    from vibemask.detector.privacy_context import detect_xlsx_row_context

    segments = [
        TextSegment("序号", "xl/worksheets/sheet1.xml:A2", 0, 2),
        TextSegment("姓名", "xl/worksheets/sheet1.xml:B2", 3, 5),
        TextSegment("专业", "xl/worksheets/sheet1.xml:C2", 6, 8),
        TextSegment("1", "xl/worksheets/sheet1.xml:A3", 9, 10),
        TextSegment("杨嘉明", "xl/worksheets/sheet1.xml:B3", 11, 14),
        TextSegment("机器人与自动化系统", "xl/worksheets/sheet1.xml:C3", 15, 24),
    ]
    processor = SimpleNamespace(file_path=SimpleNamespace(suffix=".xlsx"), _segments=segments)

    class FakeDetector:
        def detect(self, context_text):
            assert "姓名: 杨嘉明" in context_text
            start = context_text.index("杨嘉明")
            return [
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

    spans = detect_xlsx_row_context(processor, FakeDetector())

    # The model span is mapped back to the original cell offset (11:14)...
    model_spans = [s for s in spans if s.source == SourceType.PRIVACY_FILTER]
    assert len(model_spans) == 1
    assert (model_spans[0].start, model_spans[0].end, model_spans[0].text) == (11, 14, "杨嘉明")
    # ...and a structural span is also emitted (column 姓名 -> PERSON), filling
    # any model recall gap. Both share the value's offset.
    assert any(
        s.source == SourceType.SCHEMA and s.text == "杨嘉明" and s.type == EntityType.PERSON
        for s in spans
    )


def test_xlsx_row_context_does_not_emit_header_spans():
    from vibemask.detector.privacy_context import detect_xlsx_row_context

    segments = [
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("专业", "xl/worksheets/sheet1.xml:B1", 3, 5),
    ]
    processor = SimpleNamespace(file_path=SimpleNamespace(suffix=".xlsx"), _segments=segments)

    class FakeDetector:
        def detect(self, context_text):
            return [
                Span(
                    start=context_text.index("姓名"),
                    end=context_text.index("姓名") + len("姓名"),
                    text="姓名",
                    type=EntityType.PERSON,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter:private_person",
                )
            ]

    assert detect_xlsx_row_context(processor, FakeDetector()) == []


def test_xlsx_row_context_maps_multiple_rows():
    from vibemask.detector.privacy_context import detect_xlsx_row_context

    segments = [
        TextSegment("姓名", "xl/worksheets/sheet1.xml:A1", 0, 2),
        TextSegment("专业", "xl/worksheets/sheet1.xml:B1", 3, 5),
        TextSegment("杨嘉明", "xl/worksheets/sheet1.xml:A2", 6, 9),
        TextSegment("机器人", "xl/worksheets/sheet1.xml:B2", 10, 13),
        TextSegment("刘重肖", "xl/worksheets/sheet1.xml:A3", 14, 17),
        TextSegment("自动化", "xl/worksheets/sheet1.xml:B3", 18, 21),
    ]
    processor = SimpleNamespace(file_path=SimpleNamespace(suffix=".xlsx"), _segments=segments)

    class FakeDetector:
        def detect(self, context_text):
            spans = []
            for value in ("杨嘉明", "刘重肖"):
                start = context_text.index(value)
                spans.append(
                    Span(
                        start=start,
                        end=start + len(value),
                        text=value,
                        type=EntityType.PERSON,
                        source=SourceType.PRIVACY_FILTER,
                        confidence=1.0,
                        reason="privacy_filter:private_person",
                    )
                )
            return spans

    spans = detect_xlsx_row_context(processor, FakeDetector())

    # Model-mapped spans + structural spans are both emitted (the structural
    # ones dedup with the model ones downstream in merge_spans). Check that both
    # values are covered at their original offsets, deduplicated by position.
    got = {(span.start, span.end, span.text) for span in spans}
    assert got == {(6, 9, "杨嘉明"), (14, 17, "刘重肖")}
