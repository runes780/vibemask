from typer.testing import CliRunner

import pytest

from vibemask.vault.storage import VaultStorage


@pytest.fixture
def cli_vault(tmp_path, monkeypatch, isolate_vault_keyring):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    vault = VaultStorage(str(project), key_store=isolate_vault_keyring)
    vault.get_or_create_mapping(
        "SYNTHETIC-CLI-SECRET", "PERSON", "{{PERSON_CLI}}"
    )
    return vault, isolate_vault_keyring, project


def test_vault_security_status_contains_no_sensitive_values(cli_vault):
    from vibemask.cli import app

    _, _, project = cli_vault
    result = CliRunner().invoke(
        app, ["vault", "security-status", "--project-root", str(project)]
    )

    assert result.exit_code == 0
    assert "AES-256-GCM" in result.output
    assert "Verified" in result.output
    assert "SYNTHETIC-CLI-SECRET" not in result.output
    assert "vmenc:" not in result.output


def test_vault_verify_reports_epoch_without_values(cli_vault):
    from vibemask.cli import app

    vault, _, project = cli_vault
    result = CliRunner().invoke(
        app, ["vault", "verify", "--project-root", str(project)]
    )

    assert result.exit_code == 0
    assert f"epoch {vault.security_status()['epoch']}" in result.output
    assert "SYNTHETIC" not in result.output


def test_backup_key_uses_hidden_confirmed_prompt(cli_vault, tmp_path):
    from vibemask.cli import app

    _, _, project = cli_vault
    output = tmp_path / "recovery.json"
    passphrase = "correct horse battery staple"
    result = CliRunner().invoke(
        app,
        [
            "vault",
            "backup-key",
            "--project-root",
            str(project),
            "--output",
            str(output),
        ],
        input=f"{passphrase}\n{passphrase}\n",
    )

    assert result.exit_code == 0
    assert output.exists()
    assert passphrase not in result.output
    assert "SYNTHETIC" not in result.output


def test_restore_key_recovers_missing_material(cli_vault, tmp_path):
    from vibemask.cli import app

    vault, store, project = cli_vault
    output = tmp_path / "recovery.json"
    passphrase = "correct horse battery staple"
    vault.backup_key(output, passphrase)
    store.delete_encryption_key(vault.project_id, 1)
    store.delete_integrity_key(vault.project_id)
    store.delete_trusted_state(vault.project_id)

    result = CliRunner().invoke(
        app,
        ["vault", "restore-key", str(output), "--project-root", str(project)],
        input=f"{passphrase}\n",
    )

    assert result.exit_code == 0
    assert "restored and verified" in result.output.lower()
    assert passphrase not in result.output


def test_rotate_key_requires_confirmation_and_marks_recovery_stale(cli_vault):
    from vibemask.cli import app

    vault, _, project = cli_vault
    result = CliRunner().invoke(
        app,
        ["vault", "rotate-key", "--project-root", str(project)],
        input="n\n",
    )
    assert result.exit_code == 0
    assert vault.security_status()["active_key_version"] == 1

    result = CliRunner().invoke(
        app,
        ["vault", "rotate-key", "--project-root", str(project), "--yes"],
    )
    assert result.exit_code == 0
    reopened = VaultStorage(str(project))
    assert reopened.security_status()["active_key_version"] == 2
    assert "backup-key" in result.output


def test_vault_cli_security_errors_are_short_and_safe(cli_vault, monkeypatch):
    from vibemask.cli import app
    from vibemask.vault.crypto import VaultSecurityError

    _, _, project = cli_vault

    def fail_verify(self):
        raise VaultSecurityError("Synthetic safe failure")

    monkeypatch.setattr(VaultStorage, "verify", fail_verify)
    result = CliRunner().invoke(
        app, ["vault", "verify", "--project-root", str(project)]
    )

    assert result.exit_code == 1
    assert "Synthetic safe failure" in result.output
    assert "Traceback" not in result.output
    assert "SYNTHETIC-CLI-SECRET" not in result.output
