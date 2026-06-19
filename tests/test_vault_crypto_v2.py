import base64

import pytest

import vibemask.vault.crypto as crypto
from vibemask.vault.crypto import (
    CIPHERTEXT_PREFIX,
    VaultCipher,
    VaultDecryptionError,
)


def test_v2_ciphertext_binds_key_version_and_context():
    cipher = VaultCipher(b"a" * 32, "project", key_version=3)

    first = cipher.encrypt("SYNTHETIC", "mappings:row:original_text")
    second = cipher.encrypt("SYNTHETIC", "mappings:row:original_text")

    assert first.startswith("vmenc:v2:3:")
    assert first != second
    assert crypto.parse_envelope(first).key_version == 3
    assert cipher.decrypt(first, "mappings:row:original_text") == "SYNTHETIC"
    assert CIPHERTEXT_PREFIX == "vmenc:v2:"


def test_v2_cipher_rejects_wrong_key_version_and_context():
    value = VaultCipher(b"a" * 32, "project", key_version=2).encrypt(
        "SYNTHETIC", "mappings:row:original_text"
    )

    with pytest.raises(VaultDecryptionError):
        VaultCipher(b"a" * 32, "project", key_version=1).decrypt(
            value, "mappings:row:original_text"
        )
    with pytest.raises(VaultDecryptionError):
        VaultCipher(b"a" * 32, "project", key_version=2).decrypt(
            value, "mappings:other:original_text"
        )


def test_v2_lookup_digest_is_versioned_and_domain_separated():
    first = VaultCipher(b"a" * 32, "project", key_version=1)
    second = VaultCipher(b"a" * 32, "project", key_version=2)

    digest1 = first.lookup_digest("SYNTHETIC", "mapping-original")
    digest2 = second.lookup_digest("SYNTHETIC", "mapping-original")

    assert digest1.startswith("vmhmac:v2:1:")
    assert digest2.startswith("vmhmac:v2:2:")
    assert digest1 != digest2
    assert digest1 != first.lookup_digest("SYNTHETIC", "other-domain")


def test_v1_ciphertext_remains_readable():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = b"v" * 32
    project_id = "legacy-project"
    context = "mappings:legacy:original_text"
    nonce = b"n" * 12
    aad = f"vibemask:v1:{project_id}:{context}".encode("utf-8")
    encrypted = AESGCM(key).encrypt(nonce, b"SYNTHETIC-LEGACY", aad)
    value = "vmenc:v1:" + base64.urlsafe_b64encode(nonce + encrypted).decode()

    envelope = crypto.parse_envelope(value)

    assert envelope.version == 1
    assert envelope.key_version == 1
    assert VaultCipher(key, project_id, key_version=1).decrypt(value, context) == (
        "SYNTHETIC-LEGACY"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "vmenc:v1:",
        "vmenc:v1:not!base64",
        "vmenc:v2:",
        "vmenc:v2:0:AA==",
        "vmenc:v2:01:AA==",
        "vmenc:v2:x:AA==",
        "vmenc:v2:1:not!base64",
        "vmenc:v3:1:AA==",
        " vmenc:v2:1:AA==",
    ],
)
def test_envelope_parser_rejects_malformed_or_unknown_values(value):
    with pytest.raises(VaultDecryptionError):
        crypto.parse_envelope(value)


def test_v2_cipher_rejects_tampered_payload():
    cipher = VaultCipher(b"t" * 32, "project", key_version=4)
    value = cipher.encrypt("SYNTHETIC", "sessions:s1:mappings")
    envelope = crypto.parse_envelope(value)
    payload = bytearray(envelope.payload)
    payload[-1] ^= 1
    tampered = "vmenc:v2:4:" + base64.urlsafe_b64encode(payload).decode()

    with pytest.raises(VaultDecryptionError):
        cipher.decrypt(tampered, "sessions:s1:mappings")
