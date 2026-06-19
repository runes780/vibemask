"""Global test isolation: never access a developer's real OS keyring."""

import pytest


class InMemoryVaultKeyProvider:
    name = "memory-test-keyring"

    def __init__(self) -> None:
        self.keys: dict[str, bytes] = {}

    def get_key(self, project_id: str) -> bytes | None:
        return self.keys.get(project_id)

    def set_key(self, project_id: str, key: bytes) -> None:
        self.keys[project_id] = key


@pytest.fixture(autouse=True)
def isolate_vault_keyring(monkeypatch: pytest.MonkeyPatch):
    """Use one in-memory keyring per test, shared by all VaultStorage instances."""
    from vibemask.vault import storage

    provider = InMemoryVaultKeyProvider()
    monkeypatch.setattr(storage, "default_key_provider", lambda: provider)
    return provider
