from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import os
from pathlib import Path
from typing import Any, BinaryIO, cast

__all__ = ["exclusive_file_lock"]


@contextmanager
def exclusive_file_lock(path: Path) -> Generator[None]:
    """Hold a blocking cross-process lock on a hidden sibling of ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_name(f".{path.name}.lock").open("a+b")
    try:
        _acquire(lock_file)
    except BaseException:
        lock_file.close()
        raise
    try:
        yield
    finally:
        _release(lock_file)
        lock_file.close()


def _acquire(file: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt = cast(Any, __import__("msvcrt"))
        # Reading byte 0 while another handle locks it raises PermissionError.
        descriptor = file.fileno()
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_EX)


def _release(file: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt = cast(Any, __import__("msvcrt"))
        os.lseek(file.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_UN)
