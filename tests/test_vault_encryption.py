import base64
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vibemask.vault.crypto import (
    CIPHERTEXT_PREFIX,
    KeyringKeyProvider,
    VaultCipher,
    VaultDecryptionError,
    VaultKeyMissingError,
    VaultKeyUnavailableError,
    resolve_vault_key,
)
from vibemask.vault.storage import VaultStorage, get_project_fingerprint, get_vault_path


class MemoryKeyProvider:
    name = "memory-test-keyring"

    def __init__(self):
        self.keys: dict[str, bytes] = {}

    def get_key(self, project_id: str) -> bytes | None:
        return self.keys.get(project_id)

    def set_key(self, project_id: str, key: bytes) -> None:
        self.keys[project_id] = key


def test_vault_cipher_roundtrip_is_randomized():
    cipher = VaultCipher(b"k" * 32, "project-1")

    first = cipher.encrypt("SYNTHETIC-PII-001", "mappings:row-1:original_text")
    second = cipher.encrypt("SYNTHETIC-PII-001", "mappings:row-1:original_text")

    assert first.startswith(CIPHERTEXT_PREFIX)
    assert second.startswith(CIPHERTEXT_PREFIX)
    assert first != second
    assert "SYNTHETIC-PII-001" not in first
    assert cipher.decrypt(first, "mappings:row-1:original_text") == "SYNTHETIC-PII-001"
    assert cipher.decrypt(second, "mappings:row-1:original_text") == "SYNTHETIC-PII-001"


def test_vault_cipher_rejects_wrong_key_and_wrong_aad():
    ciphertext = VaultCipher(b"a" * 32, "project-1").encrypt(
        "SYNTHETIC-PII-002", "mappings:row-1:original_text"
    )

    with pytest.raises(VaultDecryptionError):
        VaultCipher(b"b" * 32, "project-1").decrypt(
            ciphertext, "mappings:row-1:original_text"
        )

    with pytest.raises(VaultDecryptionError):
        VaultCipher(b"a" * 32, "project-1").decrypt(
            ciphertext, "mappings:row-2:original_text"
        )


def test_vault_cipher_rejects_tampered_ciphertext():
    cipher = VaultCipher(b"c" * 32, "project-1")
    ciphertext = cipher.encrypt("SYNTHETIC-PII-003", "sessions:s1:mappings")
    payload = bytearray(base64.urlsafe_b64decode(ciphertext.removeprefix(CIPHERTEXT_PREFIX)))
    payload[-1] ^= 1
    tampered = CIPHERTEXT_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii")

    with pytest.raises(VaultDecryptionError):
        cipher.decrypt(tampered, "sessions:s1:mappings")


def test_resolve_vault_key_creates_only_for_unencrypted_vault():
    provider = MemoryKeyProvider()

    key = resolve_vault_key(provider, "project-1", encrypted_data_exists=False)

    assert len(key) == 32
    assert provider.get_key("project-1") == key


def test_resolve_vault_key_refuses_replacement_for_encrypted_vault():
    provider = MemoryKeyProvider()

    with pytest.raises(VaultKeyMissingError):
        resolve_vault_key(provider, "project-1", encrypted_data_exists=True)

    assert provider.get_key("project-1") is None


def test_keyring_provider_converts_backend_errors_to_fail_closed_error():
    class BrokenKeyring:
        @staticmethod
        def get_password(service: str, account: str) -> str | None:
            raise RuntimeError("backend unavailable")

    provider = KeyringKeyProvider(keyring_module=BrokenKeyring())

    with pytest.raises(VaultKeyUnavailableError):
        provider.get_key("project-1")


def test_vault_storage_encrypts_mapping_and_session_fields_at_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    provider = MemoryKeyProvider()
    project = tmp_path / "project"
    vault = VaultStorage(str(project), key_provider=provider)

    masked = vault.get_or_create_mapping(
        "SYNTHETIC-PII-AT-REST",
        "PERSON",
        "{{PERSON_000001}}",
    )
    session_id = vault.create_session(
        input_files=["/private/SYNTHETIC-INPUT.docx"],
        output_files=["/private/SYNTHETIC-OUTPUT.docx"],
        mappings={masked: "SYNTHETIC-PII-AT-REST"},
        stats={"PERSON": 1},
    )

    assert vault.get_mapping_by_masked(masked) == "SYNTHETIC-PII-AT-REST"
    assert vault.get_all_mappings() == {masked: "SYNTHETIC-PII-AT-REST"}
    session = vault.get_session(session_id)
    assert session is not None
    assert session.input_files == ["/private/SYNTHETIC-INPUT.docx"]
    assert session.output_files == ["/private/SYNTHETIC-OUTPUT.docx"]
    assert session.mappings == {masked: "SYNTHETIC-PII-AT-REST"}

    database_bytes = vault.vault_path.read_bytes()
    assert b"SYNTHETIC-PII-AT-REST" not in database_bytes
    assert b"SYNTHETIC-INPUT" not in database_bytes
    assert b"SYNTHETIC-OUTPUT" not in database_bytes
    assert CIPHERTEXT_PREFIX.encode() in database_bytes


def test_vault_storage_keeps_stable_mapping_with_encrypted_original(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    provider = MemoryKeyProvider()
    vault = VaultStorage(str(tmp_path / "project"), key_provider=provider)

    first = vault.get_or_create_mapping("SYNTHETIC-STABLE", "PERSON", "{{PERSON_1}}")
    second = vault.get_or_create_mapping("SYNTHETIC-STABLE", "PERSON", "{{PERSON_2}}")

    assert first == second == "{{PERSON_1}}"
    assert vault.get_stats()["total_mappings"] == 1


def _create_legacy_plaintext_vault(path: Path, project_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE mappings (
            entity_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            original_text TEXT NOT NULL,
            masked_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            source TEXT,
            confidence REAL
        );
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            input_files TEXT,
            output_files TEXT,
            mappings TEXT,
            stats TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-entity",
            project_id,
            "PERSON",
            "SYNTHETIC-LEGACY-PII",
            "{{PERSON_LEGACY}}",
            "2026-01-01",
            "2026-01-01",
            "test",
            1.0,
        ),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-session",
            project_id,
            "2026-01-01",
            "pending",
            json.dumps(["SYNTHETIC-LEGACY-INPUT.docx"]),
            json.dumps([]),
            json.dumps({"{{PERSON_LEGACY}}": "SYNTHETIC-LEGACY-PII"}),
            json.dumps({"PERSON": 1}),
        ),
    )
    conn.commit()
    conn.close()


def test_legacy_plaintext_vault_is_migrated_transactionally(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project_id = get_project_fingerprint(str(project))
    vault_path = get_vault_path(str(project))
    _create_legacy_plaintext_vault(vault_path, project_id)
    provider = MemoryKeyProvider()

    vault = VaultStorage(str(project), key_provider=provider)

    assert vault.get_mapping_by_masked("{{PERSON_LEGACY}}") == "SYNTHETIC-LEGACY-PII"
    session = vault.get_session("legacy-session")
    assert session is not None
    assert session.mappings == {"{{PERSON_LEGACY}}": "SYNTHETIC-LEGACY-PII"}
    assert b"SYNTHETIC-LEGACY-PII" not in vault_path.read_bytes()
    assert vault.get_stats()["encryption_version"] == 1

    reopened = VaultStorage(str(project), key_provider=provider)
    assert reopened.get_mapping_by_masked("{{PERSON_LEGACY}}") == "SYNTHETIC-LEGACY-PII"


def test_legacy_migration_rolls_back_all_sensitive_fields_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project_id = get_project_fingerprint(str(project))
    vault_path = get_vault_path(str(project))
    _create_legacy_plaintext_vault(vault_path, project_id)
    provider = MemoryKeyProvider()
    original_encrypt = VaultCipher.encrypt
    calls = 0

    def fail_after_first_encrypt(self, plaintext: str, context: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic migration failure")
        return original_encrypt(self, plaintext, context)

    monkeypatch.setattr(VaultCipher, "encrypt", fail_after_first_encrypt)

    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        VaultStorage(str(project), key_provider=provider)

    conn = sqlite3.connect(vault_path)
    try:
        mapping_value = conn.execute("SELECT original_text FROM mappings").fetchone()[0]
        session_value = conn.execute("SELECT mappings FROM sessions").fetchone()[0]
        version = conn.execute(
            "SELECT value FROM vault_meta WHERE key = 'encryption_version'"
        ).fetchone()
    finally:
        conn.close()
    assert mapping_value == "SYNTHETIC-LEGACY-PII"
    assert "SYNTHETIC-LEGACY-PII" in session_value
    assert version is None


def test_encrypted_vault_with_missing_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    provider = MemoryKeyProvider()
    vault = VaultStorage(str(project), key_provider=provider)
    vault.get_or_create_mapping("SYNTHETIC-KEY-LOSS", "PERSON", "{{PERSON_1}}")

    with pytest.raises(VaultKeyMissingError):
        VaultStorage(str(project), key_provider=MemoryKeyProvider())


def test_cli_status_reports_encryption_without_exposing_values(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from vibemask.cli import app

    result = CliRunner().invoke(app, ["status", "--project-root", str(tmp_path / "project")])

    assert result.exit_code == 0
    assert "AES-256-GCM" in result.output
    assert "memory-test-keyring" in result.output
    assert "SYNTHETIC" not in result.output
