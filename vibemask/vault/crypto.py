"""Authenticated encryption and OS keyring support for the VibeMask vault."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


LEGACY_CIPHERTEXT_PREFIX = "vmenc:v1:"
CIPHERTEXT_PREFIX = "vmenc:v2:"
KEYRING_SERVICE = "vibemask"
KEYRING_ACCOUNT_PREFIX = "vault:"
KEY_SIZE = 32
NONCE_SIZE = 12


class VaultSecurityError(RuntimeError):
    """Base class for fail-closed vault security errors."""


class VaultKeyUnavailableError(VaultSecurityError):
    """The configured secure key store could not be used."""


class VaultKeyMissingError(VaultSecurityError):
    """Encrypted vault data exists but its key is missing."""


class VaultDecryptionError(VaultSecurityError):
    """Ciphertext authentication or decoding failed."""


@dataclass(frozen=True)
class CiphertextEnvelope:
    """Strictly parsed, versioned vault ciphertext envelope."""

    version: int
    key_version: int
    payload: bytes


def parse_envelope(value: str) -> CiphertextEnvelope:
    """Parse a supported ciphertext envelope without accepting ambiguous input."""
    try:
        if not isinstance(value, str):
            raise ValueError("ciphertext is not text")
        if value.startswith(LEGACY_CIPHERTEXT_PREFIX):
            version = 1
            key_version = 1
            encoded = value[len(LEGACY_CIPHERTEXT_PREFIX) :]
        elif value.startswith(CIPHERTEXT_PREFIX):
            parts = value.split(":", 3)
            if len(parts) != 4 or parts[0:2] != ["vmenc", "v2"]:
                raise ValueError("invalid ciphertext prefix")
            key_text = parts[2]
            key_version = int(key_text)
            if key_version < 1 or str(key_version) != key_text:
                raise ValueError("invalid key version")
            version = 2
            encoded = parts[3]
        else:
            raise ValueError("unsupported ciphertext version")

        payload = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
        if len(payload) < NONCE_SIZE + 16:
            raise ValueError("ciphertext payload is too short")
        return CiphertextEnvelope(version, key_version, payload)
    except (binascii.Error, TypeError, ValueError, UnicodeError) as exc:
        raise VaultDecryptionError("Vault ciphertext envelope is invalid.") from exc


class KeyProvider(Protocol):
    """Minimal key-store interface used by :class:`VaultStorage`."""

    name: str

    def get_key(self, project_id: str) -> bytes | None: ...

    def set_key(self, project_id: str, key: bytes) -> None: ...


class KeyringKeyProvider:
    """Store one independent vault key per project in the OS keyring."""

    name = "os-keyring"

    def __init__(self, keyring_module=None) -> None:
        if keyring_module is None:
            try:
                import keyring as keyring_module
            except ImportError as exc:
                raise VaultKeyUnavailableError(
                    "Vault encryption requires the 'keyring' package."
                ) from exc
        self._keyring = keyring_module

    @staticmethod
    def _account(project_id: str) -> str:
        return f"{KEYRING_ACCOUNT_PREFIX}{project_id}"

    def get_key(self, project_id: str) -> bytes | None:
        try:
            encoded = self._keyring.get_password(KEYRING_SERVICE, self._account(project_id))
        except Exception as exc:
            raise VaultKeyUnavailableError("The operating-system keyring is unavailable.") from exc
        if encoded is None:
            return None
        try:
            key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise VaultKeyUnavailableError("The vault key stored in the keyring is invalid.") from exc
        if len(key) != KEY_SIZE:
            raise VaultKeyUnavailableError("The vault key stored in the keyring has an invalid size.")
        return key

    def set_key(self, project_id: str, key: bytes) -> None:
        if len(key) != KEY_SIZE:
            raise ValueError("Vault keys must be exactly 32 bytes.")
        encoded = base64.urlsafe_b64encode(key).decode("ascii")
        try:
            self._keyring.set_password(KEYRING_SERVICE, self._account(project_id), encoded)
        except Exception as exc:
            raise VaultKeyUnavailableError(
                "The vault key could not be stored in the operating-system keyring."
            ) from exc


def resolve_vault_key(
    provider: KeyProvider,
    project_id: str,
    *,
    encrypted_data_exists: bool,
) -> bytes:
    """Load an existing project key or create one only when it is safe."""
    key = provider.get_key(project_id)
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
    provider.set_key(project_id, key)
    return key


class VaultCipher:
    """AES-256-GCM field encryption scoped to one project."""

    algorithm = "AES-256-GCM"

    def __init__(self, key: bytes, project_id: str, key_version: int = 1) -> None:
        if len(key) != KEY_SIZE:
            raise ValueError("Vault keys must be exactly 32 bytes.")
        if key_version < 1:
            raise ValueError("Vault key versions must be positive integers.")
        self._aesgcm = AESGCM(key)
        self._lookup_key = hmac.new(
            key,
            f"vibemask:v2:lookup:{key_version}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        self._project_id = project_id
        self.key_version = key_version

    @staticmethod
    def is_encrypted(value: str | None) -> bool:
        return isinstance(value, str) and value.startswith(
            (LEGACY_CIPHERTEXT_PREFIX, CIPHERTEXT_PREFIX)
        )

    def _aad(self, context: str, envelope_version: int = 2) -> bytes:
        if envelope_version == 1:
            return f"vibemask:v1:{self._project_id}:{context}".encode("utf-8")
        return (
            f"vibemask:v2\0{self._project_id}\0{context}\0{self.key_version}".encode(
                "utf-8"
            )
        )

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = secrets.token_bytes(NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), self._aad(context))
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return f"{CIPHERTEXT_PREFIX}{self.key_version}:{payload}"

    def lookup_digest(self, plaintext: str, context: str) -> str:
        """Return a keyed equality token without exposing a raw PII hash."""
        message = f"{self._project_id}:{context}\0{plaintext}".encode("utf-8")
        digest = hmac.new(self._lookup_key, message, hashlib.sha256).hexdigest()
        return f"vmhmac:v2:{self.key_version}:{digest}"

    def decrypt(self, value: str, context: str) -> str:
        envelope = parse_envelope(value)
        if envelope.key_version != self.key_version:
            raise VaultDecryptionError("Vault ciphertext requires another key version.")
        try:
            nonce = envelope.payload[:NONCE_SIZE]
            ciphertext = envelope.payload[NONCE_SIZE:]
            plaintext = self._aesgcm.decrypt(
                nonce,
                ciphertext,
                self._aad(context, envelope_version=envelope.version),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeError) as exc:
            raise VaultDecryptionError(
                "Vault ciphertext authentication failed; the key or stored data is invalid."
            ) from exc
