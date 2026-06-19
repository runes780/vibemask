# Vault Encryption Design

## Goal

Encrypt reversible PII stored by VibeMask without reading or copying any real user vault during development. New and legacy vaults must fail closed when the system key store is unavailable.

## Cryptography

- Encrypt sensitive SQLite fields with AES-256-GCM from `cryptography`.
- Generate a fresh 96-bit nonce for every field encryption.
- Bind ciphertext to project, table, row, and column through authenticated additional data (AAD).
- Store ciphertext as `vmenc:v1:<base64(nonce || ciphertext || tag)>` so encrypted and legacy values can be distinguished and future formats can be versioned.
- Keep generated placeholders (`masked_text`) and non-sensitive statistics/index fields in plaintext. Randomized encryption cannot support equality lookup, while placeholders contain no source PII.
- Store a keyed HMAC equality token for each original value so stable mapping lookup remains indexed without exposing a dictionary-attackable raw hash.

Sensitive fields are:

- `mappings.original_text`
- `sessions.input_files`
- `sessions.output_files`
- `sessions.mappings`

## Key Management

- Generate an independent 256-bit key for every project fingerprint.
- Store it in the operating-system key store using service `vibemask` and account `vault:<project_id>`.
- Never store the key or a reversible derivative in SQLite.
- If encrypted rows exist and the key is missing, stop with a specific error. Never create a replacement key.
- If the keyring backend is unavailable, stop. Never fall back to plaintext.
- Tests inject an in-memory provider and use temporary HOME directories; they never inspect `~/.vibemask/`.

## Legacy Migration

On first open, initialize schema metadata and migrate legacy plaintext fields inside one SQLite transaction. Encrypt only values without the version prefix, record encryption version 1 after all rows succeed, and roll the transaction back on any error. The migration is idempotent, does not create a plaintext backup, and does not log values.

## API Compatibility

`VaultStorage` keeps its existing public methods. Encryption and decryption happen at the storage boundary, so CLI, restore, wrapper, and Web callers continue receiving normal Python strings and dictionaries. An optional key-provider constructor argument exists only for controlled embedding and tests; production defaults to the OS keyring.

## Status and Errors

`vibemask status` reports the encryption algorithm, key-store backend, and migration version without exposing keys or stored values. Authentication failures, missing keys, unavailable keyring backends, and migration failures raise explicit vault-security errors.

## Verification

Tests prove randomized encryption, authenticated decryption, ciphertext tamper rejection, absence of synthetic PII bytes in SQLite, mapping/session roundtrips, plaintext migration, transactional rollback, missing-key failure, and unavailable-keyring failure. The full existing Python suite must remain green.
