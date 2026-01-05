import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner


# Ensure the repository root is on sys.path when pytest uses importlib mode.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _docx_available() -> bool:
    try:
        import docx  # noqa: F401
        return True
    except Exception:
        return False


def test_cli_mask_requires_libreoffice_for_legacy_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vibemask.cli import app
    import vibemask.core.converter as converter

    legacy_path = tmp_path / "legacy.doc"
    legacy_path.write_bytes(b"dummy")

    def _raise(*args, **kwargs):
        raise RuntimeError("LibreOffice is not installed")

    monkeypatch.setattr(converter, "convert_to_ooxml", _raise)

    runner = CliRunner()
    result = runner.invoke(app, ["mask", str(legacy_path)])
    assert result.exit_code != 0
    assert "LibreOffice conversion required" in result.output


def test_cli_restore_requires_libreoffice_for_legacy_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vibemask.cli import app
    import vibemask.core.converter as converter

    legacy_path = tmp_path / "legacy.xls"
    legacy_path.write_bytes(b"dummy")

    def _raise(*args, **kwargs):
        raise RuntimeError("LibreOffice is not installed")

    monkeypatch.setattr(converter, "convert_to_ooxml", _raise)

    runner = CliRunner()
    result = runner.invoke(app, ["restore", str(legacy_path)])
    assert result.exit_code != 0
    assert "LibreOffice conversion required" in result.output


@pytest.mark.skipif(not _docx_available(), reason="python-docx not installed")
def test_cli_mask_no_convert_legacy_skips_libreoffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vibemask.cli import app
    import vibemask.core.converter as converter
    import vibemask.detector.llm_scanner as llm_scanner

    # Create a valid DOCX file but give it a .doc extension so the legacy processor can read it.
    from docx import Document

    doc = Document()
    doc.add_paragraph("hello 张三")
    tmp_docx = tmp_path / "tmp.docx"
    doc.save(tmp_docx)

    legacy_path = tmp_path / "legacy.doc"
    legacy_path.write_bytes(tmp_docx.read_bytes())

    calls = {"count": 0}

    def _convert(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("should not be called")

    monkeypatch.setattr(converter, "convert_to_ooxml", _convert)

    class _DummyLLMResult:
        pii_types = {}

    def _dummy_scan_and_rewrite(self, text: str, stream: bool = False):
        return _DummyLLMResult()

    monkeypatch.setattr(llm_scanner.LLMScanner, "scan_and_rewrite", _dummy_scan_and_rewrite)

    runner = CliRunner()
    result = runner.invoke(app, ["mask", str(legacy_path), "--no-convert-legacy"])
    assert result.exit_code == 0
    assert calls["count"] == 0

