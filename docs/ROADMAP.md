# VibeMask Roadmap

## Current production baseline

- [x] Default `hybrid + MLX + viterbi` masking path for Apple Silicon.
- [x] Lossless DOCX/XLSX/PPTX masking and restoration on the validated fixtures.
- [x] Transactional v1-to-v2 Vault migration with no plaintext backup.
- [x] AES-256-GCM v2 field envelopes bound to project, row, field, and key version.
- [x] Native keyring allowlist for macOS Keychain, Windows Credential Manager, and Linux Secret Service.
- [x] HMAC logical manifest/audit chain with completed-transaction rollback detection.
- [x] One Vault commit/checkpoint per high-volume masking session.
- [x] Scrypt + AES-GCM recovery bundles, transactional key rotation, and safe Vault CLI.
- [x] Automated tests, Ruff, dependency audit, and CycloneDX SBOM CI gates.

## Release blockers and remaining work

### Latest local verification (2026-06-19)

- `pytest tests/ -v`: **167 passed**, 6 upstream deprecation warnings.
- Security-scope Ruff gate: passed.
- Clean temporary environment with `pip 26.1.2`: `pip-audit` reported no known
  dependency vulnerabilities; CycloneDX 1.6 SBOM generation produced 80 components.
- `hybrid + OPF` golden evaluation: MICRO-F1 **90.9%**, WEIGHTED-F1 **91.6%**.
- Default `hybrid + MLX` evaluation could not run in the restricted desktop session
  because Metal is unavailable. It remains an Apple Silicon release gate.

These checks do not replace the platform and release-corpus gates below.

### P0 — must pass before declaring the build production-ready on a platform

- [ ] Run `scripts/vault_keyring_smoke.py --confirm-native-keyring` on a real macOS, Windows, and Linux desktop session. Restricted/headless CI does not prove native credential-store behavior.
- [ ] Run the complete test suite and `pip-audit` from a clean locked release environment; resolve rather than suppress advisories.
- [ ] Run `python -m eval.runner --engine hybrid` on supported Apple Silicon hardware and record MICRO-F1 / WEIGHTED-F1.
- [ ] Run residual-PII and byte/content roundtrip checks on the approved Office release corpus.
- [ ] Verify package installation and migration from the last released Vault format on copies of synthetic fixtures.
- [ ] Clear the repository's historical full-tree Ruff backlog; the security workflow currently gates Vault, CLI, Vault tests, and the native smoke script.

### P1 — document-processing reliability and throughput

- [ ] Add a macOS/Linux/Windows LibreOffice conversion matrix for legacy `.doc/.xls/.ppt` files.
- [ ] Add crash-safe coordination between masked output-file publication and Vault session commit. Vault transactions are atomic; filesystem output replacement is a separate boundary.
- [ ] Benchmark large tables and multi-document batches after the one-checkpoint Vault change; optimize only from measured profiles.
- [ ] Expand PDF layout-preserving masking and add scanned-document OCR as an explicit optional pipeline.
- [ ] Add corrupted/hostile OOXML archive limits (entry count, decompressed size, nesting) before treating untrusted uploads as production-safe.

### P1 — deployment and application security

- [ ] Add authentication, authorization, upload quotas, and CSRF/rate-limit policy before exposing the Web UI beyond localhost.
- [ ] Define signed release artifacts and provenance in addition to the generated SBOM.
- [ ] Add scheduled dependency-update and vulnerability-response ownership.
- [ ] Add documented restore drills for key recovery and database rollback incidents.

### Deliberately out of scope for the local single-user product

- Malware or AI tooling already executing as the same OS user and controlling the VibeMask process.
- Recovery of plaintext from APFS/NTFS snapshots, Time Machine, cloud history, swap, crash dumps, or old backups.
- Multi-user/team access control, centralized KMS/HSM custody, remote revocation, or audit-log export.

Those require process/user isolation, encrypted storage and backup policy, or a separate multi-tenant architecture; they are not solved by application-level field encryption.
