import importlib
import os
import stat
import subprocess
import sys

import pytest

from vibemask.vault.storage import VaultStorage


def locking_module():
    return importlib.import_module("vibemask.vault.locking")


CHILD_LOCK_SCRIPT = """
import sys
from pathlib import Path
from vibemask.vault.locking import VaultFileLock, VaultLockTimeout
try:
    with VaultFileLock(Path(sys.argv[1]), timeout=0.15):
        pass
except VaultLockTimeout as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(0)
"""


def test_second_process_cannot_acquire_vault_lock(tmp_path):
    locking = locking_module()
    lock_path = tmp_path / "vault.lock"

    with locking.VaultFileLock(lock_path, timeout=1.0):
        result = subprocess.run(
            [sys.executable, "-c", CHILD_LOCK_SCRIPT, str(lock_path)],
            text=True,
            capture_output=True,
            check=False,
            cwd=os.getcwd(),
        )

    assert result.returncode == 2
    assert "timed out" in result.stderr.lower()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode assertion")
def test_lock_file_is_private(tmp_path):
    locking = locking_module()
    lock_path = tmp_path / "vault.lock"

    with locking.VaultFileLock(lock_path):
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_secure_connection_pragmas_are_applied(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    vault = VaultStorage(str(tmp_path / "project"))

    conn = vault._connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5_000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_non_empty_sqlite_sidecar_is_rejected(tmp_path, suffix):
    locking = locking_module()
    vault_path = tmp_path / "vault.sqlite"
    vault_path.touch()
    sidecar = tmp_path / f"vault.sqlite{suffix}"
    sidecar.write_bytes(b"synthetic stale sidecar")

    with pytest.raises(locking.VaultSidecarError, match="sidecar"):
        locking.assert_no_stale_sidecars(vault_path)


def test_empty_sqlite_sidecars_are_removed(tmp_path):
    locking = locking_module()
    vault_path = tmp_path / "vault.sqlite"
    vault_path.touch()
    sidecars = [tmp_path / "vault.sqlite-wal", tmp_path / "vault.sqlite-shm"]
    for sidecar in sidecars:
        sidecar.touch()

    locking.assert_no_stale_sidecars(vault_path)

    assert not any(sidecar.exists() for sidecar in sidecars)
