from vibemask.core.span import EntityType, SourceType, Span


def test_refines_private_date_span_that_contains_school_history():
    from vibemask.detector.privacy_postprocess import refine_privacy_filter_spans

    text = "1987.09-1990.07\n浙江幼儿师范学校学前教育专业学习并毕业\n"
    original_span = Span(
        start=text.index("浙江幼儿师范学校"),
        end=text.index("毕业") + len("毕业"),
        text="浙江幼儿师范学校学前教育专业学习并毕业",
        type=EntityType.DATE_TIME,
        source=SourceType.PRIVACY_FILTER,
        confidence=1.0,
        reason="privacy_filter:private_date",
    )

    refined = refine_privacy_filter_spans([original_span], text)

    assert [(span.text, span.type, span.reason) for span in refined] == [
        ("浙江幼儿师范学校", EntityType.ORG, "privacy_filter:private_date_refined_org")
    ]


def test_keeps_regular_private_date_span():
    from vibemask.detector.privacy_postprocess import refine_privacy_filter_spans

    text = "出生年月：1971.11"
    original_span = Span(
        start=text.index("1971.11"),
        end=text.index("1971.11") + len("1971.11"),
        text="1971.11",
        type=EntityType.DATE_TIME,
        source=SourceType.PRIVACY_FILTER,
        confidence=1.0,
        reason="privacy_filter:private_date",
    )

    refined = refine_privacy_filter_spans([original_span], text)

    assert refined == [original_span]
