from pathlib import Path
import subprocess

from typer.testing import CliRunner


def test_cli_exec_masks_prompt_args_and_runs_tool(tmp_path: Path, monkeypatch):
    from vibemask.core.span import EntityType, SourceType, Span
    import vibemask.cli as cli
    import vibemask.detector.hybrid as hybrid
    from vibemask.vault.storage import VaultStorage

    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    calls = []

    class DummyHybridDetector:
        def __init__(self, **kwargs):
            pass

        def detect(self, text):
            if "张三" not in text:
                return []
            start = text.index("张三")
            return [
                Span(
                    start=start,
                    end=start + len("张三"),
                    text="张三",
                    type=EntityType.PERSON,
                    source=SourceType.PRIVACY_FILTER,
                    confidence=1.0,
                    reason="privacy_filter_mlx:private_person",
                )
            ]

    class DummyCompleted:
        returncode = 0
        stdout = ""

    def fake_run(command, cwd=None, capture_output=False, **kwargs):
        if command and command[0] == "git":
            return DummyCompleted()
        calls.append((command, cwd, capture_output))
        return DummyCompleted()

    monkeypatch.setattr(hybrid, "HybridDetector", DummyHybridDetector)
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "exec",
            "--project-root",
            str(tmp_path),
            "--no-restore",
            "codex",
            "请处理张三的文件",
            "--flag",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            ["codex", "请处理{{PERSON_000001}}的文件", "--flag"],
            tmp_path.resolve(),
            False,
        )
    ]
    mappings = VaultStorage(str(tmp_path)).get_all_mappings()
    assert mappings["{{PERSON_000001}}"] == "张三"
    assert VaultStorage(str(tmp_path)).security_status()["epoch"] == 1


def test_cli_exec_dry_run_does_not_run_tool(tmp_path: Path, monkeypatch):
    import vibemask.cli as cli
    import vibemask.detector.hybrid as hybrid

    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    class DummyHybridDetector:
        def __init__(self, **kwargs):
            pass

        def detect(self, text):
            return []

    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(hybrid, "HybridDetector", DummyHybridDetector)
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["exec", "--dry-run", "claude", "hello"])

    assert result.exit_code == 0
    assert calls == []


def test_root_double_dash_wraps_arbitrary_command(tmp_path: Path, monkeypatch):
    import vibemask.cli as cli
    import vibemask.detector.hybrid as hybrid
    from vibemask.vault.storage import VaultStorage

    monkeypatch.chdir(tmp_path)

    class DummyHybridDetector:
        def __init__(self, **kwargs):
            pass

        def detect(self, text):
            return []

    class DummyCompleted:
        returncode = 0
        stdout = ""

    calls = []

    def fake_run(command, cwd=None, capture_output=False, **kwargs):
        if command and command[0] == "git":
            return DummyCompleted()
        calls.append((command, cwd, capture_output))
        return DummyCompleted()

    monkeypatch.setattr(hybrid, "HybridDetector", DummyHybridDetector)
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["--", "echo", "hello"])

    assert result.exit_code == 0
    assert calls == [(["echo", "hello"], tmp_path.resolve(), False)]
    assert VaultStorage(str(tmp_path)).security_status()["epoch"] == 0


def test_cli_exec_does_not_restore_files_dirty_before_command(tmp_path: Path, monkeypatch):
    import subprocess as real_subprocess
    import vibemask.cli as cli
    import vibemask.detector.hybrid as hybrid
    from vibemask.vault.storage import VaultStorage

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    real_subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    dirty_file = tmp_path / "notes.txt"
    dirty_file.write_text("{{PERSON_000001}}", encoding="utf-8")
    vault = VaultStorage(str(tmp_path))
    vault.get_or_create_mapping(
        original="张三",
        entity_type="PERSON",
        masked="{{PERSON_000001}}",
    )

    class DummyHybridDetector:
        def __init__(self, **kwargs):
            pass

        def detect(self, text):
            return []

    class DummyCompleted:
        returncode = 0
        stdout = "notes.txt\n"

    def fake_run(command, cwd=None, capture_output=False, **kwargs):
        if command and command[0] == "git":
            return DummyCompleted()
        return DummyCompleted()

    monkeypatch.setattr(hybrid, "HybridDetector", DummyHybridDetector)
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["exec", "--project-root", str(tmp_path), "echo", "hello"])

    assert result.exit_code == 0
    assert dirty_file.read_text(encoding="utf-8") == "{{PERSON_000001}}"


def test_shell_init_prints_codex_and_claude_wrappers():
    import vibemask.cli as cli

    runner = CliRunner()
    result = runner.invoke(cli.app, ["shell-init"])

    assert result.exit_code == 0
    assert 'vibemask -- "$_vibemask_codex_bin" "$@"' in result.output
    assert 'vibemask -- "$_vibemask_claude_bin" "$@"' in result.output
    assert "claude-yolo()" in result.output
