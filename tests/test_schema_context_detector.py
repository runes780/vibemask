from vibemask.core.span import EntityType, SourceType


def test_schema_context_detects_chinese_field_values():
    from vibemask.detector.schema_context import detect_schema_context

    text = (
        "申报人：何黎明\n"
        "联系电话：18606518817\n"
        "学号：202401230045\n"
        "API Key: sk-live-test-token\n"
    )

    spans = detect_schema_context(text)
    by_text = {span.text: span for span in spans}

    assert by_text["何黎明"].type == EntityType.PERSON
    assert by_text["何黎明"].source == SourceType.SCHEMA
    assert by_text["202401230045"].type == EntityType.ACCOUNT_NUMBER
    assert by_text["sk-live-test-token"].type == EntityType.SECRET


def test_schema_context_detects_values_in_following_cells():
    from vibemask.detector.schema_context import detect_schema_context

    text = "姓名\n何黎明\n学号\n202401230045\nToken\nsk-live-test-token\n"

    spans = detect_schema_context(text)
    by_text = {span.text: span for span in spans}

    assert by_text["何黎明"].type == EntityType.PERSON
    assert by_text["202401230045"].type == EntityType.ACCOUNT_NUMBER
    assert by_text["sk-live-test-token"].type == EntityType.SECRET


def test_schema_context_does_not_treat_public_unit_as_person():
    from vibemask.detector.schema_context import detect_schema_context

    spans = detect_schema_context("申报单位：杭州市教育局\n")

    assert "杭州市教育局" not in {span.text for span in spans}


def test_schema_context_does_not_treat_table_headers_as_person_values():
    from vibemask.detector.schema_context import detect_schema_context

    spans = detect_schema_context("姓名\n专业\n个人主页\n手机号码\n")

    detected = {span.text for span in spans}
    assert "专业" not in detected
    assert "个人主页" not in detected
    assert "手机号码" not in detected
