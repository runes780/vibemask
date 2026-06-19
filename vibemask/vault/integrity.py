"""Logical vault manifests, authenticated audit chains, and rollback decisions."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

from .crypto import VaultSecurityError
from .keyring_policy import TrustedState


GENESIS_HEAD = "0" * 64
SECURITY_META_KEYS = (
    "active_key_version",
    "database_id",
    "encryption_version",
    "integrity_epoch",
)


class VaultIntegrityError(VaultSecurityError):
    """The vault cannot establish an authenticated integrity baseline."""


class VaultTamperError(VaultIntegrityError):
    """The database contents or audit history do not authenticate."""


class VaultRollbackError(VaultIntegrityError):
    """The database predates the latest trusted completed transaction."""


def _rows(conn: sqlite3.Connection, query: str) -> list[list[object]]:
    return [list(row) for row in conn.execute(query).fetchall()]


def compute_manifest(conn: sqlite3.Connection) -> str:
    """Hash every logical mapping/session field plus security metadata canonically."""
    placeholders = ",".join("?" for _ in SECURITY_META_KEYS)
    payload = {
        "mappings": _rows(
            conn,
            """
            SELECT entity_id, project_id, entity_type, original_text, original_digest,
                   masked_text, created_at, last_seen_at, source, confidence
            FROM mappings ORDER BY entity_id
            """,
        ),
        "sessions": _rows(
            conn,
            """
            SELECT session_id, project_id, created_at, status, input_files,
                   output_files, mappings, stats
            FROM sessions ORDER BY session_id
            """,
        ),
        "meta": [
            list(row)
            for row in conn.execute(
                f"SELECT key, value FROM vault_meta WHERE key IN ({placeholders}) ORDER BY key",
                SECURITY_META_KEYS,
            ).fetchall()
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_audit_head(
    key: bytes,
    epoch: int,
    previous_head: str,
    manifest: str,
    operation: str,
) -> str:
    """Authenticate one link in the append-only logical audit chain."""
    message = json.dumps(
        [epoch, previous_head, manifest, operation],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hmac.new(key, b"vibemask:audit:v1\0" + message, hashlib.sha256).hexdigest()


def _required_meta(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM vault_meta WHERE key = ?", (key,)).fetchone()
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise VaultTamperError("Vault security metadata is missing or invalid.")
    return row[0]


def append_integrity_checkpoint(
    conn: sqlite3.Connection,
    integrity_key: bytes,
    operation: str,
    *,
    recovery_key_version: int | None = None,
) -> TrustedState:
    """Append one checkpoint inside the caller's uncommitted write transaction."""
    previous = conn.execute(
        "SELECT epoch, head FROM vault_audit ORDER BY epoch DESC LIMIT 1"
    ).fetchone()
    if previous is None:
        epoch = 0
        previous_head = GENESIS_HEAD
    else:
        epoch = previous[0] + 1
        previous_head = previous[1]
    conn.execute(
        "INSERT OR REPLACE INTO vault_meta(key, value) VALUES ('integrity_epoch', ?)",
        (str(epoch),),
    )
    manifest = compute_manifest(conn)
    head = make_audit_head(integrity_key, epoch, previous_head, manifest, operation)
    conn.execute(
        """
        INSERT INTO vault_audit(epoch, previous_head, manifest, operation, head)
        VALUES (?, ?, ?, ?, ?)
        """,
        (epoch, previous_head, manifest, operation, head),
    )
    return TrustedState(
        database_id=_required_meta(conn, "database_id"),
        active_key_version=int(_required_meta(conn, "active_key_version")),
        epoch=epoch,
        manifest=manifest,
        chain_head=head,
        recovery_key_version=recovery_key_version,
    )


def validate_database(conn: sqlite3.Connection, integrity_key: bytes) -> TrustedState:
    """Validate the complete audit chain and the current logical manifest."""
    audit_rows = conn.execute(
        """
        SELECT epoch, previous_head, manifest, operation, head
        FROM vault_audit ORDER BY epoch
        """
    ).fetchall()
    if not audit_rows:
        raise VaultIntegrityError("The audited vault has no integrity history.")

    previous_head = GENESIS_HEAD
    for expected_epoch, row in enumerate(audit_rows):
        epoch, stored_previous, manifest, operation, head = row
        expected_head = make_audit_head(
            integrity_key, epoch, stored_previous, manifest, operation
        )
        if (
            epoch != expected_epoch
            or stored_previous != previous_head
            or not hmac.compare_digest(head, expected_head)
        ):
            raise VaultTamperError("The vault audit chain is invalid.")
        previous_head = head

    latest_epoch, _, latest_manifest, _, latest_head = audit_rows[-1]
    try:
        metadata_epoch = int(_required_meta(conn, "integrity_epoch"))
        active_key_version = int(_required_meta(conn, "active_key_version"))
    except ValueError as exc:
        raise VaultTamperError("Vault security metadata is invalid.") from exc
    current_manifest = compute_manifest(conn)
    if metadata_epoch != latest_epoch or not hmac.compare_digest(
        current_manifest, latest_manifest
    ):
        raise VaultTamperError("The vault logical manifest does not match its audit history.")
    if active_key_version < 1:
        raise VaultTamperError("The vault active key version is invalid.")
    return TrustedState(
        database_id=_required_meta(conn, "database_id"),
        active_key_version=active_key_version,
        epoch=latest_epoch,
        manifest=current_manifest,
        chain_head=latest_head,
        recovery_key_version=None,
    )


def compare_with_trusted_state(
    conn: sqlite3.Connection,
    current: TrustedState,
    trusted: TrustedState,
) -> TrustedState:
    """Reject rollback/mismatch or return the state to promote after a valid crash window."""
    if current.database_id != trusted.database_id:
        raise VaultTamperError("The vault database identity does not match trusted state.")
    if current.epoch < trusted.epoch:
        raise VaultRollbackError("The vault database is older than trusted state.")
    if current.epoch == trusted.epoch:
        if (
            current.active_key_version != trusted.active_key_version
            or not hmac.compare_digest(current.manifest, trusted.manifest)
            or not hmac.compare_digest(current.chain_head, trusted.chain_head)
        ):
            raise VaultTamperError("The vault database does not match trusted state.")
    else:
        anchor = conn.execute(
            "SELECT manifest, head FROM vault_audit WHERE epoch = ?", (trusted.epoch,)
        ).fetchone()
        if anchor is None or not hmac.compare_digest(anchor[0], trusted.manifest) or not hmac.compare_digest(
            anchor[1], trusted.chain_head
        ):
            raise VaultTamperError("The vault audit history does not continue trusted state.")
    return TrustedState(
        database_id=current.database_id,
        active_key_version=current.active_key_version,
        epoch=current.epoch,
        manifest=current.manifest,
        chain_head=current.chain_head,
        recovery_key_version=trusted.recovery_key_version,
    )
