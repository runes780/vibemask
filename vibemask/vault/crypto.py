"""Authenticated encryption and OS keyring support for the VibeMask vault."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


CIPHERTEXT_PREFIX = "vmenc:v1:"
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

    def __init__(self, key: bytes, project_id: str) -> None:
        if len(key) != KEY_SIZE:
            raise ValueError("Vault keys must be exactly 32 bytes.")
        self._aesgcm = AESGCM(key)
        self._lookup_key = hmac.new(key, b"vibemask:v1:lookup", hashlib.sha256).digest()
        self._project_id = project_id

    @staticmethod
    def is_encrypted(value: str | None) -> bool:
        return isinstance(value, str) and value.startswith(CIPHERTEXT_PREFIX)

    def _aad(self, context: str) -> bytes:
        return f"vibemask:v1:{self._project_id}:{context}".encode("utf-8")

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = secrets.token_bytes(NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), self._aad(context))
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return CIPHERTEXT_PREFIX + payload

    def lookup_digest(self, plaintext: str, context: str) -> str:
        """Return a keyed equality token without exposing a raw PII hash."""
        message = f"{self._project_id}:{context}\0{plaintext}".encode("utf-8")
        return "vmhmac:v1:" + hmac.new(self._lookup_key, message, hashlib.sha256).hexdigest()

    def decrypt(self, value: str, context: str) -> str:
        if not self.is_encrypted(value):
            raise VaultDecryptionError("Refusing to decrypt an unversioned vault value.")
        encoded = value[len(CIPHERTEXT_PREFIX) :]
        try:
            payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
            if len(payload) <= NONCE_SIZE:
                raise ValueError("ciphertext payload is too short")
            nonce, ciphertext = payload[:NONCE_SIZE], payload[NONCE_SIZE:]
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, self._aad(context))
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeError) as exc:
            raise VaultDecryptionError(
                "Vault ciphertext authentication failed; the key or stored data is invalid."
            ) from exc
