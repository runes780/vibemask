import base64
import hashlib
import hmac
import json
import sqlite3

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vibemask.vault.storage import VaultStorage, get_project_fingerprint, get_vault_path


def test_batch_failure_rolls_back_mappings_and_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = VaultStorage(str(tmp_path / "project"))
    initial_epoch = vault.security_status()["epoch"]

    with pytest.raises(RuntimeError, match="synthetic failure"):
        with vault.batch("mask-session"):
            masked = vault.get_or_create_mapping(
                "SYNTHETIC-BATCH", "PERSON", "{{PERSON_1}}"
            )
            vault.create_session(
                input_files=["synthetic.docx"],
                mappings={masked: "SYNTHETIC-BATCH"},
                stats={"PERSON": 1},
            )
            raise RuntimeError("synthetic failure")

    assert vault.get_stats()["total_mappings"] == 0
    assert vault.get_stats()["total_sessions"] == 0
    assert vault.security_status()["epoch"] == initial_epoch


def test_batch_uses_one_epoch_and_one_keyring_checkpoint(
    tmp_path, monkeypatch, isolate_vault_keyring
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = VaultStorage(str(tmp_path / "project"), key_store=isolate_vault_keyring)
    initial_epoch = vault.security_status()["epoch"]
    original_set_state = isolate_vault_keyring.set_trusted_state
    checkpoints = 0

    def count_checkpoint(project_id, state):
        nonlocal checkpoints
        checkpoints += 1
        original_set_state(project_id, state)

    monkeypatch.setattr(isolate_vault_keyring, "set_trusted_state", count_checkpoint)

    with vault.batch("mask-session"):
        mappings = {}
        for number in range(100):
            original = f"SYNTHETIC-BATCH-{number:03d}"
            masked = vault.get_or_create_mapping(
                original, "PERSON", f"{{{{PERSON_{number:03d}}}}}"
            )
            mappings[masked] = original
        vault.create_session(["synthetic.docx"], mappings, {"PERSON": 100})

    assert vault.security_status()["epoch"] == initial_epoch + 1
    assert checkpoints == 1
    assert vault.get_stats()["total_mappings"] == 100


def test_caught_nested_batch_failure_still_aborts_outer_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = VaultStorage(str(tmp_path / "project"))

    with pytest.raises(RuntimeError, match="batch was marked failed"):
        with vault.batch("outer"):
            vault.get_or_create_mapping("SYNTHETIC-A", "PERSON", "{{PERSON_1}}")
            try:
                with vault.batch("inner"):
                    raise ValueError("inner failure")
            except ValueError:
                pass

    assert vault.get_stats()["total_mappings"] == 0


def legacy_encrypt(key: bytes, project_id: str, context: str, plaintext: str) -> str:
    nonce = hashlib.sha256(context.encode()).digest()[:12]
    aad = f"vibemask:v1:{project_id}:{context}".encode()
    payload = nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), aad)
    return "vmenc:v1:" + base64.urlsafe_b64encode(payload).decode()


def create_v1_vault(path, project_id, key):
    path.parent.mkdir(parents=True, exist_ok=True)
    original = "SYNTHETIC-V1-PII"
    lookup_key = hmac.new(key, b"vibemask:v1:lookup", hashlib.sha256).digest()
    digest_message = f"{project_id}:mapping-original\0{original}".encode()
    digest = "vmhmac:v1:" + hmac.new(lookup_key, digest_message, hashlib.sha256).hexdigest()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE mappings (
            entity_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, entity_type TEXT NOT NULL,
            original_text TEXT NOT NULL, original_digest TEXT, masked_text TEXT NOT NULL,
            created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, source TEXT, confidence REAL
        );
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, created_at TEXT NOT NULL,
            status TEXT NOT NULL, input_files TEXT, output_files TEXT, mappings TEXT, stats TEXT
        );
        CREATE TABLE vault_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.execute(
        "INSERT INTO mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-entity",
            project_id,
            "PERSON",
            legacy_encrypt(key, project_id, "mappings:legacy-entity:original_text", original),
            digest,
            "{{PERSON_LEGACY}}",
            "2026-01-01",
            "2026-01-01",
            "test",
            1.0,
        ),
    )
    for field, value in {
        "input_files": json.dumps(["synthetic-v1.docx"]),
        "output_files": "[]",
        "mappings": json.dumps({"{{PERSON_LEGACY}}": original}),
    }.items():
        encrypted = legacy_encrypt(key, project_id, f"sessions:legacy-session:{field}", value)
        if field == "input_files":
            input_files = encrypted
        elif field == "output_files":
            output_files = encrypted
        else:
            mappings = encrypted
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-session",
            project_id,
            "2026-01-01",
            "pending",
            input_files,
            output_files,
            mappings,
            json.dumps({"PERSON": 1}),
        ),
    )
    conn.execute("INSERT INTO vault_meta VALUES ('encryption_version', '1')")
    conn.commit()
    conn.close()


def test_v1_rows_upgrade_to_v2_before_integrity_bootstrap(
    tmp_path, monkeypatch, isolate_vault_keyring
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project_id = get_project_fingerprint(str(project))
    path = get_vault_path(str(project))
    key = b"v" * 32
    isolate_vault_keyring.set_encryption_key(project_id, 1, key)
    create_v1_vault(path, project_id, key)

    vault = VaultStorage(str(project), key_store=isolate_vault_keyring)

    conn = sqlite3.connect(path)
    try:
        mapping = conn.execute("SELECT original_text, original_digest FROM mappings").fetchone()
        session = conn.execute("SELECT input_files, output_files, mappings FROM sessions").fetchone()
    finally:
        conn.close()
    assert mapping[0].startswith("vmenc:v2:1:")
    assert mapping[1].startswith("vmhmac:v2:1:")
    assert all(value.startswith("vmenc:v2:1:") for value in session)
    assert vault.get_mapping_by_masked("{{PERSON_LEGACY}}") == "SYNTHETIC-V1-PII"
    assert vault.security_status()["epoch"] == 0
