import importlib
import json

import pytest

from vibemask.vault.crypto import VaultKeyUnavailableError


def policy_module():
    return importlib.import_module("vibemask.vault.keyring_policy")


class FakeKeyringModule:
    def __init__(self, backend_module: str = "keyring.backends.macOS"):
        self.backend = type("Keyring", (), {"__module__": backend_module})()
        self.values: dict[tuple[str, str], str] = {}
        self.deleted: list[tuple[str, str]] = []

    def get_keyring(self):
        return self.backend

    def get_password(self, service: str, account: str):
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str):
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str):
        self.deleted.append((service, account))
        self.values.pop((service, account), None)


@pytest.mark.parametrize(
    "module",
    [
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
    ],
)
def test_native_backends_are_allowed(module):
    policy_module().assert_backend_allowed(type("Keyring", (), {"__module__": module})())


@pytest.mark.parametrize(
    "module",
    [
        "keyring.backends.fail",
        "keyring.backends.null",
        "keyrings.alt.file",
        "keyring.backends.chainer",
        "vendor.unknown",
    ],
)
def test_non_native_backends_fail_closed(module):
    with pytest.raises(VaultKeyUnavailableError, match="approved native"):
        policy_module().assert_backend_allowed(
            type("Keyring", (), {"__module__": module})()
        )


def test_versioned_keys_integrity_and_state_use_separate_accounts():
    policy = policy_module()
    keyring = FakeKeyringModule()
    store = policy.NativeKeyringStore(keyring_module=keyring)
    state = policy.TrustedState(
        database_id="db-1",
        active_key_version=2,
        epoch=7,
        manifest="a" * 64,
        chain_head="b" * 64,
        recovery_key_version=1,
    )

    store.set_encryption_key("project", 2, b"e" * 32)
    store.set_integrity_key("project", b"i" * 32)
    store.set_trusted_state("project", state)

    assert store.get_encryption_key("project", 2) == b"e" * 32
    assert store.get_integrity_key("project") == b"i" * 32
    assert store.get_trusted_state("project") == state
    assert ("vibemask", "vault:project:key:v2") in keyring.values
    assert ("vibemask", "vault:project:integrity") in keyring.values
    assert ("vibemask", "vault:project:state") in keyring.values


def test_legacy_key_is_copied_to_versioned_v1_without_deletion():
    policy = policy_module()
    keyring = FakeKeyringModule()
    legacy = policy.encode_key(b"l" * 32)
    keyring.values[("vibemask", "vault:project")] = legacy
    store = policy.NativeKeyringStore(keyring_module=keyring)

    assert store.get_encryption_key("project", 1) == b"l" * 32
    assert keyring.values[("vibemask", "vault:project:key:v1")] == legacy
    assert keyring.values[("vibemask", "vault:project")] == legacy
    assert keyring.deleted == []


@pytest.mark.parametrize("encoded", ["not!base64", "YQ=="])
def test_invalid_stored_key_fails_closed(encoded):
    policy = policy_module()
    keyring = FakeKeyringModule()
    keyring.values[("vibemask", "vault:project:key:v1")] = encoded
    store = policy.NativeKeyringStore(keyring_module=keyring)

    with pytest.raises(VaultKeyUnavailableError, match="invalid"):
        store.get_encryption_key("project", 1)


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        json.dumps({"database_id": "db"}),
        json.dumps(
            {
                "database_id": "db",
                "active_key_version": 0,
                "epoch": -1,
                "manifest": "x",
                "chain_head": "y",
                "recovery_key_version": None,
            }
        ),
    ],
)
def test_invalid_trusted_state_fails_closed(raw):
    policy = policy_module()
    keyring = FakeKeyringModule()
    keyring.values[("vibemask", "vault:project:state")] = raw
    store = policy.NativeKeyringStore(keyring_module=keyring)

    with pytest.raises(VaultKeyUnavailableError, match="trusted state"):
        store.get_trusted_state("project")


def test_backend_is_checked_before_every_operation():
    policy = policy_module()
    keyring = FakeKeyringModule()
    store = policy.NativeKeyringStore(keyring_module=keyring)
    store.set_encryption_key("project", 1, b"k" * 32)
    keyring.backend = type("Keyring", (), {"__module__": "keyrings.alt.file"})()

    with pytest.raises(VaultKeyUnavailableError, match="approved native"):
        store.get_encryption_key("project", 1)


def test_delete_missing_key_is_idempotent():
    policy = policy_module()
    store = policy.NativeKeyringStore(keyring_module=FakeKeyringModule())

    store.delete_encryption_key("project", 9)
