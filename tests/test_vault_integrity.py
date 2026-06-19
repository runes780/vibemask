import importlib
import shutil
import sqlite3

import pytest

from vibemask.vault.storage import VaultStorage


def integrity_module():
    return importlib.import_module("vibemask.vault.integrity")


@pytest.fixture
def secured_vault(tmp_path, monkeypatch, isolate_vault_keyring):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    vault = VaultStorage(str(project), key_store=isolate_vault_keyring)
    first_mask = vault.get_or_create_mapping(
        "SYNTHETIC-INTEGRITY-A", "PERSON", "{{PERSON_1}}"
    )
    vault.create_session(
        input_files=["/synthetic/input.docx"],
        output_files=["/synthetic/output.docx"],
        mappings={first_mask: "SYNTHETIC-INTEGRITY-A"},
        stats={"PERSON": 1},
    )
    return vault, isolate_vault_keyring, project


def mutate_database(vault, statement, parameters=()):
    conn = sqlite3.connect(vault.vault_path)
    try:
        conn.execute(statement, parameters)
        conn.commit()
    finally:
        conn.close()


def test_ciphertext_replacement_is_rejected_before_plaintext(secured_vault):
    vault, store, project = secured_vault
    mutate_database(
        vault,
        "UPDATE mappings SET original_text = original_text || 'A'",
    )

    with pytest.raises(integrity_module().VaultTamperError):
        VaultStorage(str(project), key_store=store)


def test_row_deletion_is_rejected(secured_vault):
    vault, store, project = secured_vault
    mutate_database(vault, "DELETE FROM mappings")

    with pytest.raises(integrity_module().VaultTamperError):
        VaultStorage(str(project), key_store=store)


def test_plaintext_metadata_replacement_is_rejected(secured_vault):
    vault, store, project = secured_vault
    mutate_database(vault, "UPDATE sessions SET stats = ?", ('{"PERSON":999}',))

    with pytest.raises(integrity_module().VaultTamperError):
        VaultStorage(str(project), key_store=store)


def test_audit_truncation_is_rejected(secured_vault):
    vault, store, project = secured_vault
    mutate_database(
        vault,
        "DELETE FROM vault_audit WHERE epoch = (SELECT MAX(epoch) FROM vault_audit)",
    )

    with pytest.raises(integrity_module().VaultTamperError):
        VaultStorage(str(project), key_store=store)


def test_completed_database_rollback_is_rejected(secured_vault, tmp_path):
    vault, store, project = secured_vault
    snapshot = tmp_path / "old-vault.sqlite"
    shutil.copyfile(vault.vault_path, snapshot)
    vault.get_or_create_mapping("SYNTHETIC-INTEGRITY-B", "PERSON", "{{PERSON_2}}")
    shutil.copyfile(snapshot, vault.vault_path)

    with pytest.raises(integrity_module().VaultRollbackError):
        VaultStorage(str(project), key_store=store)


def test_database_ahead_of_keyring_recovers_valid_commit(secured_vault):
    vault, store, project = secured_vault
    old_state = store.get_trusted_state(vault.project_id)
    vault.get_or_create_mapping("SYNTHETIC-INTEGRITY-B", "PERSON", "{{PERSON_2}}")
    store.set_trusted_state(vault.project_id, old_state)

    reopened = VaultStorage(str(project), key_store=store)

    current = store.get_trusted_state(vault.project_id)
    assert current.epoch == reopened.security_status()["epoch"]
    assert current.epoch > old_state.epoch


def test_missing_trusted_state_for_audited_vault_fails_closed(secured_vault):
    vault, store, project = secured_vault
    store.delete_trusted_state(vault.project_id)

    with pytest.raises(integrity_module().VaultIntegrityError, match="trusted state"):
        VaultStorage(str(project), key_store=store)


def test_verify_reports_current_manifest_without_exposing_values(secured_vault):
    vault, _, _ = secured_vault

    result = vault.verify()

    assert result["verified"] is True
    assert result["epoch"] >= 2
    assert len(result["manifest"]) == 64
    assert "SYNTHETIC" not in str(result)
