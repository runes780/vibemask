# Production Vault Hardening Design

## Scope and threat model

VibeMask will provide a production-grade local, single-user vault. It protects copied vault databases from offline disclosure, detects authenticated-field tampering and rollback of completed transactions, supports key recovery and rotation, and fails closed when its trusted key store is unavailable.

It does not claim to defend against malware already executing as the same OS user and controlling the VibeMask process, forensic recovery from filesystem snapshots or historical backups, or multi-tenant/team key management.

Development and automated tests must never inspect a real `~/.vibemask` directory. They use temporary HOME directories, synthetic values, and injected in-memory key providers.

## Selected architecture

Keep SQLite and field-level AES-256-GCM. SQLCipher is not selected because it adds native packaging risk and does not solve key recovery, rotation, rollback, or same-user process access. The design separates cryptography, key-store policy, recovery bundles, logical integrity, locking, and storage orchestration.

### Components

- `vault/crypto.py`: versioned AES-GCM envelopes, parsing, domain-separated HMAC lookup tokens.
- `vault/keyring_policy.py`: approved backend policy, versioned encryption keys, a persistent integrity key, and trusted state stored in the OS keyring.
- `vault/recovery.py`: passphrase-protected recovery bundles using Scrypt and AES-GCM, written atomically with mode `0600`.
- `vault/integrity.py`: canonical logical database manifest, append-only HMAC audit chain, trusted epoch comparison, tamper and rollback errors.
- `vault/locking.py`: cross-process file locking around initialization, migration, writes, rotation, and recovery.
- `vault/storage.py`: schema, transactions, encryption boundary, batch transactions, migration, verification, rotation, and status orchestration.

## Keyring policy and key model

Production accepts only the native macOS Keychain, Windows Credential Manager, and Linux Secret Service backends. Null, fail, plaintext, `keyrings.alt`, chained unknown, and unrecognized backends are rejected. Tests may inject an explicitly marked in-memory provider.

Versioned accounts:

```text
vibemask / vault:<project_id>:key:v<N>
vibemask / vault:<project_id>:integrity
vibemask / vault:<project_id>:state
```

The legacy account `vault:<project_id>` is read only for migration to versioned key `v1`.

Trusted state contains a database UUID, active encryption-key version, integrity epoch, logical manifest, audit-chain head, and latest recovery-bundle key version. Key material is never stored in SQLite.

## Ciphertext format

New writes use:

```text
vmenc:v2:<key_version>:<base64(nonce || ciphertext || tag)>
```

AES-256-GCM uses a random 96-bit nonce per field. AAD binds format version, project, table, record, field, and key version. Existing `vmenc:v1:` values remain readable and are upgraded transactionally.

Stable mapping lookup uses a domain-separated keyed HMAC under the active encryption key. Raw hashes of PII are never stored.

## Transaction integrity and rollback detection

The database stores a canonical logical manifest covering all encrypted mapping/session rows and relevant metadata. Every committed write transaction increments an epoch and appends an HMAC audit entry containing the previous chain head, new manifest, operation type, and epoch. The persistent integrity key is independent from rotated encryption keys.

The OS keyring stores the latest trusted epoch, manifest, and chain head. On open:

- database epoch below trusted epoch: rollback, refuse;
- same epoch with a different manifest/head: tamper, refuse;
- database ahead with a valid audit continuation from the trusted head: recover a crash between SQLite commit and keyring update, then advance trusted state;
- invalid chain or logical manifest: tamper, refuse.

All high-volume mapping writes run in one batch transaction, so one database commit and one keyring checkpoint cover the complete masking session.

## Recovery bundles

`vibemask vault backup-key` creates a JSON recovery bundle containing the active encryption key, persistent integrity key, key version, project ID, database ID, and trusted state. The payload is encrypted with AES-256-GCM under a key derived from a hidden, confirmed passphrase using Scrypt with a random salt. The file is written through a same-directory temporary file, `fsync`, atomic replace, and mode `0600`.

`restore-key` verifies envelope authentication, project/database identity, and database key version. It restores only missing material by default. Replacing existing different keys requires an explicit flag and interactive confirmation. After restoration, the complete vault integrity check must pass before success is reported.

## Key rotation

`rotate-key` obtains the exclusive lock, verifies the vault, stores a new versioned key, re-encrypts all sensitive fields and lookup HMACs in one SQLite transaction, appends a new integrity epoch, commits, updates trusted keyring state, verifies all rows under the new key, and only then deletes the old key. Failures before commit remove the new key and roll back. A crash after database commit is recovered from the signed audit continuation. The old key is retained until verification completes.

Rotation marks existing recovery bundles stale and instructs the user to create a new one.

## SQLite and locking

Use `journal_mode=DELETE`, `secure_delete=ON`, a busy timeout, and an exclusive cross-process file lock for state-changing operations. Migration and rotation finish with `VACUUM`; stale WAL/SHM files are rejected or safely checkpointed before mutation. No claim is made about APFS snapshots, Time Machine, cloud version history, or deleted historical copies.

## CLI

Add a `vault` command group:

```text
vibemask vault security-status
vibemask vault verify
vibemask vault backup-key --output FILE
vibemask vault restore-key FILE
vibemask vault rotate-key
```

Commands never print keys, ciphertext, passphrases, original values, or session mappings.

## Verification and release gates

Automated coverage includes backend policy, v1/v2 compatibility, malformed envelopes, recovery bundle authentication and permissions, missing/wrong keys, every rotation failure boundary, rollback, replay, row deletion, field replacement, audit truncation, concurrent writers, SQLite busy handling, migration rollback, and keyring update failure. Fuzz-style malformed input tests exercise ciphertext and bundle parsers.

Repository release gates include full pytest, focused Ruff, `pip-audit`, CycloneDX SBOM generation, platform keyring smoke-test scripts, MLX evaluation on Metal, and residual-PII scans. Platform smoke tests are documented and fail closed when unavailable; they are not simulated as successful in restricted CI.
