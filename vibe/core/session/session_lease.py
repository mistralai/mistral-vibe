from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Self, cast

_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class SessionBusyError(RuntimeError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session is already open: {session_id}")


class SessionLease:
    # The on-disk lease format (seed byte, {id}.lock.json, .registry) is
    # shared with the twin in
    # vibe_sdk/harness/runtimes/python/.../vibe/_storage.py; change both.

    def __init__(self, root: Path, session_id: str) -> None:
        if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            raise ValueError(f"invalid session ID: {session_id!r}")
        self._path = root / "active" / f"{session_id}.lock"
        self._diagnostic_path = root / "active" / f"{session_id}.lock.json"
        self._session_id = session_id
        self._file: Any | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def diagnostic_path(self) -> Path:
        return self._diagnostic_path

    def acquire(self) -> Self:
        if self._file is not None:
            raise RuntimeError("session lease is already acquired")
        if self._path.parents[1].is_symlink() or self._path.parent.is_symlink():
            raise ValueError("session lease path cannot contain a symbolic link")
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _lease_directory_lock(self._path.parent):
            self._path.touch(mode=0o600, exist_ok=True)
            file = self._path.open("a+b")
            try:
                _acquire_file_lock(file)
            except BlockingIOError as exc:
                file.close()
                raise SessionBusyError(self._session_id) from exc
            try:
                diagnostic = {
                    "lease_version": 1,
                    "session_id": self._session_id,
                    "process_id": os.getpid(),
                    "acquired_at": _timestamp(),
                }
                # The diagnostic sits beside the lock, not inside it: Windows
                # byte-range locks make the lock file unreadable while held.
                # Readers must tolerate a torn read during acquire.
                self._diagnostic_path.touch(mode=0o600, exist_ok=True)
                with self._diagnostic_path.open("wb") as diagnostic_file:
                    diagnostic_file.write(
                        json.dumps(
                            diagnostic,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode()
                        + b"\n"
                    )
                    diagnostic_file.flush()
                    os.fsync(diagnostic_file.fileno())
            except BaseException:
                # A lease that cannot publish its diagnostic is not acquired:
                # undo the lock so the caller can retry. BaseException, not
                # OSError: the guarded block also runs non-OS code (_timestamp,
                # getpid, json.dumps), and a Ctrl-C there must roll back too —
                # _lease_directory_lock uses the same width for that reason.
                # The unlock can itself fail on Windows; the close must still
                # run — closing the descriptor releases the OS-level lock on
                # every platform, so the lease cannot stay stuck.
                try:
                    _release_file_lock(file)
                finally:
                    file.close()
                with suppress(OSError):
                    self._path.unlink(missing_ok=True)
                raise
            self._file = file
        return self

    def release(self) -> None:
        if self._file is None:
            return
        file = self._file
        self._file = None
        with _lease_directory_lock(self._path.parent):
            try:
                _release_file_lock(file)
            finally:
                file.close()
            # An out-of-band reader can hold either file open on Windows and
            # make unlink fail; leftovers are harmless (the next acquire
            # reuses the lock file and overwrites the diagnostic).
            with suppress(OSError):
                self._path.unlink(missing_ok=True)
            with suppress(OSError):
                self._diagnostic_path.unlink(missing_ok=True)

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.release()


@contextmanager
def _lease_directory_lock(directory: Path) -> Iterator[None]:
    registry = directory / ".registry"
    registry.touch(mode=0o600, exist_ok=True)
    file = registry.open("a+b")
    try:
        _acquire_file_lock(file, blocking=True)
    except BaseException:
        file.close()
        raise
    try:
        yield
    finally:
        _release_file_lock(file)
        file.close()


def _acquire_file_lock(file: Any, *, blocking: bool = False) -> None:
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


def _release_file_lock(file: Any) -> None:
    if _is_windows():
        msvcrt = cast(Any, __import__("msvcrt"))

        # Raw lseek, matching _acquire_file_lock.
        os.lseek(file.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _is_windows() -> bool:
    return os.name == "nt"


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["SessionBusyError", "SessionLease"]
