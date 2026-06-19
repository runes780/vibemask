"""Passphrase-protected, authenticated recovery bundles for Vault key material."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .crypto import KEY_SIZE, NONCE_SIZE, VaultSecurityError
from .keyring_policy import TrustedState, decode_trusted_state, encode_trusted_state


RECOVERY_VERSION = 1
RECOVERY_AAD = b"vibemask:recovery:v1"
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
MAX_SCRYPT_N = 2**18
MAX_SCRYPT_R = 16
MAX_SCRYPT_P = 4


class RecoveryBundleError(VaultSecurityError):
    """A recovery bundle could not be parsed or authenticated safely."""


class RecoveryIdentityError(RecoveryBundleError):
    """The recovery bundle belongs to another project or database."""


class RecoveryReplaceRequired(RecoveryBundleError):
    """Restoring would replace different keyring material."""


@dataclass(frozen=True)
class RecoveryPayload:
    project_id: str
    database_id: str
    key_version: int
    encryption_key: bytes
    integrity_key: bytes
    trusted_state: TrustedState


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("encoded value is not text")
    return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)


def _validate_kdf(kdf: object) -> tuple[int, int, int, bytes]:
    if not isinstance(kdf, dict) or set(kdf) != {"name", "n", "r", "p", "salt"}:
        raise ValueError("invalid KDF fields")
    n, r, p = kdf["n"], kdf["r"], kdf["p"]
    if (
        kdf["name"] != "scrypt"
        or not isinstance(n, int)
        or isinstance(n, bool)
        or n < 2**14
        or n > MAX_SCRYPT_N
        or n & (n - 1)
        or not isinstance(r, int)
        or isinstance(r, bool)
        or not 1 <= r <= MAX_SCRYPT_R
        or not isinstance(p, int)
        or isinstance(p, bool)
        or not 1 <= p <= MAX_SCRYPT_P
    ):
        raise ValueError("unsafe KDF parameters")
    salt = _decode(kdf["salt"])
    if len(salt) != 16:
        raise ValueError("invalid salt")
    return n, r, p, salt


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("empty passphrase")
    return Scrypt(salt=salt, length=KEY_SIZE, n=n, r=r, p=p).derive(
        passphrase.encode("utf-8")
    )


def _serialize_payload(payload: RecoveryPayload) -> bytes:
    if (
        not payload.project_id
        or not payload.database_id
        or payload.key_version < 1
        or len(payload.encryption_key) != KEY_SIZE
        or len(payload.integrity_key) != KEY_SIZE
    ):
        raise ValueError("invalid recovery payload")
    state = json.loads(encode_trusted_state(payload.trusted_state))
    value = {
        "project_id": payload.project_id,
        "database_id": payload.database_id,
        "key_version": payload.key_version,
        "encryption_key": _encode(payload.encryption_key),
        "integrity_key": _encode(payload.integrity_key),
        "trusted_state": state,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _deserialize_payload(encoded: bytes) -> RecoveryPayload:
    value = json.loads(encoded)
    expected = {
        "project_id",
        "database_id",
        "key_version",
        "encryption_key",
        "integrity_key",
        "trusted_state",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("invalid payload fields")
    trusted_state = decode_trusted_state(
        json.dumps(value["trusted_state"], sort_keys=True, separators=(",", ":"))
    )
    payload = RecoveryPayload(
        project_id=value["project_id"],
        database_id=value["database_id"],
        key_version=value["key_version"],
        encryption_key=_decode(value["encryption_key"]),
        integrity_key=_decode(value["integrity_key"]),
        trusted_state=trusted_state,
    )
    _serialize_payload(payload)
    if (
        payload.database_id != trusted_state.database_id
        or payload.key_version != trusted_state.active_key_version
    ):
        raise ValueError("payload identity mismatch")
    return payload


def create_recovery_envelope(payload: RecoveryPayload, passphrase: str) -> bytes:
    """Encrypt a recovery payload into the public JSON envelope."""
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(NONCE_SIZE)
    key = _derive_key(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    ciphertext = AESGCM(key).encrypt(nonce, _serialize_payload(payload), RECOVERY_AAD)
    envelope = {
        "format": "vibemask-recovery",
        "version": RECOVERY_VERSION,
        "kdf": {
            "name": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": _encode(salt),
        },
        "cipher": "AES-256-GCM",
        "nonce": _encode(nonce),
        "ciphertext": _encode(ciphertext),
    }
    return (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _parse_recovery_envelope(data: bytes, passphrase: str) -> RecoveryPayload:
    value = json.loads(data)
    expected = {"format", "version", "kdf", "cipher", "nonce", "ciphertext"}
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value["format"] != "vibemask-recovery"
        or value["version"] != RECOVERY_VERSION
        or value["cipher"] != "AES-256-GCM"
    ):
        raise ValueError("invalid envelope")
    n, r, p, salt = _validate_kdf(value["kdf"])
    nonce = _decode(value["nonce"])
    ciphertext = _decode(value["ciphertext"])
    if len(nonce) != NONCE_SIZE or len(ciphertext) < 16:
        raise ValueError("invalid cipher payload")
    key = _derive_key(passphrase, salt, n, r, p)
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, RECOVERY_AAD)
    return _deserialize_payload(plaintext)


def load_recovery_bundle(path: Path, passphrase: str) -> RecoveryPayload:
    """Load a bundle while presenting one safe error for parsing/authentication failures."""
    try:
        return _parse_recovery_envelope(Path(path).read_bytes(), passphrase)
    except (
        InvalidTag,
        OSError,
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise RecoveryBundleError("Recovery bundle authentication failed.") from exc


def write_recovery_bundle(
    path: Path,
    payload: RecoveryPayload,
    passphrase: str,
    *,
    overwrite: bool = False,
) -> None:
    """Durably write one private bundle using same-directory atomic replacement."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Recovery bundle already exists: {destination}")
    data = create_recovery_envelope(payload, passphrase)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        if os.name != "nt":
            destination.chmod(0o600)
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
