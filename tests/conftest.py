"""Global test isolation: never access a developer's real OS keyring."""

import pytest

from vibemask.vault.keyring_policy import TrustedState


class InMemoryVaultKeyStore:
    name = "memory-test-keyring"

    def __init__(self) -> None:
        self.keys: dict[tuple[str, int], bytes] = {}
        self.integrity_keys: dict[str, bytes] = {}
        self.states: dict[str, TrustedState] = {}

    def get_encryption_key(self, project_id: str, version: int) -> bytes | None:
        return self.keys.get((project_id, version))

    def set_encryption_key(self, project_id: str, version: int, key: bytes) -> None:
        self.keys[(project_id, version)] = key

    def delete_encryption_key(self, project_id: str, version: int) -> None:
        self.keys.pop((project_id, version), None)

    def get_integrity_key(self, project_id: str) -> bytes | None:
        return self.integrity_keys.get(project_id)

    def set_integrity_key(self, project_id: str, key: bytes) -> None:
        self.integrity_keys[project_id] = key

    def delete_integrity_key(self, project_id: str) -> None:
        self.integrity_keys.pop(project_id, None)

    def get_trusted_state(self, project_id: str) -> TrustedState | None:
        return self.states.get(project_id)

    def set_trusted_state(self, project_id: str, state: TrustedState) -> None:
        self.states[project_id] = state

    def delete_trusted_state(self, project_id: str) -> None:
        self.states.pop(project_id, None)


@pytest.fixture(autouse=True)
def isolate_vault_keyring(monkeypatch: pytest.MonkeyPatch):
    """Use one in-memory keyring per test, shared by all VaultStorage instances."""
    from vibemask.vault import storage

    store = InMemoryVaultKeyStore()
    monkeypatch.setattr(storage, "default_key_store", lambda: store)
    return store
