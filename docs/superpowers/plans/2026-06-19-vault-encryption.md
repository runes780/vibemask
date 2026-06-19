# Vault Encryption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encrypt all reversible PII in the SQLite vault with AES-256-GCM and keep per-project keys in the operating-system keyring, including safe migration of legacy plaintext vaults.

**Architecture:** A focused `vibemask.vault.crypto` module owns authenticated encryption and key-provider behavior. `VaultStorage` remains the public boundary, encrypting before SQL writes and decrypting after reads; its initialization performs an idempotent transaction migration and fails closed when a key is missing or unavailable.

**Tech Stack:** Python 3.10+, SQLite, `cryptography` AESGCM, `keyring`, pytest.

---

### Task 1: Authenticated encryption primitive

**Files:**
- Create: `vibemask/vault/crypto.py`
- Create: `tests/test_vault_encryption.py`

- [ ] Write tests for randomized AES-GCM ciphertext, roundtrip, wrong-key rejection, tamper rejection, and keyring missing-key behavior.
- [ ] Run `pytest tests/test_vault_encryption.py -v` and confirm failure because the module does not exist.
- [ ] Implement `VaultCipher`, `KeyringKeyProvider`, and explicit security exceptions with `vmenc:v1:` ciphertext envelopes.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Encrypted VaultStorage boundary

**Files:**
- Modify: `vibemask/vault/storage.py`
- Modify: `tests/test_vault_encryption.py`
- Create: `tests/conftest.py`

- [ ] Add failing tests proving mapping and session SQLite bytes contain no synthetic PII while public APIs still roundtrip normal values.
- [ ] Add an autouse in-memory key provider fixture so no test accesses the real OS keyring.
- [ ] Run the focused tests and confirm plaintext-at-rest assertions fail.
- [ ] Initialize a project cipher in `VaultStorage`, encrypt sensitive fields on writes, and decrypt them on reads.
- [ ] Run focused and existing Vault tests.

### Task 3: Transactional legacy migration

**Files:**
- Modify: `vibemask/vault/storage.py`
- Modify: `tests/test_vault_encryption.py`

- [ ] Add failing tests for legacy plaintext migration, idempotence, rollback, and missing-key failure for encrypted rows.
- [ ] Run the migration tests and confirm the expected failures.
- [ ] Add `vault_meta`, encrypted-row detection, key acquisition rules, and one-transaction migration.
- [ ] Run the migration tests and confirm they pass.

### Task 4: CLI status, dependencies, and documentation

**Files:**
- Modify: `vibemask/cli.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `RPd.md`
- Modify: `tests/test_vault_encryption.py`

- [ ] Add a failing test for safe encryption status metadata.
- [ ] Expose algorithm, key provider, and encryption version through `get_stats()` and `vibemask status`.
- [ ] Move `cryptography` and `keyring` into default dependencies and document fail-closed behavior and key-loss consequences.
- [ ] Run focused tests.

### Task 5: Full verification

**Files:**
- Verify all files above without modifying unrelated detection work.

- [ ] Run `HOME=/tmp/vibemask-test-home pytest tests/ -v`.
- [ ] Run `ruff check` on newly modified Vault and test files.
- [ ] Run `git diff --check`.
- [ ] Search the temporary test SQLite file for the synthetic PII marker and confirm it is absent.
- [ ] Review `git diff` and report any verification blocked by the sandbox.
