"""Which app-server process resumes an owner's left turns.

Several processes share one store (Desktop runs a few), and any of them may be
asked to resume an owner's left turns. The first to take the owner's claim, an
OS file lock in the store held until the process exits, resumes them; it writes
the sessions it resumes beside the lock. Another process reads from there which
of them still run, so it reports them as running elsewhere rather than failed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import IO, Any

from vibe.core.utils.file_lock import acquire_file_lock, release_file_lock
from vibe.utils.io import atomic_write_bytes
from vibe.utils.platform import PRIVATE_DIRECTORY_MODE

logger = logging.getLogger(__name__)

type Owner = tuple[str, str, str, str]

# Beside the session leases (``active/<id>.lock``), which a lease sweep reads
# only by their ``.lock`` names: this directory is left alone.
_CLAIMS = Path("active") / "left-turns"
# As often as a lease watch polls.
_LEASE_POLL_S = 2.0


class LeftTurnsClaim:
    def __init__(
        self, owner: Owner, storage_root: Callable[[], Awaitable[Path]]
    ) -> None:
        self._storage_root = storage_root
        self._name = hashlib.sha256(json.dumps(list(owner)).encode()).hexdigest()
        self._root: Path | None = None
        self._file: IO[bytes] | None = None

    async def acquire(self) -> bool:
        """Whether this process holds the claim, taking it if it is free."""
        if self._file is not None:
            return True
        if self._root is None:
            self._root = await self._storage_root()
        self._file = await asyncio.to_thread(_lock, self._lock_path(self._root))
        if self._file is None:
            return False
        try:
            await self.publish([])
        except OSError:
            # Held all the same: until the holder publishes, the others list
            # nothing as running elsewhere.
            logger.warning("Could not publish a left-turns claim", exc_info=True)
        return True

    async def publish(self, session_ids: list[str]) -> None:
        """The sessions the holder resumes, for the other processes to read."""
        assert self._root is not None
        document = {"process_id": os.getpid(), "session_ids": session_ids}
        await asyncio.to_thread(
            atomic_write_bytes,
            self._diagnostic_path(self._root),
            json.dumps(document).encode(),
        )

    async def running_elsewhere(self) -> set[str]:
        """Of the sessions the holder published, those a process holds open.
        Nothing while no process holds the claim: what a holder published
        before it let go is no longer run.
        """
        if self._root is None:
            return set()
        root = self._root
        return await asyncio.to_thread(
            _held, root, self._lock_path(root), self._diagnostic_path(root)
        )

    async def until_free(self, session_id: str) -> None:
        """Until no process holds ``session_id`` open."""
        if self._root is None:
            return
        while session_id in await asyncio.to_thread(_leases, self._root):
            await asyncio.sleep(_LEASE_POLL_S)

    async def release(self) -> None:
        file, self._file = self._file, None
        if file is not None:
            # Closing the file releases the lock, as the process exit does.
            await asyncio.to_thread(file.close)

    def _lock_path(self, root: Path) -> Path:
        return root / _CLAIMS / f"{self._name}.lock"

    def _diagnostic_path(self, root: Path) -> Path:
        return root / _CLAIMS / f"{self._name}.lock.json"


def _lock(path: Path) -> IO[bytes] | None:
    path.parent.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    file = path.open("a+b")
    try:
        acquire_file_lock(file)
    except BlockingIOError:
        file.close()
        return None
    except BaseException:
        file.close()
        raise
    return file


def _held(root: Path, lock_path: Path, diagnostic_path: Path) -> set[str]:
    session_ids = _published(diagnostic_path)
    if not session_ids or not _claimed(lock_path):
        return set()
    return set(session_ids) & set(_leases(root))


def _claimed(lock_path: Path) -> bool:
    # Errors count as held, as for a session lease.
    try:
        file = lock_path.open("r+b")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    with file:
        try:
            acquire_file_lock(file)
        except BlockingIOError:
            return True
        release_file_lock(file)
    return False


def _leases(root: Path) -> dict[str, int | None]:
    # The harness is loaded with the first session host, not with this module.
    from mistralai_vibe_local_harness.vibe._storage import sweep_session_leases

    return sweep_session_leases(root)


def _published(path: Path) -> list[str]:
    try:
        document: Any = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return []
    session_ids = document.get("session_ids") if isinstance(document, dict) else None
    if not isinstance(session_ids, list):
        return []
    return [item for item in session_ids if isinstance(item, str)]


__all__ = ["LeftTurnsClaim"]
