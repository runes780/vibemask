import importlib
import json
import os
import stat

import pytest

from vibemask.vault.storage import VaultStorage


def recovery_module():
    return importlib.import_module("vibemask.vault.recovery")


@pytest.fixture
def vault_with_data(tmp_path, monkeypatch, isolate_vault_keyring):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    vault = VaultStorage(str(project), key_store=isolate_vault_keyring)
    masked = vault.get_or_create_mapping(
        "SYNTHETIC-RECOVERY-PII", "PERSON", "{{PERSON_RECOVERY}}"
    )
    return vault, isolate_vault_keyring, project, masked


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode assertion")
def test_recovery_bundle_is_private_authenticated_and_contains_no_plain_keys(
    vault_with_data, tmp_path
):
    vault, store, _, _ = vault_with_data
    output = tmp_path / "vault-recovery.json"
    encryption_key = store.get_encryption_key(vault.project_id, 1)
    integrity_key = store.get_integrity_key(vault.project_id)

    vault.backup_key(output, "correct horse battery staple")
    restored = recovery_module().load_recovery_bundle(
        output, "correct horse battery staple"
    )

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert encryption_key not in output.read_bytes()
    assert integrity_key not in output.read_bytes()
    assert b"correct horse" not in output.read_bytes()
    assert restored.project_id == vault.project_id
    assert restored.database_id == vault.security_status()["database_id"]
    assert restored.encryption_key == encryption_key
    assert restored.integrity_key == integrity_key
    assert vault.security_status()["recovery_current"] is True


def test_wrong_passphrase_and_tamper_use_safe_authentication_error(vault_with_data, tmp_path):
    vault, _, _, _ = vault_with_data
    output = tmp_path / "vault-recovery.json"
    vault.backup_key(output, "correct horse battery staple")

    with pytest.raises(recovery_module().RecoveryBundleError, match="authentication failed"):
        recovery_module().load_recovery_bundle(output, "wrong passphrase")

    envelope = json.loads(output.read_text())
    envelope["ciphertext"] = envelope["ciphertext"][:-2] + "AA"
    output.write_text(json.dumps(envelope))
    with pytest.raises(recovery_module().RecoveryBundleError, match="authentication failed"):
        recovery_module().load_recovery_bundle(output, "correct horse battery staple")


def test_new_recovery_bundle_rejects_short_passphrase(vault_with_data, tmp_path):
    vault, _, _, _ = vault_with_data

    with pytest.raises(recovery_module().RecoveryPassphraseError, match="at least 12"):
        vault.backup_key(tmp_path / "vault-recovery.json", "too-short")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"version": 99}),
        lambda value: value["kdf"].update({"n": 2**30}),
        lambda value: value["kdf"].update({"r": 999}),
        lambda value: value.update({"nonce": "not!base64"}),
    ],
)
def test_malformed_or_expensive_bundle_is_rejected_before_kdf(
    vault_with_data, tmp_path, mutation
):
    vault, _, _, _ = vault_with_data
    output = tmp_path / "vault-recovery.json"
    vault.backup_key(output, "correct horse battery staple")
    envelope = json.loads(output.read_text())
    mutation(envelope)
    output.write_text(json.dumps(envelope))

    with pytest.raises(recovery_module().RecoveryBundleError, match="authentication failed"):
        recovery_module().load_recovery_bundle(output, "correct horse battery staple")


def test_combined_scrypt_memory_cost_is_rejected_before_derivation(
    vault_with_data, tmp_path, monkeypatch
):
    vault, _, _, _ = vault_with_data
    output = tmp_path / "vault-recovery.json"
    vault.backup_key(output, "correct horse battery staple")
    envelope = json.loads(output.read_text())
    envelope["kdf"].update({"n": 2**16, "r": 16})
    output.write_text(json.dumps(envelope))
    recovery = recovery_module()

    def forbidden_scrypt(*args, **kwargs):
        raise AssertionError("unsafe Scrypt parameters reached key derivation")

    monkeypatch.setattr(recovery, "Scrypt", forbidden_scrypt)

    with pytest.raises(recovery.RecoveryBundleError, match="authentication failed"):
        recovery.load_recovery_bundle(output, "correct horse battery staple")


def test_missing_keyring_material_can_be_restored_and_verified(vault_with_data, tmp_path):
    vault, store, project, masked = vault_with_data
    output = tmp_path / "vault-recovery.json"
    vault.backup_key(output, "correct horse battery staple")
    store.delete_encryption_key(vault.project_id, 1)
    store.delete_integrity_key(vault.project_id)
    store.delete_trusted_state(vault.project_id)

    restored = VaultStorage.restore_key_for_project(
        str(project),
        output,
        "correct horse battery staple",
        key_store=store,
    )

    assert restored.get_mapping_by_masked(masked) == "SYNTHETIC-RECOVERY-PII"
    assert restored.verify()["verified"] is True


def test_restore_rejects_wrong_project_or_database(vault_with_data, tmp_path):
    vault, store, _, _ = vault_with_data
    output = tmp_path / "vault-recovery.json"
    vault.backup_key(output, "correct horse battery staple")

    with pytest.raises(recovery_module().RecoveryIdentityError):
        VaultStorage.restore_key_for_project(
            str(tmp_path / "other-project"),
            output,
            "correct horse battery staple",
            key_store=store,
        )


def test_restore_requires_explicit_replace_for_different_existing_key(
    vault_with_data, tmp_path
):
    vault, store, project, masked = vault_with_data
    output = tmp_path / "vault-recovery.json"
    vault.backup_key(output, "correct horse battery staple")
    store.set_encryption_key(vault.project_id, 1, b"x" * 32)

    with pytest.raises(recovery_module().RecoveryReplaceRequired):
        VaultStorage.restore_key_for_project(
            str(project),
            output,
            "correct horse battery staple",
            key_store=store,
        )

    restored = VaultStorage.restore_key_for_project(
        str(project),
        output,
        "correct horse battery staple",
        key_store=store,
        replace=True,
    )
    assert restored.get_mapping_by_masked(masked) == "SYNTHETIC-RECOVERY-PII"


def test_backup_refuses_overwrite_without_explicit_flag(vault_with_data, tmp_path):
    vault, _, _, _ = vault_with_data
    output = tmp_path / "vault-recovery.json"
    vault.backup_key(output, "correct horse battery staple")

    with pytest.raises(FileExistsError):
        vault.backup_key(output, "correct horse battery staple")

    vault.backup_key(output, "new correct horse battery staple", overwrite=True)
