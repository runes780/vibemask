import sys
import types
from types import SimpleNamespace

import pytest


def test_privacy_filter_detector_maps_native_spans(monkeypatch):
    from vibemask.core.span import EntityType, SourceType

    text = (
        "Alice Smith lives at 12 3rd St. "
        "Email alice@example.com, phone 123 456 7890, "
        "born 1990-01-02, account 4111111111111111, secret sk-test, "
        "site https://example.com/alice, unknown token."
    )

    labels = [
        ("private_person", "Alice Smith"),
        ("private_address", "12 3rd St"),
        ("private_email", "alice@example.com"),
        ("private_phone", "123 456 7890"),
        ("private_date", "1990-01-02"),
        ("account_number", "4111111111111111"),
        ("secret", "sk-test"),
        ("private_url", "https://example.com/alice"),
        ("future_label", "unknown token"),
    ]

    detected_spans = []
    for label, value in labels:
        start = text.index(value)
        detected_spans.append(
            SimpleNamespace(label=label, start=start, end=start + len(value), text=value)
        )

    class FakeOPF:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def redact(self, input_text):
            assert input_text == text
            return SimpleNamespace(
                text=input_text,
                detected_spans=tuple(detected_spans),
                warning="tokenizer warning",
            )

    monkeypatch.setitem(sys.modules, "opf", types.SimpleNamespace(OPF=FakeOPF))

    from vibemask.detector.privacy_filter import PrivacyFilterDetector

    detector = PrivacyFilterDetector(
        device="cpu",
        checkpoint="/tmp/checkpoint",
        context_window_length=128,
        decode_mode="argmax",
    )
    spans = detector.detect(text)

    assert [span.type for span in spans] == [
        EntityType.PERSON,
        EntityType.ADDRESS,
        EntityType.EMAIL,
        EntityType.PHONE,
        EntityType.DATE_TIME,
        EntityType.ACCOUNT_NUMBER,
        EntityType.SECRET,
        EntityType.URL,
        EntityType.UNKNOWN,
    ]
    assert all(span.source == SourceType.PRIVACY_FILTER for span in spans)
    assert spans[-1].reason == "privacy_filter:future_label"
    assert spans[0].text == "Alice Smith"


def test_privacy_filter_detector_reports_missing_dependency(monkeypatch):
    import vibemask.detector.privacy_filter as privacy_filter

    def fake_import_module(name):
        if name == "opf":
            raise ImportError(name)
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(privacy_filter, "import_module", fake_import_module)

    from vibemask.detector.privacy_filter import PrivacyFilterDetector

    detector = PrivacyFilterDetector(device="cpu")

    with pytest.raises(RuntimeError, match=r"vibemask\[privacy-filter\]"):
        detector.detect("Alice Smith")
