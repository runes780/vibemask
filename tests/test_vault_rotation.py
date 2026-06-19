import sqlite3

import pytest

from vibemask.vault.crypto import VaultCipher, VaultKeyUnavailableError
from vibemask.vault.storage import VaultStorage


@pytest.fixture
def rotatable_vault(tmp_path, monkeypatch, isolate_vault_keyring):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    vault = VaultStorage(str(project), key_store=isolate_vault_keyring)
    first = vault.get_or_create_mapping(
        "SYNTHETIC-ROTATE-A", "PERSON", "{{PERSON_ROTATE_A}}"
    )
    second = vault.get_or_create_mapping(
        "SYNTHETIC-ROTATE-B", "PERSON", "{{PERSON_ROTATE_B}}"
    )
    vault.create_session(
        ["synthetic-input.docx"],
        {first: "SYNTHETIC-ROTATE-A", second: "SYNTHETIC-ROTATE-B"},
        {"PERSON": 2},
        output_files=["synthetic-output.docx"],
    )
    return vault, isolate_vault_keyring, project, first, second


def raw_sensitive_values(vault):
    conn = sqlite3.connect(vault.vault_path)
    try:
        values = [row[0] for row in conn.execute("SELECT original_text FROM mappings")]
        for row in conn.execute("SELECT input_files, output_files, mappings FROM sessions"):
            values.extend(row)
        active = conn.execute(
            "SELECT value FROM vault_meta WHERE key = 'active_key_version'"
        ).fetchone()[0]
        return values, int(active)
    finally:
        conn.close()


def test_rotation_reencrypts_all_fields_and_deletes_old_key(rotatable_vault, tmp_path):
    vault, store, _, first, second = rotatable_vault
    backup = tmp_path / "before-rotation.json"
    vault.backup_key(backup, "correct horse battery staple")
    before, old_version = raw_sensitive_values(vault)

    new_version = vault.rotate_key()
    after, active_version = raw_sensitive_values(vault)

    assert old_version == 1
    assert new_version == active_version == 2
    assert before != after
    assert all(value.startswith("vmenc:v2:2:") for value in after)
    assert store.get_encryption_key(vault.project_id, 1) is None
    assert store.get_encryption_key(vault.project_id, 2) is not None
    assert vault.get_mapping_by_masked(first) == "SYNTHETIC-ROTATE-A"
    assert vault.get_mapping_by_masked(second) == "SYNTHETIC-ROTATE-B"
    assert vault.security_status()["recovery_current"] is False

    vault.backup_key(
        tmp_path / "after-rotation.json", "new correct horse battery staple"
    )
    assert vault.security_status()["recovery_current"] is True


def test_rotation_failure_before_commit_rolls_back_and_removes_new_key(
    rotatable_vault, monkeypatch
):
    vault, store, _, first, _ = rotatable_vault
    original_encrypt = VaultCipher.encrypt
    calls = 0

    def fail_second_new_encryption(self, plaintext, context):
        nonlocal calls
        if self.key_version == 2:
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic rotation encryption failure")
        return original_encrypt(self, plaintext, context)

    monkeypatch.setattr(VaultCipher, "encrypt", fail_second_new_encryption)

    with pytest.raises(RuntimeError, match="synthetic rotation encryption failure"):
        vault.rotate_key()

    _, active_version = raw_sensitive_values(vault)
    assert active_version == 1
    assert store.get_encryption_key(vault.project_id, 2) is None
    assert vault.get_mapping_by_masked(first) == "SYNTHETIC-ROTATE-A"


def test_checkpoint_failure_after_commit_keeps_both_keys_and_recovers_on_open(
    rotatable_vault, monkeypatch
):
    vault, store, project, first, _ = rotatable_vault
    original_set_state = store.set_trusted_state
    fail_next = True

    def fail_checkpoint(project_id, state):
        nonlocal fail_next
        if fail_next and state.active_key_version == 2:
            fail_next = False
            raise VaultKeyUnavailableError("synthetic checkpoint failure")
        original_set_state(project_id, state)

    monkeypatch.setattr(store, "set_trusted_state", fail_checkpoint)

    with pytest.raises(VaultKeyUnavailableError, match="synthetic checkpoint failure"):
        vault.rotate_key()

    _, active_version = raw_sensitive_values(vault)
    assert active_version == 2
    assert store.get_encryption_key(vault.project_id, 1) is not None
    assert store.get_encryption_key(vault.project_id, 2) is not None

    reopened = VaultStorage(str(project), key_store=store)
    assert reopened.get_mapping_by_masked(first) == "SYNTHETIC-ROTATE-A"
    assert store.get_trusted_state(vault.project_id).active_key_version == 2


def test_old_key_deletion_failure_leaves_recoverable_rotated_vault(
    rotatable_vault, monkeypatch
):
    vault, store, project, first, _ = rotatable_vault
    original_delete = store.delete_encryption_key

    def fail_old_delete(project_id, version):
        if version == 1:
            raise VaultKeyUnavailableError("synthetic old-key deletion failure")
        original_delete(project_id, version)

    monkeypatch.setattr(store, "delete_encryption_key", fail_old_delete)

    with pytest.raises(VaultKeyUnavailableError, match="synthetic old-key deletion failure"):
        vault.rotate_key()

    reopened = VaultStorage(str(project), key_store=store)
    assert reopened.security_status()["active_key_version"] == 2
    assert reopened.get_mapping_by_masked(first) == "SYNTHETIC-ROTATE-A"
    assert store.get_encryption_key(vault.project_id, 1) is not None
