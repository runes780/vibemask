from pathlib import Path

from typer.testing import CliRunner


def test_cli_mask_uses_hybrid_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    from vibemask.core.span import EntityType, SourceType, Span
    import vibemask.detector.hybrid as hybrid

    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "masked.txt"
    input_path.write_text("Contact Alice Smith at 123 456 7890.", encoding="utf-8")

    calls = []

    class DummyHybridDetector:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def detect_document(self, processor, text):
            assert processor.__class__.__name__ == "PlainTextProcessor"
            return [
                Span(
                    start=text.index("Alice Smith"),
                    end=text.index("Alice Smith") + len("Alice Smith"),
                    text="Alice Smith",
                    type=EntityType.PERSON,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter:private_person",
                ),
                Span(
                    start=text.index("123 456 7890"),
                    end=text.index("123 456 7890") + len("123 456 7890"),
                    text="123 456 7890",
                    type=EntityType.PHONE,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter:private_phone",
                ),
            ]

    monkeypatch.setattr(hybrid, "HybridDetector", DummyHybridDetector)

    from vibemask import cli
    from vibemask.vault.storage import VaultStorage

    cli.mask(file=input_path, output=output_path)

    masked_text = output_path.read_text(encoding="utf-8")
    assert "Alice Smith" not in masked_text
    assert "123 456 7890" not in masked_text
    assert calls == [
        {
            "privacy_device": "cpu",
            "privacy_checkpoint": None,
            "privacy_context_window": None,
            "privacy_decode_mode": "viterbi",
            "privacy_backend": "mlx",
        }
    ]

    sessions = VaultStorage(str(tmp_path)).list_sessions(limit=1)
    assert sessions
    assert sessions[0].stats["PERSON"] == 1
    assert sessions[0].stats["PHONE"] == 1


def test_cli_mask_uses_privacy_filter_when_explicit(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    from vibemask.core.span import EntityType, SourceType, Span
    import vibemask.detector.privacy_filter as privacy_filter

    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "masked.txt"
    input_path.write_text("Contact Alice Smith.", encoding="utf-8")

    calls = []

    class DummyPrivacyFilterDetector:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def detect(self, text):
            return [
                Span(
                    start=text.index("Alice Smith"),
                    end=text.index("Alice Smith") + len("Alice Smith"),
                    text="Alice Smith",
                    type=EntityType.PERSON,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter:private_person",
                )
            ]

    monkeypatch.setattr(privacy_filter, "PrivacyFilterDetector", DummyPrivacyFilterDetector)

    from vibemask import cli

    cli.mask(file=input_path, output=output_path, engine="privacy-filter")

    assert "Alice Smith" not in output_path.read_text(encoding="utf-8")
    assert calls == [
        {
            "device": "cpu",
            "checkpoint": None,
            "context_window_length": None,
            "decode_mode": "viterbi",
        }
    ]


def test_cli_mask_uses_privacy_filter_mlx_when_explicit(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    from vibemask.core.span import EntityType, SourceType, Span
    import vibemask.detector.privacy_filter_mlx as privacy_filter_mlx

    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "masked.txt"
    input_path.write_text("Contact Alice Smith.", encoding="utf-8")

    calls = []

    class DummyPrivacyFilterMLXDetector:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def detect(self, text):
            return [
                Span(
                    start=text.index("Alice Smith"),
                    end=text.index("Alice Smith") + len("Alice Smith"),
                    text="Alice Smith",
                    type=EntityType.PERSON,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter_mlx:private_person",
                )
            ]

    monkeypatch.setattr(
        privacy_filter_mlx, "PrivacyFilterMLXDetector", DummyPrivacyFilterMLXDetector
    )

    from vibemask import cli

    cli.mask(file=input_path, output=output_path, engine="privacy-filter-mlx")

    assert "Alice Smith" not in output_path.read_text(encoding="utf-8")
    assert calls == [{"checkpoint": None}]


def test_cli_hybrid_accepts_mlx_backend(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    import vibemask.detector.hybrid as hybrid

    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "masked.txt"
    input_path.write_text("No secrets here.", encoding="utf-8")

    calls = []

    class DummyHybridDetector:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def detect_document(self, processor, text):
            return []

    monkeypatch.setattr(hybrid, "HybridDetector", DummyHybridDetector)

    from vibemask import cli

    cli.mask(file=input_path, output=output_path, privacy_backend="mlx")

    assert calls == [
        {
            "privacy_device": "cpu",
            "privacy_checkpoint": None,
            "privacy_context_window": None,
            "privacy_decode_mode": "viterbi",
            "privacy_backend": "mlx",
        }
    ]


def test_cli_mask_defaults_project_to_input_file_folder(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    from vibemask.core.span import EntityType, SourceType, Span
    import vibemask.detector.hybrid as hybrid
    from vibemask.vault.storage import VaultStorage

    input_dir = tmp_path / "project-a"
    input_dir.mkdir()
    input_path = input_dir / "input.txt"
    output_path = input_dir / "masked.txt"
    input_path.write_text("Contact Alice Smith.", encoding="utf-8")

    class DummyHybridDetector:
        def __init__(self, **kwargs):
            pass

        def detect_document(self, processor, text):
            return [
                Span(
                    start=text.index("Alice Smith"),
                    end=text.index("Alice Smith") + len("Alice Smith"),
                    text="Alice Smith",
                    type=EntityType.PERSON,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter_mlx:private_person",
                )
            ]

    monkeypatch.setattr(hybrid, "HybridDetector", DummyHybridDetector)

    from vibemask import cli

    cli.mask(file=input_path, output=output_path)

    assert VaultStorage(str(input_dir)).list_sessions(limit=1)
    assert not VaultStorage(str(Path.cwd())).list_sessions(limit=1)


def test_cli_unknown_engine_lists_privacy_filter(tmp_path: Path):
    from vibemask.cli import app

    input_path = tmp_path / "input.txt"
    input_path.write_text("hello", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["mask", str(input_path), "--engine", "bogus"])

    assert result.exit_code != 0
    normalized_output = " ".join(result.output.split())
    assert "hybrid | privacy-filter-mlx | privacy-filter | regex | qwen | presidio" in normalized_output
