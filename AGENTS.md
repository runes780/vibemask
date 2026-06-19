# VibeMask — Agent Guidance

This file contains agent-focused guidance for working with the VibeMask codebase.

## Recommended Masking Mode

Use the **default** `hybrid + MLX + viterbi` pipeline:

```bash
vibemask mask document.docx
```

No flags are required. This is the validated default path.

### Why this is the default

- Real-world Chinese Office documents (lists, tables, application forms) show **≥99.6% recall and 100% precision** for actual PII values.
- Masked output has **zero residual PII** (no leftover names, IDs, phone numbers, or URLs).
- Full roundtrip is lossless: `original → mask → restore` yields the original file byte-for-byte in content.
- All fixes and improvements live in the default code path; no `--engine`, `--privacy-backend`, or `--privacy-decode-mode` flags are needed.

### Default configuration equivalence

```bash
vibemask mask document.docx \
  --engine hybrid \
  --privacy-backend mlx \
  --privacy-decode-mode viterbi \
  --language zh \
  --convert-legacy
```

### Platform exceptions

| Environment | Recommended command | Reason |
|---|---|---|
| Apple Silicon (M1/M2/M3/M4) | `vibemask mask file` | MLX backend is available and is the default. |
| Linux / Windows / Intel Mac | `vibemask mask file --privacy-backend opf` | MLX is not available; use the native OpenAI Privacy Filter backend. |
| Fully offline / no model | `vibemask mask file --engine regex` | Fast and deterministic, but only catches rule-based PII (phone, ID, email, URL, date). Complex person-name recall will be lower. |

## Files validated on this default path

- `tests/20220629105340584058.xlsx` → 40 person names masked and restored losslessly.
- `tests/b1868fc7-e4dc-4415-a85d-37a9d714dec7.xlsx` → 134 entities (names + URLs) masked and restored losslessly.
- `tests/浙江大学2015年推荐免试生公示名单.xls` → 2,804 entities (names + student IDs) masked and restored losslessly.

## Privacy and security

- **Never read `~/.vibemask/` directly.** This directory contains reversible PII mappings (`vault.sqlite`) and session data with original sensitive values. If you need to inspect vault state, use the public CLI (`vibemask sessions`) or API.
- `chmod 700 ~/.vibemask` does **not** prevent AI tools from reading the vault, because agents run as the same user. Real isolation requires encryption, a separate user account, or a sandbox/container.
- See `CLAUDE.md` for additional Claude Code-specific privacy guidance.

## General conventions

- Keep changes minimal and follow the existing Python style (black + ruff).
- Run `pytest tests/ -v` after non-trivial changes.
- If you modify default detection/masking behavior, run `python -m eval.runner --engine hybrid` and report the new MICRO-F1 / WEIGHTED-F1.
- Do not change the default CLI option values in `vibemask/cli.py` unless the platform-exception guidance above is also updated.
