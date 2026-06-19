# Production Vault Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn VibeMask's encrypted local SQLite vault into a fail-closed, recoverable, rotatable, tamper- and rollback-detecting single-user production vault.

**Architecture:** Keep field-level AES-256-GCM and introduce a v2 envelope whose AAD binds every ciphertext to its project, row, field, and key version. Separate native keyring policy, recovery bundles, audit integrity, and cross-process locking into small modules; `VaultStorage` orchestrates those pieces with one integrity checkpoint per write transaction or masking batch.

**Tech Stack:** Python 3.10+, SQLite, `cryptography` AESGCM/Scrypt, `keyring`, Typer, pytest, Ruff, standard-library `fcntl`/`msvcrt` locking.

---

## File map

- Create `vibemask/vault/keyring_policy.py`: native-backend allowlist, versioned encryption keys, integrity key, and trusted state.
- Create `vibemask/vault/integrity.py`: canonical database manifest, HMAC audit entries, and rollback/tamper decisions.
- Create `vibemask/vault/locking.py`: exclusive cross-process lock with timeout.
- Create `vibemask/vault/recovery.py`: authenticated Scrypt recovery bundle parser and atomic private writer.
- Modify `vibemask/vault/crypto.py`: strict v1/v2 envelope parsing and version-bound AES-GCM operations.
- Modify `vibemask/vault/storage.py`: secure connection policy, schema v2, migration, verified writes, batches, backup/restore, rotation, and status.
- Modify `vibemask/cli.py`: `vault` command group and safe operator output.
- Modify masking call sites in `vibemask/cli.py`, `vibemask/core/ooxml.py`, `vibemask/core/office.py`, and `vibemask/web/api.py`: wrap mapping/session writes in one batch transaction.
- Modify `tests/conftest.py`: full in-memory secure-key-store fixture, never a real keyring.
- Create `tests/test_vault_crypto_v2.py`, `tests/test_vault_keyring_policy.py`, `tests/test_vault_integrity.py`, `tests/test_vault_locking.py`, `tests/test_vault_recovery.py`, `tests/test_vault_rotation.py`, and `tests/test_vault_cli_security.py`.
- Modify `tests/test_vault_encryption.py`: v1 migration compatibility and v2 at-rest assertions.
- Create `scripts/vault_keyring_smoke.py`: opt-in native platform smoke test using an isolated synthetic project/account.
- Create `.github/workflows/security.yml`: dependency audit, SBOM artifact, tests, and Ruff release gates.
- Modify `pyproject.toml`, `README.md`, and `docs/ROADMAP.md`: security tooling and operational documentation.

### Task 1: Versioned ciphertext envelopes

**Files:**
- Modify: `vibemask/vault/crypto.py`
- Create: `tests/test_vault_crypto_v2.py`

- [ ] **Step 1: Write failing tests for v2 roundtrip, key version, strict parsing, wrong AAD, tamper, and v1 compatibility**

```python
def test_v2_ciphertext_binds_key_version_and_context():
    cipher = VaultCipher(b"a" * 32, "project", key_version=3)
    value = cipher.encrypt("SYNTHETIC", "mappings:row:original_text")
    assert value.startswith("vmenc:v2:3:")
    assert parse_envelope(value).key_version == 3
    assert cipher.decrypt(value, "mappings:row:original_text") == "SYNTHETIC"

@pytest.mark.parametrize("value", ["", "vmenc:v2:", "vmenc:v2:x:AA==", "vmenc:v3:1:AA=="])
def test_envelope_parser_rejects_malformed_or_unknown_values(value):
    with pytest.raises(VaultDecryptionError):
        parse_envelope(value)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_crypto_v2.py -v`

Expected: FAIL because `parse_envelope` and v2 `key_version` support do not exist.

- [ ] **Step 3: Implement strict v1/v2 envelopes**

```python
@dataclass(frozen=True)
class CiphertextEnvelope:
    version: int
    key_version: int
    payload: bytes

def parse_envelope(value: str) -> CiphertextEnvelope:
    try:
        if value.startswith("vmenc:v1:"):
            version, key_version, encoded = 1, 1, value.removeprefix("vmenc:v1:")
        elif value.startswith("vmenc:v2:"):
            prefix, version_text, key_text, encoded = value.split(":", 3)
            if prefix != "vmenc" or version_text != "v2":
                raise ValueError("invalid prefix")
            version, key_version = 2, int(key_text)
            if key_version < 1 or str(key_version) != key_text:
                raise ValueError("invalid key version")
        else:
            raise ValueError("unknown envelope")
        payload = base64.b64decode(encoded, altchars=b"-_", validate=True)
        if len(payload) < NONCE_SIZE + 16:
            raise ValueError("short payload")
        return CiphertextEnvelope(version, key_version, payload)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise VaultDecryptionError("Vault ciphertext envelope is invalid.") from exc

class VaultCipher:
    def __init__(self, key: bytes, project_id: str, key_version: int = 1):
        if len(key) != KEY_SIZE or key_version < 1:
            raise ValueError("Invalid vault key or key version.")
        self._aesgcm = AESGCM(key)
        self._project_id = project_id
        self.key_version = key_version

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = secrets.token_bytes(NONCE_SIZE)
        aad = f"vibemask:v2\0{self._project_id}\0{context}\0{self.key_version}".encode()
        payload = nonce + self._aesgcm.encrypt(nonce, plaintext.encode(), aad)
        return f"vmenc:v2:{self.key_version}:" + base64.urlsafe_b64encode(payload).decode()

    def decrypt(self, value: str, context: str) -> str:
        envelope = parse_envelope(value)
        if envelope.key_version != self.key_version:
            raise VaultDecryptionError("Vault ciphertext requires another key version.")
        if envelope.version == 1:
            aad = f"vibemask:v1:{self._project_id}:{context}".encode()
        else:
            aad = f"vibemask:v2\0{self._project_id}\0{context}\0{self.key_version}".encode()
        try:
            return self._aesgcm.decrypt(
                envelope.payload[:NONCE_SIZE], envelope.payload[NONCE_SIZE:], aad
            ).decode()
        except (InvalidTag, UnicodeError) as exc:
            raise VaultDecryptionError("Vault ciphertext authentication failed.") from exc
```

Use v2 AAD `vibemask:v2\0<project_id>\0<context>\0<key_version>` and retain the exact existing v1 AAD for compatibility. Domain-separate lookup tokens as `vmhmac:v2:<key_version>:`.

- [ ] **Step 4: Run focused and legacy encryption tests**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_crypto_v2.py tests/test_vault_encryption.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vibemask/vault/crypto.py tests/test_vault_crypto_v2.py tests/test_vault_encryption.py
git commit -m "feat: add versioned vault ciphertext envelopes"
```

### Task 2: Native keyring policy and versioned key material

**Files:**
- Create: `vibemask/vault/keyring_policy.py`
- Modify: `vibemask/vault/crypto.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_vault_keyring_policy.py`

- [ ] **Step 1: Write failing tests for allowed and rejected backends and versioned accounts**

```python
@pytest.mark.parametrize("module", [
    "keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService"
])
def test_native_backends_are_allowed(module):
    assert_backend_allowed(type("Keyring", (), {"__module__": module})())

@pytest.mark.parametrize("module", [
    "keyring.backends.fail", "keyring.backends.null", "keyrings.alt.file", "vendor.unknown"
])
def test_non_native_backends_fail_closed(module):
    with pytest.raises(VaultKeyUnavailableError):
        assert_backend_allowed(type("Keyring", (), {"__module__": module})())
```

Also assert exact account names `vault:<project>:key:v2`, `vault:<project>:integrity`, and `vault:<project>:state`; invalid base64/length/JSON must fail closed; a legacy `vault:<project>` key is copied to versioned v1 without deletion.

- [ ] **Step 2: Run the tests and verify RED**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_keyring_policy.py -v`

Expected: FAIL because `keyring_policy.py` does not exist.

- [ ] **Step 3: Implement the high-level secure key store**

```python
@dataclass(frozen=True)
class TrustedState:
    database_id: str
    active_key_version: int
    epoch: int
    manifest: str
    chain_head: str
    recovery_key_version: int | None = None

class SecureKeyStore(Protocol):
    name: str
    def get_encryption_key(self, project_id: str, version: int) -> bytes | None:
        raise NotImplementedError
    def set_encryption_key(self, project_id: str, version: int, key: bytes) -> None:
        raise NotImplementedError
    def delete_encryption_key(self, project_id: str, version: int) -> None:
        raise NotImplementedError
    def get_integrity_key(self, project_id: str) -> bytes | None:
        raise NotImplementedError
    def set_integrity_key(self, project_id: str, key: bytes) -> None:
        raise NotImplementedError
    def get_trusted_state(self, project_id: str) -> TrustedState | None:
        raise NotImplementedError
    def set_trusted_state(self, project_id: str, state: TrustedState) -> None:
        raise NotImplementedError
```

`NativeKeyringStore` must call `keyring.get_keyring()`, enforce the module allowlist before any read/write, encode binary keys with URL-safe base64, validate 32-byte keys, and convert all backend failures into `VaultKeyUnavailableError` without embedding secret values.

- [ ] **Step 4: Replace the test fixture with an explicit in-memory `SecureKeyStore` and run tests**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_keyring_policy.py tests/test_vault_encryption.py -v`

Expected: PASS, with no native keyring calls.

- [ ] **Step 5: Commit**

```bash
git add vibemask/vault/keyring_policy.py vibemask/vault/crypto.py tests/conftest.py tests/test_vault_keyring_policy.py tests/test_vault_encryption.py
git commit -m "feat: enforce native vault keyring policy"
```

### Task 3: Cross-process lock and hardened SQLite connections

**Files:**
- Create: `vibemask/vault/locking.py`
- Modify: `vibemask/vault/storage.py`
- Create: `tests/test_vault_locking.py`

- [ ] **Step 1: Write failing contention and timeout tests**

```python
def test_second_process_cannot_acquire_vault_lock(tmp_path):
    lock_path = tmp_path / "vault.lock"
    with VaultFileLock(lock_path, timeout=0.2):
        result = subprocess.run(
            [sys.executable, "-c", CHILD_LOCK_SCRIPT, str(lock_path)],
            text=True, capture_output=True, check=False,
        )
    assert result.returncode == 2
    assert "timed out" in result.stderr.lower()
```

Also test lock-file mode `0600` on POSIX and secure connection PRAGMAs: `journal_mode=delete`, `secure_delete=1`, `busy_timeout>=5000`, `foreign_keys=1`.

- [ ] **Step 2: Run tests and verify RED**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_locking.py -v`

Expected: FAIL because locking and `_connect()` are absent.

- [ ] **Step 3: Implement lock and one connection factory**

```python
class VaultFileLock:
    def __enter__(self):
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    raise VaultLockTimeout("Vault lock acquisition timed out.") from exc
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        try:
            if os.name == "nt":
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)

def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self.vault_path, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")
    conn.execute("PRAGMA journal_mode = DELETE")
    return conn
```

All schema initialization, migration, writes, recovery, and rotation acquire `<vault.sqlite>.lock`. Reject unexpected non-empty `-wal`/`-shm` sidecars before exclusive mutation.

- [ ] **Step 4: Run focused and full Vault tests**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_locking.py tests/test_vault_encryption.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vibemask/vault/locking.py vibemask/vault/storage.py tests/test_vault_locking.py
git commit -m "feat: serialize vault writes across processes"
```

### Task 4: Logical integrity manifest and rollback detection

**Files:**
- Create: `vibemask/vault/integrity.py`
- Modify: `vibemask/vault/storage.py`
- Create: `tests/test_vault_integrity.py`

- [ ] **Step 1: Write failing tamper, deletion, audit truncation, replay, rollback, and crash-window tests**

```python
def test_completed_database_rollback_is_rejected(vault_fixture):
    vault, store = vault_fixture
    snapshot = vault.vault_path.read_bytes()
    vault.get_or_create_mapping("SYNTHETIC-B", "PERSON", "{{PERSON_2}}")
    vault.vault_path.write_bytes(snapshot)
    with pytest.raises(VaultRollbackError):
        VaultStorage(vault.project_path, key_store=store)

def test_database_ahead_of_keyring_recovers_valid_commit(vault_fixture):
    vault, store = vault_fixture
    old_state = store.get_trusted_state(vault.project_id)
    vault.get_or_create_mapping("SYNTHETIC-B", "PERSON", "{{PERSON_2}}")
    store.set_trusted_state(vault.project_id, old_state)
    reopened = VaultStorage(vault.project_path, key_store=store)
    assert store.get_trusted_state(vault.project_id).epoch == reopened.security_status()["epoch"]
```

Direct SQLite mutation tests must change a ciphertext, delete a row, replace `stats`, truncate `vault_audit`, and replay a prior audit row; every case must raise `VaultTamperError` before any plaintext is returned.

- [ ] **Step 2: Run tests and verify RED**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_integrity.py -v`

Expected: FAIL because manifests and trusted epochs do not exist.

- [ ] **Step 3: Implement canonical manifest and audit chain**

```python
def compute_manifest(conn: sqlite3.Connection) -> str:
    payload = {
        "mappings": conn.execute(MAPPING_MANIFEST_QUERY).fetchall(),
        "sessions": conn.execute(SESSION_MANIFEST_QUERY).fetchall(),
        "meta": conn.execute(SECURITY_META_QUERY).fetchall(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

def make_audit_head(key: bytes, epoch: int, previous: str, manifest: str, operation: str) -> str:
    message = json.dumps([epoch, previous, manifest, operation], separators=(",", ":")).encode()
    return hmac.new(key, b"vibemask:audit:v1\0" + message, hashlib.sha256).hexdigest()
```

Add `vault_audit(epoch INTEGER PRIMARY KEY, previous_head, manifest, operation, head)` and metadata `database_id`, `active_key_version`, and `integrity_epoch`. Bootstrap an existing pre-integrity vault only when no trusted state exists and no audit history claims otherwise. On every open, recompute the manifest, validate the complete audit chain, compare it to `TrustedState`, and apply the four decisions in the design specification.

- [ ] **Step 4: Route every mutating storage method through one verified write helper**

```python
def _verified_write(self, operation: str, callback):
    with self._lock:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        result = callback(conn)
        pending_state = append_integrity_checkpoint(conn, self._integrity_key, operation)
        conn.commit()
        self.key_store.set_trusted_state(self.project_id, pending_state)
        return result
```

Keyring checkpoint failure after SQLite commit must be surfaced; reopening must validate the signed continuation and repair the trusted state. Failure before commit must leave both epoch and manifest unchanged.

- [ ] **Step 5: Run focused tests and the existing Vault suite**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_integrity.py tests/test_vault_encryption.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add vibemask/vault/integrity.py vibemask/vault/storage.py tests/test_vault_integrity.py tests/test_vault_encryption.py
git commit -m "feat: detect vault tampering and rollback"
```

### Task 5: Transactional v1-to-v2 migration and masking batches

**Files:**
- Modify: `vibemask/vault/storage.py`
- Modify: `vibemask/cli.py`
- Modify: `vibemask/core/ooxml.py`
- Modify: `vibemask/core/office.py`
- Modify: `vibemask/web/api.py`
- Modify: `tests/test_vault_encryption.py`
- Create: `tests/test_vault_batching.py`

- [ ] **Step 1: Write failing migration and batch atomicity tests**

```python
def test_batch_failure_rolls_back_mappings_and_session(vault):
    with pytest.raises(RuntimeError):
        with vault.batch("mask-session"):
            vault.get_or_create_mapping("SYNTHETIC", "PERSON", "{{PERSON_1}}")
            raise RuntimeError("synthetic failure")
    assert vault.get_stats()["total_mappings"] == 0

def test_v1_rows_upgrade_to_v2_in_one_integrity_epoch(legacy_v1_vault):
    vault = VaultStorage(legacy_v1_vault.project, key_store=legacy_v1_vault.store)
    assert all(value.startswith("vmenc:v2:1:") for value in raw_sensitive_values(vault))
    assert vault.security_status()["encryption_version"] == 2
```

Add a keyring checkpoint counter and assert 100 mapping inserts plus one session inside a batch produce one SQLite epoch and one trusted-state update. Inject failure on the second encryption and verify v1 data and metadata remain unchanged.

- [ ] **Step 2: Run tests and verify RED**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_batching.py tests/test_vault_encryption.py -v`

Expected: FAIL because `batch()` and v2 migration are absent.

- [ ] **Step 3: Implement nested-safe batch orchestration and v2 migration**

```python
@contextmanager
def batch(self, operation: str = "batch"):
    if self._batch_conn is not None:
        yield self
        return
    with self._lock:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        self._batch_conn = conn
        try:
            yield self
            pending = append_integrity_checkpoint(conn, self._integrity_key, operation)
            conn.commit()
            self.key_store.set_trusted_state(self.project_id, pending)
        except Exception:
            conn.rollback()
            raise
        finally:
            self._batch_conn = None
            conn.close()
```

The migration must decrypt v1 with key v1 and the old AAD, re-encrypt every sensitive field with v2 key v1 and new AAD, regenerate v2 lookup HMACs, set `encryption_version=2`, append one audit entry, commit, update trusted state, and `VACUUM` only after success.

- [ ] **Step 4: Wrap complete masking operations in `vault.batch("mask-session")`**

Use the batch around all calls that create stable mappings and the final session in CLI, OOXML, Office, and web paths. Reads remain outside write transactions.

- [ ] **Step 5: Run focused and roundtrip tests**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_batching.py tests/test_vault_encryption.py tests/test_lossless_roundtrip.py tests/test_span_aware_replacement.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add vibemask/vault/storage.py vibemask/cli.py vibemask/core/ooxml.py vibemask/core/office.py vibemask/web/api.py tests/test_vault_batching.py tests/test_vault_encryption.py
git commit -m "feat: make vault masking writes atomic"
```

### Task 6: Authenticated recovery bundles

**Files:**
- Create: `vibemask/vault/recovery.py`
- Modify: `vibemask/vault/storage.py`
- Create: `tests/test_vault_recovery.py`

- [ ] **Step 1: Write failing bundle authentication, identity, permissions, and overwrite tests**

```python
def test_recovery_bundle_is_private_and_roundtrips(tmp_path, vault):
    output = tmp_path / "vault-recovery.json"
    vault.backup_key(output, "correct horse battery staple")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert b"correct horse" not in output.read_bytes()
    restored = load_recovery_bundle(output, "correct horse battery staple")
    assert restored.project_id == vault.project_id

def test_wrong_passphrase_and_tamper_are_indistinguishable(bundle):
    with pytest.raises(RecoveryBundleError, match="authentication failed"):
        load_recovery_bundle(bundle, "wrong")
```

Also test malformed JSON/base64, Scrypt parameter bounds, wrong project/database, missing-only default restore, explicit replace requirement, atomic replace cleanup, and no secret material in exception strings.

- [ ] **Step 2: Run tests and verify RED**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_recovery.py -v`

Expected: FAIL because recovery APIs do not exist.

- [ ] **Step 3: Implement the recovery format and atomic writer**

```python
@dataclass(frozen=True)
class RecoveryPayload:
    project_id: str
    database_id: str
    key_version: int
    encryption_key: bytes
    integrity_key: bytes
    trusted_state: TrustedState

def derive_recovery_key(passphrase: str, salt: bytes, *, n: int = 2**15) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passphrase.encode("utf-8"))
```

The JSON envelope is versioned, stores only salt/nonce/ciphertext/KDF parameters, uses AAD `vibemask:recovery:v1`, caps attacker-controlled Scrypt parameters before allocation, and maps parse/authentication failures to one safe `RecoveryBundleError`. Write in the destination directory, `fsync` the file, `chmod 0600`, `os.replace`, then `fsync` the directory on POSIX.

- [ ] **Step 4: Add `VaultStorage.backup_key()` and `restore_key()`**

Backup verifies integrity before exporting and updates `recovery_key_version` only after the file is durable. Restore decrypts first, verifies project/database/version, writes missing secrets, reopens/verifies the vault, and rolls back newly written keyring entries on verification failure. Different existing secrets require `replace=True`.

- [ ] **Step 5: Run recovery and integrity tests**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_recovery.py tests/test_vault_integrity.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add vibemask/vault/recovery.py vibemask/vault/storage.py tests/test_vault_recovery.py
git commit -m "feat: add authenticated vault recovery bundles"
```

### Task 7: Transactional key rotation

**Files:**
- Modify: `vibemask/vault/storage.py`
- Create: `tests/test_vault_rotation.py`

- [ ] **Step 1: Write failing success and failure-boundary tests**

```python
def test_rotation_reencrypts_all_fields_and_invalidates_old_key(vault):
    before = raw_sensitive_values(vault)
    vault.rotate_key()
    after = raw_sensitive_values(vault)
    assert all(value.startswith("vmenc:v2:2:") for value in after)
    assert before != after
    assert vault.security_status()["active_key_version"] == 2
    assert vault.key_store.get_encryption_key(vault.project_id, 1) is None
```

Inject failures at: new-key store, first decrypt, mid-row encrypt, SQLite commit, trusted-state update, post-commit verification, and old-key deletion. Assert rollback and key cleanup before commit; after commit assert new data remains recoverable and the old key is retained until verification succeeds.

- [ ] **Step 2: Run tests and verify RED**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_rotation.py -v`

Expected: FAIL because `rotate_key()` does not exist.

- [ ] **Step 3: Implement rotation state machine**

```python
def rotate_key(self) -> int:
    self.verify()
    old_version = self.active_key_version
    new_version = old_version + 1
    new_key = secrets.token_bytes(32)
    self.key_store.set_encryption_key(self.project_id, new_version, new_key)
    committed = False
    try:
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            self._reencrypt_sensitive_rows(conn, old_version, new_version, new_key)
            self._set_meta(conn, "active_key_version", str(new_version))
            pending = append_integrity_checkpoint(conn, self._integrity_key, "rotate-key")
            conn.commit()
            committed = True
        self.key_store.set_trusted_state(self.project_id, pending)
        self._activate_key(new_version, new_key)
        self.verify()
        self.key_store.delete_encryption_key(self.project_id, old_version)
        return new_version
    except Exception:
        if not committed:
            self.key_store.delete_encryption_key(self.project_id, new_version)
        raise
```

Implement `_reencrypt_sensitive_rows()` to decrypt each mapping/session field with the cipher selected from its parsed envelope, encrypt with the new cipher and row/field AAD, and rebuild every mapping lookup HMAC. Implement `_activate_key()` to replace the cached active version/cipher only after the database commit. On a keyring checkpoint failure after commit, raise a specific incomplete-operation error and leave both keys. A normal subsequent open validates the signed continuation and advances trusted state. Rotation reports that recovery is stale until a v2-current backup is created.

- [ ] **Step 4: Run rotation, recovery, and integrity tests**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_rotation.py tests/test_vault_recovery.py tests/test_vault_integrity.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vibemask/vault/storage.py tests/test_vault_rotation.py
git commit -m "feat: rotate vault encryption keys safely"
```

### Task 8: Safe Vault CLI operations

**Files:**
- Modify: `vibemask/cli.py`
- Create: `tests/test_vault_cli_security.py`

- [ ] **Step 1: Write failing CLI tests**

```python
def test_vault_security_status_contains_no_sensitive_values(runner, synthetic_vault):
    result = runner.invoke(app, ["vault", "security-status", "--project-root", str(synthetic_vault.project)])
    assert result.exit_code == 0
    assert "AES-256-GCM" in result.output
    assert "SYNTHETIC-PII" not in result.output

def test_backup_key_uses_hidden_confirmed_prompt(runner, synthetic_vault, tmp_path):
    result = runner.invoke(
        app, ["vault", "backup-key", "--project-root", str(synthetic_vault.project),
              "--output", str(tmp_path / "recovery.json")],
        input="passphrase\npassphrase\n",
    )
    assert result.exit_code == 0
    assert "passphrase" not in result.output
```

Cover `verify`, `restore-key`, `rotate-key`, confirmation on replacement, safe nonzero exits, stale-recovery warning, and absence of keys/ciphertexts/original values in stdout/stderr.

- [ ] **Step 2: Run tests and verify RED**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_cli_security.py -v`

Expected: FAIL because the `vault` command group is absent.

- [ ] **Step 3: Add the Typer sub-application**

```python
vault_app = typer.Typer(help="Verify, recover, and rotate the encrypted local vault.")
app.add_typer(vault_app, name="vault")

@vault_app.command("backup-key")
def backup_key(output: Path, project_root: Path | None = None):
    passphrase = typer.prompt("Recovery passphrase", hide_input=True, confirmation_prompt=True)
    VaultStorage(str(get_project_path(project_root))).backup_key(output, passphrase)
```

Catch only known `VaultSecurityError`/`RecoveryBundleError` failures, print a short safe message, and exit 1. Never print traceback, secret material, ciphertext, passphrase, original paths stored inside sessions, or mappings.

- [ ] **Step 4: Run CLI tests**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/test_vault_cli_security.py tests/test_cli_exec_wrapper.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vibemask/cli.py tests/test_vault_cli_security.py
git commit -m "feat: add vault security management commands"
```

### Task 9: Supply-chain gates, native smoke test, and operator documentation

**Files:**
- Create: `scripts/vault_keyring_smoke.py`
- Create: `.github/workflows/security.yml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Add a synthetic, opt-in native keyring smoke script**

```python
def main() -> int:
    project_id = f"smoke-{uuid.uuid4()}"
    store = NativeKeyringStore()
    key = secrets.token_bytes(32)
    try:
        store.set_encryption_key(project_id, 1, key)
        assert store.get_encryption_key(project_id, 1) == key
        return 0
    finally:
        store.delete_encryption_key(project_id, 1)
```

The script must never use the user's normal project fingerprint or vault path, must print backend/status only, and must clean up in `finally`.

- [ ] **Step 2: Add security development dependencies and CI gates**

Add `pip-audit>=2.7` and `cyclonedx-bom>=4.5` to the `dev` extra. The workflow runs Python 3.11 tests and Ruff, `pip-audit`, and `cyclonedx-py environment --output-file sbom.json`, then uploads the SBOM. Native keyring and MLX checks are documented as platform release smoke tests, not falsely marked successful in Linux CI.

- [ ] **Step 3: Document the exact guarantees and runbooks**

README and roadmap must state: v2 AES-GCM field encryption, approved keyring backends, integrity/rollback checks, backup before rotation, recovery-stale status, commands, Linux Secret Service prerequisite, and the explicit same-user malware/filesystem-history exclusions. Mark the roadmap items complete only when their automated tests exist.

- [ ] **Step 4: Run format and configuration checks**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m ruff check vibemask/vault tests/test_vault_*.py scripts/vault_keyring_smoke.py`

Expected: PASS.

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pip_audit`

Expected: PASS, or record the exact advisory and block release; do not suppress known vulnerabilities.

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m cyclonedx_py environment --output-file /tmp/vibemask-sbom.json`

Expected: exit 0 and a valid CycloneDX JSON document.

- [ ] **Step 5: Commit**

```bash
git add scripts/vault_keyring_smoke.py .github/workflows/security.yml pyproject.toml README.md docs/ROADMAP.md
git commit -m "chore: add vault security release gates"
```

### Task 10: Full verification and Draft PR update

**Files:**
- Verify all changed files; modify only if verification exposes a defect.

- [ ] **Step 1: Run the complete automated test suite**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m pytest tests/ -v`

Expected: all tests PASS. Any platform-skipped test must state the unavailable real backend explicitly.

- [ ] **Step 2: Run repository lint and diff checks**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m ruff check vibemask tests scripts`

Expected: PASS.

Run: `git diff --check HEAD~9..HEAD`

Expected: no output and exit 0.

- [ ] **Step 3: Run behavior evaluations required by AGENTS.md**

Run: `/opt/homebrew/opt/python@3.11/bin/python3.11 -m eval.runner --engine hybrid`

Expected: report MICRO-F1 and WEIGHTED-F1; because detection behavior is not intentionally changed, regressions block completion.

Run the documented synthetic residual-PII scan and mask/restore roundtrip tests. Do not inspect real vault contents or `~/.vibemask`.

- [ ] **Step 4: Review the threat-model checklist**

Confirm with test or code evidence: offline DB disclosure, ciphertext tamper, row deletion/replay, completed-transaction rollback, crash between DB/keyring commit, missing key, recovery authentication, rotation boundaries, concurrent writer timeout, v1 migration, safe CLI output, backend fail-closed policy, and stale-recovery reporting. Record any platform checks not actually run.

- [ ] **Step 5: Push and update the existing Draft PR**

```bash
git push origin feat/eval-and-detection-accuracy
```

Update Draft PR #2 with the security model, migration behavior, test evidence, dependency-audit/SBOM status, and the explicit native-Keychain/Windows/Linux smoke checks still required before marking ready.
