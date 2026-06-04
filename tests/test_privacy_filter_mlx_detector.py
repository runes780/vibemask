import pytest


def test_privacy_filter_mlx_merges_bioes_labels_and_trims():
    from vibemask.core.span import EntityType, SourceType
    from vibemask.detector.privacy_filter_mlx import _spans_from_token_labels

    text = "姓名: 杨嘉明 电话: 18606518817"
    labels = [
        "O",
        "O",
        "B-private_person",
        "E-private_person",
        "O",
        "O",
        "S-private_phone",
    ]
    offsets = [
        (0, 2),
        (2, 4),
        (4, 6),
        (6, 8),
        (8, 9),
        (9, 13),
        (12, len(text)),
    ]

    spans = _spans_from_token_labels(text, labels, offsets)

    assert [(span.text, span.type, span.source) for span in spans] == [
        ("杨嘉明", EntityType.PERSON, SourceType.PRIVACY_FILTER),
        ("18606518817", EntityType.PHONE, SourceType.PRIVACY_FILTER),
    ]
    assert spans[0].reason == "privacy_filter_mlx:private_person"


def test_privacy_filter_mlx_reports_missing_dependency(monkeypatch):
    import vibemask.detector.privacy_filter_mlx as privacy_filter_mlx

    def fake_import_module(name):
        if name == "mlx.core":
            raise ImportError(name)
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(privacy_filter_mlx, "import_module", fake_import_module)

    detector = privacy_filter_mlx.PrivacyFilterMLXDetector()

    with pytest.raises(RuntimeError, match=r"vibemask\[privacy-filter-mlx\]"):
        detector.detect("Alice Smith")
