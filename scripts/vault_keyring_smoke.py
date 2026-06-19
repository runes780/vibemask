#!/usr/bin/env python3
"""Opt-in native keyring smoke test using an isolated synthetic account."""

from __future__ import annotations

import argparse
import secrets
import uuid

from vibemask.vault.keyring_policy import NativeKeyringStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-native-keyring",
        action="store_true",
        help="Acknowledge that this test writes and deletes one random keyring account.",
    )
    args = parser.parse_args()
    if not args.confirm_native_keyring:
        parser.error("--confirm-native-keyring is required")

    project_id = f"smoke-{uuid.uuid4()}"
    store = NativeKeyringStore()
    key = secrets.token_bytes(32)
    try:
        store.set_encryption_key(project_id, 1, key)
        if store.get_encryption_key(project_id, 1) != key:
            raise RuntimeError("Native keyring roundtrip did not match.")
        print(f"PASS: {store.name} accepted an isolated synthetic key.")
        return 0
    finally:
        store.delete_encryption_key(project_id, 1)


if __name__ == "__main__":
    raise SystemExit(main())
