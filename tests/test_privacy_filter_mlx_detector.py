import pytest
from types import SimpleNamespace


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


def test_privacy_filter_mlx_oom_downgrade_covers_every_token(monkeypatch):
    import vibemask.detector.privacy_filter_mlx as privacy_filter_mlx

    token_count = 6000
    text = "x" * token_count

    def tokenizer(value, return_offsets_mapping):
        return {
            "input_ids": list(range(token_count)),
            "offset_mapping": [(i, i + 1) for i in range(token_count)],
        }

    model = SimpleNamespace(config=SimpleNamespace(id2label={0: "O"}))
    detector = privacy_filter_mlx.PrivacyFilterMLXDetector(max_tokens=4096)
    monkeypatch.setattr(detector, "_get_model", lambda: (model, tokenizer, object()))
    successful_chunks: list[list[int]] = []
    first_call = True

    def infer_with_one_oom(mx, current_model, id2label, chunk_ids):
        nonlocal first_call
        if first_call:
            first_call = False
            raise RuntimeError("Metal out of memory")
        successful_chunks.append(list(chunk_ids))
        return ["O"] * len(chunk_ids)

    monkeypatch.setattr(detector, "_infer_labels", infer_with_one_oom)

    assert detector.detect(text) == []
    covered = {token for chunk in successful_chunks for token in chunk}
    assert covered == set(range(token_count))
