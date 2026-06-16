from types import SimpleNamespace


def test_hybrid_detector_caches_mlx_privacy_detector(monkeypatch):
    import vibemask.detector.hybrid as hybrid

    created = []

    class DummyMLXDetector:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def detect(self, text):
            return []

    def fake_import_module(name):
        if name == "vibemask.detector.privacy_filter_mlx":
            return SimpleNamespace(PrivacyFilterMLXDetector=DummyMLXDetector)
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(hybrid, "import_module", fake_import_module)

    detector = hybrid.HybridDetector(
        privacy_backend="mlx",
        privacy_checkpoint="/tmp/mlx-model",
        regex_enabled=False,
        schema_enabled=False,
        chinese_names_enabled=False,
    )

    detector.detect("Alice")
    detector.detect("Bob")

    assert created == [{"checkpoint": "/tmp/mlx-model", "decode_mode": "viterbi"}]
