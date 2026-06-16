# VibeMask — Claude Code Privacy & Security Guide

This file is for Claude Code. It contains project-specific guidance on privacy, security, and safe behavior when working with VibeMask.

## Critical: Do NOT read sensitive vault data

VibeMask stores reversible mappings between original PII and masked tokens outside the project directory:

```
~/.vibemask/
├── projects/<fingerprint>/vault.sqlite   # original_text ↔ masked_text mappings
├── sessions/<session-id>/                # reports, masked file copies, recognition text
```

**You must NOT read, list, or inspect `~/.vibemask/` or its contents.**

These files contain real sensitive information (names, IDs, phone numbers, etc.) that users have chosen to mask. Reading them defeats the purpose of the tool and violates user privacy. If you need to verify vault behavior, do so through the public CLI (`vibemask sessions`) or Python API, not by opening the SQLite database or session directories directly.

## Why file permissions are not enough

A common misconception is that `chmod 700 ~/.vibemask` prevents AI tools from reading the vault. It does **not**.

- `700` only blocks **other users** on the same machine.
- Claude Code runs as the **same macOS user** as the human operator.
- Therefore, `700` permissions do **not** stop Claude Code from reading the directory.

If a user asks how to protect the vault from AI tools, explain that real isolation requires one of:

1. **Encryption**: Store the vault on an encrypted disk image that is only mounted when needed.
2. **Separate user account**: Run the vault under a different system user than Claude Code.
3. **Sandbox/container**: Run Claude Code in a container that does not mount `~/.vibemask`.

Do not suggest `chmod 700` as a solution for blocking AI access.

## Quick usage reference

Default recommended mode (Apple Silicon):

```bash
vibemask mask document.docx
vibemask restore document_masked.docx
vibemask sessions --project-root ./my-project
```

Non-Apple Silicon:

```bash
vibemask mask document.docx --privacy-backend opf
```

No-model / fully offline:

```bash
vibemask mask document.docx --engine regex
```

## Complete removal

To fully remove VibeMask and all associated sensitive data:

```bash
# 1. Uninstall the package
pip uninstall -y vibemask

# 2. Delete vault and session data (contains original PII mappings)
rm -rf ~/.vibemask

# 3. Delete MLX model cache
rm -rf ~/.cache/huggingface/hub/mlx-community/openai-privacy-filter-8bit

# 4. Delete jieba cache
find /var/folders -name 'jieba.cache' -delete 2>/dev/null

# 5. Remove local masked/restored files in the project
rm -rf demo demo_mlx
```

> **Warning**: `~/.vibemask/` is the most sensitive leftover. It contains the reversible mapping database. Deleting it makes restore impossible for previously masked files.

## Development reminders

- Keep changes minimal.
- Run `pytest tests/ -v` after non-trivial changes.
- If you change default detection behavior, run `python -m eval.runner --engine hybrid`.
- Never commit real PII or vault files to git. Ensure `~/.vibemask/` and local `demo*/` directories are ignored.
