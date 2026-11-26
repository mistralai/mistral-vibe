from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
import os
from pathlib import Path
import select
import sys

from mistralai_vibe_local_harness.vibe._storage import sweep_session_leases
from vibe.observability.logging import logger

# Polled, not watched: lease probes touch lease files and would wake every watcher.
_LEASE_POLL_S = 2.0


@dataclass(frozen=True, slots=True)
class LeaseHolder:
    process_id: int | None


async def watch_session_leases(
    storage_root: Path,
) -> AsyncGenerator[dict[str, LeaseHolder | None]]:
    wake = asyncio.Event()
    exits = _HolderExits(asyncio.get_running_loop(), lambda _session_ids: wake.set())
    try:
        failing = False
        try:
            held = await asyncio.to_thread(_read_held_leases, storage_root)
        except OSError:
            logger.warning("Session lease poll failed", exc_info=True)
            held = {}
            failing = True
        exits.follow(held)
        yield dict(held)
        while True:
            with suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), _LEASE_POLL_S)
            wake.clear()
            try:
                current = await asyncio.to_thread(_read_held_leases, storage_root)
            except OSError:
                if not failing:
                    logger.warning("Session lease poll failed", exc_info=True)
                failing = True
                continue
            failing = False
            changes = _lease_changes(held, current)
            held = current
            exits.follow(held)
            if changes:
                yield changes
    finally:
        exits.close()


def _read_held_leases(storage_root: Path) -> dict[str, LeaseHolder]:
    return {
        session_id: LeaseHolder(process_id)
        for session_id, process_id in sweep_session_leases(storage_root).items()
    }


def _lease_changes(
    previous: dict[str, LeaseHolder], current: dict[str, LeaseHolder]
) -> dict[str, LeaseHolder | None]:
    changes: dict[str, LeaseHolder | None] = {
        session_id: holder
        for session_id, holder in current.items()
        if previous.get(session_id) != holder
    }
    changes.update((session_id, None) for session_id in previous.keys() - current)
    return changes


class _HolderExits:
    def __init__(
        self, loop: asyncio.AbstractEventLoop, on_exit: Callable[[Iterable[str]], None]
    ) -> None:
        self._loop = loop
        self._on_exit = on_exit
        self._watched: dict[str, tuple[int, int, Callable[[], None]]] = {}
        self._vanished: dict[str, int] = {}

    def follow(self, held: dict[str, LeaseHolder]) -> None:
        for session_id, (pid, _descriptor, _close) in list(self._watched.items()):
            holder = held.get(session_id)
            if holder is None or holder.process_id != pid:
                self._stop(session_id)
        for session_id, pid in list(self._vanished.items()):
            holder = held.get(session_id)
            if holder is None or holder.process_id != pid:
                del self._vanished[session_id]
        for session_id, holder in held.items():
            pid = holder.process_id
            if (
                pid is None
                or session_id in self._watched
                or self._vanished.get(session_id) == pid
            ):
                continue
            self._watch(session_id, pid)

    def close(self) -> None:
        for session_id in list(self._watched):
            self._stop(session_id)

    def _watch(self, session_id: str, pid: int) -> None:
        try:
            watched = _process_exit_descriptor(pid)
        except ProcessLookupError:
            self._vanished[session_id] = pid
            self._on_exit([session_id])
            return
        except OSError:
            return
        if watched is None:
            return
        descriptor, close = watched
        try:
            self._loop.add_reader(descriptor, self._exited, session_id)
        except NotImplementedError:
            close()
            return
        self._watched[session_id] = (pid, descriptor, close)

    def _exited(self, session_id: str) -> None:
        self._stop(session_id)
        self._on_exit([session_id])

    def _stop(self, session_id: str) -> None:
        watched = self._watched.pop(session_id, None)
        if watched is None:
            return
        _pid, descriptor, close = watched
        self._loop.remove_reader(descriptor)
        close()


_SYS_PIDFD_OPEN = 434  # __NR_pidfd_open, same number on all Linux arches


def _pidfd_open(pid: int) -> int:
    """Open a pidfd for ``pid``.

    Falls back to a raw ``syscall(2)`` when the interpreter was built
    without ``os.pidfd_open`` (some python-build-standalone 3.14 builds
    lack it because they are configured without ``HAVE_GETRANDOM_SYSCALL``).
    """
    pidfd_open = getattr(os, "pidfd_open", None)
    if pidfd_open is not None:
        return pidfd_open(pid)
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    descriptor = libc.syscall(_SYS_PIDFD_OPEN, pid, 0)
    if descriptor < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return descriptor


def _process_exit_descriptor(pid: int) -> tuple[int, Callable[[], None]] | None:
    if sys.platform == "linux":
        descriptor = _pidfd_open(pid)
        return descriptor, lambda: os.close(descriptor)
    if sys.platform == "darwin" or sys.platform.startswith("freebsd"):
        queue = select.kqueue()
        try:
            queue.control(
                [
                    select.kevent(
                        pid,
                        filter=select.KQ_FILTER_PROC,
                        flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                        fflags=select.KQ_NOTE_EXIT,
                    )
                ],
                0,
            )
        except BaseException:
            queue.close()
            raise
        return queue.fileno(), queue.close
    return None
