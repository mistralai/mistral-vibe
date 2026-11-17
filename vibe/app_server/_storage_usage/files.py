"""The three files every Vibe process shares to agree on storage usage.

- ``storage_usage_cache.json``: the last scan, written whole by the process
  that ran it (a temp file, then ``os.replace``) and reloaded by the others
  when it is newer than what they hold.
- ``storage_usage.lock``: held by the process scanning, so one scan runs at a
  time across processes. Never taken just to look: ``storage_usage.scanning``
  names the scanning process for readers, so a read cannot make a scan
  starting at that moment think another one runs.
- ``storage_usage.stale``: touched after an archive, a delete and a finished
  worktree removal. A snapshot whose scan started before it is stale; nobody
  rewrites the cache for that.

Imports only the standard library and the file lock: the worktree reaper and
``workspace/git/worktrees/remove`` touch the marker.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import time
from typing import IO, Any

from vibe.core.paths import VIBE_HOME
from vibe.core.utils.file_lock import acquire_file_lock, release_file_lock
from vibe.observability.logging import logger

CACHE_FILENAME = "storage_usage_cache.json"
LOCK_FILENAME = "storage_usage.lock"
STALE_FILENAME = "storage_usage.stale"
SCANNING_FILENAME = "storage_usage.scanning"
_LOCK_POLL_S = 0.2


def touch_stale_marker(home: Path | None = None) -> None:
    """Never raises: a missed marker only delays a refresh until the next one."""
    path = (home or VIBE_HOME.path) / STALE_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        # touch keeps the time of a file it did not create on some systems.
        os.utime(path)
    except OSError:
        logger.debug("Could not touch the storage usage stale marker", exc_info=True)


def stale_marker_ms(home: Path) -> int:
    try:
        return int((home / STALE_FILENAME).stat().st_mtime * 1000)
    except OSError:
        return 0


def cache_mtime_ns(home: Path) -> int | None:
    try:
        return (home / CACHE_FILENAME).stat().st_mtime_ns
    except OSError:
        return None


def read_cache(home: Path, version: int) -> dict[str, Any] | None:
    """None for a missing, unreadable or other-version cache: no numbers yet."""
    try:
        document = json.loads((home / CACHE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("version") != version:
        return None
    return document


def write_cache(home: Path, document: dict[str, Any]) -> None:
    path = home / CACHE_FILENAME
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        home.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(document, separators=(",", ":")), "utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


class ScanLock:
    """The cross-process scan lock; ``acquire`` blocks, so call it off the loop."""

    def __init__(self, home: Path, owner: str = "") -> None:
        self._path = home / LOCK_FILENAME
        self._scanning = home / SCANNING_FILENAME
        self._owner = owner
        self._file: IO[bytes] | None = None

    def mark_scanning(self) -> None:
        """Tell readers a scan runs, and whose; only the holder calls it."""
        try:
            self._scanning.write_text(f"{os.getpid()} {self._owner}", "utf-8")
        except OSError:
            logger.debug("Could not mark the storage scan running", exc_info=True)

    def acquire(
        self,
        deadline: float,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
    ) -> bool:
        """Whether it is held, given up at ``deadline`` (a ``clock`` time)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        file = self._path.open("a+b")
        while True:
            try:
                acquire_file_lock(file)
            except BlockingIOError:
                if cancelled() or clock() >= deadline:
                    file.close()
                    return False
                time.sleep(_LOCK_POLL_S)
                continue
            self._file = file
            return True

    def release(self) -> None:
        file, self._file = self._file, None
        if file is None:
            return
        self._scanning.unlink(missing_ok=True)
        try:
            release_file_lock(file)
        finally:
            file.close()


def clear_scanning_marker(home: Path) -> None:
    """Only while holding the lock: no scan can be running then."""
    (home / SCANNING_FILENAME).unlink(missing_ok=True)


def scanning_elsewhere(home: Path, owner: str) -> bool:
    """Whether a scan not ``owner``'s runs; read without the lock.

    A crashed scanner leaves the file behind: its process is gone, so it
    does not count.
    """
    try:
        pid_text, _, scanner = (
            (home / SCANNING_FILENAME).read_text("utf-8").strip().partition(" ")
        )
        pid = int(pid_text)
    except (OSError, ValueError):
        return False
    return scanner != owner and _alive(pid)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _alive_windows(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]
    still_active = 259
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


__all__ = [
    "CACHE_FILENAME",
    "LOCK_FILENAME",
    "SCANNING_FILENAME",
    "STALE_FILENAME",
    "ScanLock",
    "cache_mtime_ns",
    "clear_scanning_marker",
    "read_cache",
    "scanning_elsewhere",
    "stale_marker_ms",
    "touch_stale_marker",
    "write_cache",
]
