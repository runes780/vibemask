import sys
import types


def test_hybrid_detector_merges_regex_schema_privacy_and_chinese_names(monkeypatch):
    from vibemask.core.span import EntityType, SourceType, Span

    text = (
        "申报人：何黎明\n"
        "电话：18606518817\n"
        "个人主页：https://person.zju.edu.cn/zhaojunbo\n"
        "Alice Smith lives at 12 3rd St.\n"
    )

    class FakePrivacyFilterDetector:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def detect(self, input_text):
            assert input_text == text
            start = input_text.index("Alice Smith")
            address_start = input_text.index("12 3rd St")
            return [
                Span(
                    start=start,
                    end=start + len("Alice Smith"),
                    text="Alice Smith",
                    type=EntityType.PERSON,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter:private_person",
                ),
                Span(
                    start=address_start,
                    end=address_start + len("12 3rd St"),
                    text="12 3rd St",
                    type=EntityType.ADDRESS,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter:private_address",
                ),
            ]

    fake_module = types.SimpleNamespace(PrivacyFilterDetector=FakePrivacyFilterDetector)
    monkeypatch.setitem(sys.modules, "vibemask.detector.privacy_filter", fake_module)

    from vibemask.detector.hybrid import HybridDetector

    spans = HybridDetector(privacy_device="cpu", privacy_backend="opf").detect(text)
    by_text = {span.text: span for span in spans}

    assert by_text["何黎明"].type == EntityType.PERSON
    assert by_text["何黎明"].source == SourceType.SCHEMA
    assert by_text["18606518817"].type == EntityType.PHONE
    assert by_text["18606518817"].source == SourceType.REGEX
    assert by_text["https://person.zju.edu.cn/zhaojunbo"].type == EntityType.URL
    assert by_text["Alice Smith"].source == SourceType.PRIVACY_FILTER
    assert by_text["12 3rd St"].type == EntityType.ADDRESS
