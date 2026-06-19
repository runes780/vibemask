"""Cross-process locking and SQLite sidecar policy for the local vault."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .crypto import VaultSecurityError

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class VaultLockTimeout(VaultSecurityError):
    """The exclusive vault lock could not be acquired within its deadline."""


class VaultSidecarError(VaultSecurityError):
    """An unexpected SQLite WAL/SHM sidecar requires operator attention."""


class VaultFileLock:
    """Advisory exclusive file lock shared by all VibeMask processes."""

    def __init__(self, path: Path, timeout: float = 5.0, poll_interval: float = 0.05):
        self.path = Path(path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.name != "nt":
            os.fchmod(self._fd, 0o600)
        else:
            os.lseek(self._fd, 0, os.SEEK_SET)
            if os.fstat(self._fd).st_size == 0:
                os.write(self._fd, b"\0")
                os.fsync(self._fd)

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._acquire()
                return self
            except OSError as exc:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise VaultLockTimeout("Vault lock acquisition timed out.") from exc
                time.sleep(self.poll_interval)

    def _acquire(self) -> None:
        assert self._fd is not None
        if os.name == "nt":
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, exc_type, exc, tb):
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


def assert_no_stale_sidecars(vault_path: Path) -> None:
    """Remove empty sidecars and reject non-empty WAL/SHM files before mutation."""
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{vault_path}{suffix}")
        if not sidecar.exists():
            continue
        try:
            size = sidecar.stat().st_size
        except OSError as exc:
            raise VaultSidecarError("The SQLite sidecar could not be inspected safely.") from exc
        if size:
            raise VaultSidecarError(
                "A non-empty SQLite sidecar was found; refusing to mutate the vault."
            )
        try:
            sidecar.unlink()
        except OSError as exc:
            raise VaultSidecarError("The empty SQLite sidecar could not be removed.") from exc
