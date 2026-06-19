"""Fail-closed policy for versioned VibeMask secrets in native OS keyrings."""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from dataclasses import asdict, dataclass
from typing import Protocol

from .crypto import KEYRING_SERVICE, KEY_SIZE, VaultKeyUnavailableError
from .crypto import VaultKeyMissingError


TRUSTED_STATE_VERSION = 1
APPROVED_BACKEND_MODULES = (
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.libsecret",
)


@dataclass(frozen=True)
class TrustedState:
    """Latest vault security state anchored outside SQLite."""

    database_id: str
    active_key_version: int
    epoch: int
    manifest: str
    chain_head: str
    recovery_key_version: int | None = None


class SecureKeyStore(Protocol):
    """Secret-store surface required by the production vault."""

    name: str

    def get_encryption_key(self, project_id: str, version: int) -> bytes | None: ...

    def set_encryption_key(self, project_id: str, version: int, key: bytes) -> None: ...

    def delete_encryption_key(self, project_id: str, version: int) -> None: ...

    def get_integrity_key(self, project_id: str) -> bytes | None: ...

    def set_integrity_key(self, project_id: str, key: bytes) -> None: ...

    def delete_integrity_key(self, project_id: str) -> None: ...

    def get_trusted_state(self, project_id: str) -> TrustedState | None: ...

    def set_trusted_state(self, project_id: str, state: TrustedState) -> None: ...

    def delete_trusted_state(self, project_id: str) -> None: ...


def assert_backend_allowed(backend: object) -> None:
    """Reject fallback, plaintext, chained, and unknown keyring backends."""
    module = backend.__class__.__module__
    if module not in APPROVED_BACKEND_MODULES:
        raise VaultKeyUnavailableError(
            "Vault security requires an approved native operating-system keyring backend."
        )


def encode_key(key: bytes) -> str:
    """Encode one validated 256-bit key for keyring text storage."""
    if not isinstance(key, bytes) or len(key) != KEY_SIZE:
        raise ValueError("Vault keys must be exactly 32 bytes.")
    return base64.urlsafe_b64encode(key).decode("ascii")


def decode_key(encoded: str) -> bytes:
    """Strictly decode one keyring value without accepting invalid alphabet."""
    try:
        key = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
    except (AttributeError, binascii.Error, TypeError, UnicodeError, ValueError) as exc:
        raise VaultKeyUnavailableError("The vault key stored in the keyring is invalid.") from exc
    if len(key) != KEY_SIZE:
        raise VaultKeyUnavailableError("The vault key stored in the keyring is invalid.")
    return key


def _is_hex_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def encode_trusted_state(state: TrustedState) -> str:
    """Serialize a validated trusted-state record deterministically."""
    _validate_trusted_state(state)
    payload = {"state_version": TRUSTED_STATE_VERSION, **asdict(state)}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode_trusted_state(encoded: str) -> TrustedState:
    """Strictly parse trusted state from the keyring."""
    try:
        payload = json.loads(encoded)
        if not isinstance(payload, dict) or payload.pop("state_version") != TRUSTED_STATE_VERSION:
            raise ValueError("unsupported trusted-state version")
        expected = {
            "database_id",
            "active_key_version",
            "epoch",
            "manifest",
            "chain_head",
            "recovery_key_version",
        }
        if set(payload) != expected:
            raise ValueError("invalid trusted-state fields")
        state = TrustedState(**payload)
        _validate_trusted_state(state)
        return state
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VaultKeyUnavailableError("The vault trusted state is invalid.") from exc


def _validate_trusted_state(state: TrustedState) -> None:
    recovery_version = state.recovery_key_version
    if (
        not isinstance(state.database_id, str)
        or not state.database_id
        or not isinstance(state.active_key_version, int)
        or isinstance(state.active_key_version, bool)
        or state.active_key_version < 1
        or not isinstance(state.epoch, int)
        or isinstance(state.epoch, bool)
        or state.epoch < 0
        or not _is_hex_digest(state.manifest)
        or not _is_hex_digest(state.chain_head)
        or (
            recovery_version is not None
            and (
                not isinstance(recovery_version, int)
                or isinstance(recovery_version, bool)
                or recovery_version < 1
            )
        )
    ):
        raise VaultKeyUnavailableError("The vault trusted state is invalid.")


class NativeKeyringStore:
    """Versioned vault secrets backed only by an approved native keyring."""

    name = "os-native-keyring"

    def __init__(self, keyring_module=None) -> None:
        if keyring_module is None:
            try:
                import keyring as keyring_module
            except ImportError as exc:
                raise VaultKeyUnavailableError(
                    "Vault encryption requires the 'keyring' package."
                ) from exc
        self._keyring = keyring_module
        self._check_backend()

    @staticmethod
    def _key_account(project_id: str, version: int) -> str:
        if version < 1:
            raise ValueError("Vault key versions must be positive integers.")
        return f"vault:{project_id}:key:v{version}"

    @staticmethod
    def _integrity_account(project_id: str) -> str:
        return f"vault:{project_id}:integrity"

    @staticmethod
    def _state_account(project_id: str) -> str:
        return f"vault:{project_id}:state"

    @staticmethod
    def _legacy_account(project_id: str) -> str:
        return f"vault:{project_id}"

    def _check_backend(self) -> None:
        try:
            backend = self._keyring.get_keyring()
        except Exception as exc:
            raise VaultKeyUnavailableError(
                "The operating-system keyring backend is unavailable."
            ) from exc
        assert_backend_allowed(backend)

    def _get(self, account: str) -> str | None:
        self._check_backend()
        try:
            return self._keyring.get_password(KEYRING_SERVICE, account)
        except Exception as exc:
            raise VaultKeyUnavailableError("The operating-system keyring is unavailable.") from exc

    def _set(self, account: str, value: str) -> None:
        self._check_backend()
        try:
            self._keyring.set_password(KEYRING_SERVICE, account, value)
        except Exception as exc:
            raise VaultKeyUnavailableError(
                "Vault security material could not be stored in the operating-system keyring."
            ) from exc

    def _delete(self, account: str) -> None:
        if self._get(account) is None:
            return
        self._check_backend()
        try:
            self._keyring.delete_password(KEYRING_SERVICE, account)
        except Exception as exc:
            raise VaultKeyUnavailableError(
                "Vault security material could not be removed from the operating-system keyring."
            ) from exc

    def get_encryption_key(self, project_id: str, version: int) -> bytes | None:
        account = self._key_account(project_id, version)
        encoded = self._get(account)
        if encoded is None and version == 1:
            encoded = self._get(self._legacy_account(project_id))
            if encoded is not None:
                key = decode_key(encoded)
                self._set(account, encode_key(key))
                return key
        return None if encoded is None else decode_key(encoded)

    def set_encryption_key(self, project_id: str, version: int, key: bytes) -> None:
        self._set(self._key_account(project_id, version), encode_key(key))

    def delete_encryption_key(self, project_id: str, version: int) -> None:
        self._delete(self._key_account(project_id, version))

    def get_integrity_key(self, project_id: str) -> bytes | None:
        encoded = self._get(self._integrity_account(project_id))
        return None if encoded is None else decode_key(encoded)

    def set_integrity_key(self, project_id: str, key: bytes) -> None:
        self._set(self._integrity_account(project_id), encode_key(key))

    def delete_integrity_key(self, project_id: str) -> None:
        self._delete(self._integrity_account(project_id))

    def get_trusted_state(self, project_id: str) -> TrustedState | None:
        encoded = self._get(self._state_account(project_id))
        return None if encoded is None else decode_trusted_state(encoded)

    def set_trusted_state(self, project_id: str, state: TrustedState) -> None:
        self._set(self._state_account(project_id), encode_trusted_state(state))

    def delete_trusted_state(self, project_id: str) -> None:
        self._delete(self._state_account(project_id))


def resolve_encryption_key(
    store: SecureKeyStore,
    project_id: str,
    version: int,
    *,
    encrypted_data_exists: bool,
) -> bytes:
    """Load a versioned key, creating it only for a vault without ciphertext."""
    key = store.get_encryption_key(project_id, version)
    if key is not None:
        if len(key) != KEY_SIZE:
            raise VaultKeyUnavailableError("The vault key has an invalid size.")
        return key
    if encrypted_data_exists:
        raise VaultKeyMissingError(
            "This vault contains encrypted data, but its OS keyring key is missing. "
            "Restore the original key before opening the vault."
        )
    key = secrets.token_bytes(KEY_SIZE)
    store.set_encryption_key(project_id, version, key)
    return key
