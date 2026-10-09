from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import sys
import uuid

import watchfiles

from mistralai_vibe_local_harness.vibe._storage import SessionLease, session_is_live

_LEASE_FILE_DEBOUNCE_MS = 100
_HELD_LEASE_RECHECK_S = 10.0
_WATCH_START_ATTEMPT_S = 0.5
_WATCH_START_ATTEMPTS = 20
_LEASE_FILE_SUFFIXES = (".lock.json", ".lock")


@dataclass(frozen=True, slots=True)
class LeaseHolder:
    process_id: int | None


def read_lease_holder(storage_root: Path, session_id: str) -> LeaseHolder | None:
    if not session_is_live(storage_root, session_id):
        return None
    lease = SessionLease(storage_root, session_id)
    return LeaseHolder(_holder_process_id(lease.diagnostic_path))


async def watch_session_leases(
    storage_root: Path,
) -> AsyncGenerator[dict[str, LeaseHolder | None], None]:
    lease_dir = storage_root / "active"
    await asyncio.to_thread(lease_dir.mkdir, mode=0o700, parents=True, exist_ok=True)
    marker = lease_dir / f".watch-{uuid.uuid4().hex}"
    pending: set[str] = set()
    wake = asyncio.Event()
    watching = asyncio.Event()
    stop = asyncio.Event()

    def recheck(session_ids: Iterable[str]) -> None:
        pending.update(session_ids)
        wake.set()

    def is_watched(path: str) -> bool:
        return Path(path).name == marker.name or _lease_session_id(path) is not None

    async def watch_lease_files() -> None:
        async for changes in watchfiles.awatch(
            lease_dir,
            watch_filter=lambda _change, path: is_watched(path),
            debounce=_LEASE_FILE_DEBOUNCE_MS,
            stop_event=stop,
            recursive=False,
        ):
            if any(Path(path).name == marker.name for _change, path in changes):
                watching.set()
            recheck(
                session_id
                for _change, path in changes
                if (session_id := _lease_session_id(path)) is not None
            )

    files = asyncio.create_task(watch_lease_files())
    exits = _HolderExits(asyncio.get_running_loop(), recheck)
    try:
        await _wait_until_watching(files, marker, watching)
        held = await asyncio.to_thread(_read_held_leases, storage_root, lease_dir)
        exits.follow(held)
        yield dict(held)
        while True:
            woken = await _next_wake(
                wake, files, timeout=_HELD_LEASE_RECHECK_S if held else None
            )
            if not woken:
                pending.update(held)
            wake.clear()
            session_ids = set(pending)
            pending.clear()
            holders = await asyncio.to_thread(
                _read_lease_holders, storage_root, session_ids
            )
            changes: dict[str, LeaseHolder | None] = {}
            for session_id, holder in holders.items():
                if holder == held.get(session_id):
                    continue
                changes[session_id] = holder
                if holder is None:
                    held.pop(session_id, None)
                else:
                    held[session_id] = holder
            exits.follow(held)
            if changes:
                yield changes
    finally:
        exits.close()
        stop.set()
        files.cancel()
        with suppress(asyncio.CancelledError):
            await files


async def _wait_until_watching(
    files: asyncio.Task[None], marker: Path, watching: asyncio.Event
) -> None:
    try:
        for _attempt in range(_WATCH_START_ATTEMPTS):
            await asyncio.to_thread(marker.write_bytes, b"")
            started = asyncio.ensure_future(watching.wait())
            try:
                done, _ = await asyncio.wait(
                    {started, files},
                    timeout=_WATCH_START_ATTEMPT_S,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                started.cancel()
            if files in done:
                files.result()
                raise RuntimeError("Session lease file watcher stopped")
            if watching.is_set():
                return
        raise RuntimeError("Session lease file watcher did not start")
    finally:
        await asyncio.to_thread(marker.unlink, missing_ok=True)


async def _next_wake(
    wake: asyncio.Event, files: asyncio.Task[None], *, timeout: float | None
) -> bool:
    woken = asyncio.ensure_future(wake.wait())
    try:
        done, _ = await asyncio.wait(
            {woken, files}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        woken.cancel()
    if files in done:
        files.result()
        raise RuntimeError("Session lease file watcher stopped")
    return wake.is_set()


def _read_held_leases(storage_root: Path, lease_dir: Path) -> dict[str, LeaseHolder]:
    session_ids = {
        session_id
        for path in lease_dir.glob("*.lock")
        if (session_id := _lease_session_id(path)) is not None
    }
    return {
        session_id: holder
        for session_id, holder in _read_lease_holders(storage_root, session_ids).items()
        if holder is not None
    }


def _read_lease_holders(
    storage_root: Path, session_ids: Iterable[str]
) -> dict[str, LeaseHolder | None]:
    return {
        session_id: read_lease_holder(storage_root, session_id)
        for session_id in session_ids
    }


def _lease_session_id(path: str | Path) -> str | None:
    name = Path(path).name
    for suffix in _LEASE_FILE_SUFFIXES:
        if not name.endswith(suffix):
            continue
        session_id = name.removesuffix(suffix)
        try:
            SessionLease(Path(), session_id)
        except ValueError:
            return None
        return session_id
    return None


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


def _process_exit_descriptor(pid: int) -> tuple[int, Callable[[], None]] | None:
    if sys.platform == "linux":
        descriptor = os.pidfd_open(pid)
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


def _holder_process_id(diagnostic_path: Path) -> int | None:
    try:
        diagnostic = json.loads(diagnostic_path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(diagnostic, dict):
        return None
    pid = diagnostic.get("process_id")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return pid
