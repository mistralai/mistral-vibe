from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os
from pathlib import Path
import time
from typing import Any, cast

_POLL_SECONDS = 0.005


def acquire_file_lock(file: Any, *, blocking: bool = False) -> None:
    """Take an exclusive OS lock on an open file; raise BlockingIOError when held."""
    if _is_windows():
        msvcrt = cast(Any, __import__("msvcrt"))

        # Check the size, not a read, and write through the raw descriptor:
        # a read or buffered write of the locked byte raises a raw
        # PermissionError that must not escape this except.
        descriptor = file.fileno()
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
        except PermissionError:
            # Byte 0 is locked by another handle; skip the seed and let the
            # lock call below report the contention.
            pass
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(file.fileno(), mode, 1)
        except OSError as exc:
            raise BlockingIOError from exc
        return
    import fcntl

    try:
        operation = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(file.fileno(), operation)
    except OSError as exc:
        raise BlockingIOError from exc


def release_file_lock(file: Any) -> None:
    if _is_windows():
        msvcrt = cast(Any, __import__("msvcrt"))

        # Raw lseek, matching acquire_file_lock.
        os.lseek(file.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@asynccontextmanager
async def async_file_lock(path: Path, *, timeout: float) -> AsyncIterator[None]:
    """Hold an exclusive lock on *path*; raises TimeoutError past *timeout*."""
    # The file stays on release: unlinking it would let two lockers hold different inodes.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire_file_lock(file)
                break
            except BlockingIOError as exc:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Timed out waiting for {path}") from exc
                await asyncio.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            release_file_lock(file)


def _is_windows() -> bool:
    return os.name == "nt"


__all__ = ["acquire_file_lock", "async_file_lock", "release_file_lock"]
