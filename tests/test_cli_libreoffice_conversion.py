import os
import sys
from pathlib import Path

import pytest

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


@pytest.mark.skipif(not _docx_available(), reason="python-docx not installed")
def test_cli_mask_restore_converts_legacy_doc_to_docx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Isolate vault into the test temp HOME so we don't touch real user data.
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_dir))

    from docx import Document

    # Create a dummy legacy .doc file; conversion will be mocked to a real .docx.
    legacy_doc = tmp_path / "input.doc"
    legacy_doc.write_bytes(b"DUMMY")

    template_docx = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("张三")
    doc.save(template_docx)

    # Mock LibreOffice conversion to avoid requiring soffice during tests.
    import vibemask.core.converter as converter

    def fake_convert_to_ooxml(
        input_path: Path,
        output_dir: Path | None = None,
        soffice_path: str | None = None,
    ) -> Path:
        out_dir = Path(output_dir) if output_dir is not None else Path(input_path).parent
        out_path = out_dir / f"{Path(input_path).stem}.docx"
        out_path.write_bytes(template_docx.read_bytes())
        return out_path

    monkeypatch.setattr(converter, "convert_to_ooxml", fake_convert_to_ooxml)

    # Mock Qwen scanner so the CLI doesn't call the network.
    import vibemask.detector.llm_scanner as llm_scanner

    class DummyResult:
        def __init__(self):
            self.pii_types = {"张三": "PERSON"}
            self.rewritten_text = ""

    class DummyScanner:
        def __init__(self, *args, **kwargs):
            pass

        def scan_and_rewrite(self, text: str, stream: bool = False):
            return DummyResult()

    monkeypatch.setattr(llm_scanner, "LLMScanner", DummyScanner)

    from vibemask import cli
    from vibemask.core import factory
    from vibemask.vault.storage import VaultStorage

    # Mask: legacy .doc should be converted and saved as .docx by default.
    cli.mask(
        file=legacy_doc,
        output=None,
        dry_run=False,
        interactive=False,
        language="zh",
        engine="qwen",
        use_ollama=False,
        ollama_model="qwen2.5:0.5b",
        qwen_url="http://localhost:11434",
        qwen_model="qwen3:4b-instruct",
        qwen_stream=False,
        qwen_timeout=1,
        qwen_chunk_chars=256,
        convert_legacy=True,
        allow_lossy=False,
        keep_legacy=False,
        soffice=None,
    )

    masked_path = legacy_doc.with_stem("input_masked").with_suffix(".docx")
    assert masked_path.exists()

    masked_text = factory.extract_text(masked_path)
    assert "赵甲" in masked_text
    assert "张三" not in masked_text

    # Restore using the latest session created by the CLI.
    vault = VaultStorage(str(Path.cwd()))
    sessions = vault.list_sessions(limit=1)
    assert sessions

    restored_path = tmp_path / "restored.docx"
    cli.restore(
        file=masked_path,
        session_id=sessions[0].session_id,
        output=restored_path,
        dry_run=False,
        convert_legacy=True,
        allow_lossy=False,
        keep_legacy=False,
        soffice=None,
    )

    restored_text = factory.extract_text(restored_path)
    assert "张三" in restored_text
    assert "赵甲" not in restored_text
