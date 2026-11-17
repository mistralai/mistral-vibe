"""Storage usage for every channel of the process: ``storage/usage/*``.

The cache file is the truth all Vibe processes share (``files.py``). This
service holds the last one it read, reloads it when another process wrote a
newer one, and runs a scan when asked: one at a time here, one at a time
across processes through the lock. The walk itself is a job of the
background worker process (``scan.py``), so it never holds this process's
GIL while sessions stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from vibe.app_server._background_worker import BackgroundWorker, BackgroundWorkerError
from vibe.app_server._model import ProtocolModel
from vibe.app_server._storage_usage import files, live
from vibe.app_server._storage_usage.compat import parse_snapshot
from vibe.app_server._storage_usage.removal import finished_worktrees, wait_until_gone
from vibe.app_server._storage_usage.scan import (
    CACHE_VERSION,
    SCAN_JOB,
    Previous,
    ScanRequest,
    storage_jobs,
)
from vibe.app_server._storage_usage.telemetry import (
    ScanOutcome,
    ScanTelemetry,
    TelemetryFactory,
)
from vibe.app_server._time import now_ms
from vibe.app_server.protocol import (
    STORAGE_USAGE_UPDATED,
    StorageUsageReadResponse,
    StorageUsageRefreshParams,
    StorageUsageRefreshResponse,
    StorageUsageSnapshot,
    StorageUsageStatus,
    StorageUsageUpdatedParams,
)
from vibe.core.paths import SESSION_LOG_DIR, VIBE_HOME, WORKTREES_DIR
from vibe.observability.logging import logger

# Imported, not only named, so a frozen build bundles the module the worker loads.
_JOBS = f"{storage_jobs.__module__}:{storage_jobs.__name__}"
TIME_LIMIT_S = 600.0
REMOVAL_WAIT_S = 60.0
# Past the time limit, counted from the submit, for the folder the walk is in
# when the limit passes; then the scan is given up and the lock released.
SCAN_MARGIN_S = 60.0
# One removal wait and one scan's longest hold: another process's scan is
# over by then.
LOCK_WAIT_S = REMOVAL_WAIT_S + TIME_LIMIT_S + SCAN_MARGIN_S
# Long enough for a momentary holder to let go, short enough to answer at once.
_LOCK_SETTLE_S = 1.0
_FOLLOW_POLL_S = 0.5

type Notify = Callable[[str, ProtocolModel], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Ask:
    full: bool = False
    wait_for: frozenset[str] = frozenset()

    @property
    def waits(self) -> bool:
        """``full`` and ``waitForSessionIds`` wait for another process's scan."""
        return self.full or bool(self.wait_for)

    def merge(self, other: _Ask) -> _Ask:
        return _Ask(self.full or other.full, self.wait_for | other.wait_for)


@dataclass(frozen=True, slots=True)
class _Finished:
    response: StorageUsageReadResponse
    complete: bool
    scan: ScanOutcome


@dataclass(slots=True)
class _Cached:
    document: dict[str, Any]
    mtime_ns: int | None
    snapshot: StorageUsageSnapshot
    never_suggested: set[str] = field(default_factory=set)


class StorageUsageService:
    def __init__(
        self,
        worker: BackgroundWorker,
        *,
        home: Callable[[], Path] = lambda: VIBE_HOME.path,
        worktrees_root: Callable[[], Path] = lambda: WORKTREES_DIR.path,
        default_session_root: Callable[[], Path] = lambda: SESSION_LOG_DIR.path,
        clock_ms: Callable[[], int] = now_ms,
        time_limit_s: float = TIME_LIMIT_S,
        scan_margin_s: float = SCAN_MARGIN_S,
        removal_wait_s: float = REMOVAL_WAIT_S,
        lock_wait_s: float = LOCK_WAIT_S,
    ) -> None:
        self._worker = worker
        self._home = home
        self._worktrees_root = worktrees_root
        self._default_session_root = default_session_root
        self._clock_ms = clock_ms
        self._time_limit_s = time_limit_s
        self._scan_margin_s = scan_margin_s
        self._removal_wait_s = removal_wait_s
        self._lock_wait_s = lock_wait_s
        # Replaced whole, never mutated: reads iterate it from worker threads.
        self._extra_roots: frozenset[Path] = frozenset()
        self._cached: _Cached | None = None
        # Reads reload from threads while the loop adopts a finished scan.
        self._cache_lock = threading.Lock()
        self._failed = False
        self._event_id = 0
        self._listeners: dict[int, Notify] = {}
        self._next_listener = 0
        self._telemetry = ScanTelemetry()
        self._task: asyncio.Task[None] | None = None
        self._queued: _Ask | None = None
        # The id the queued scan will run under, given out before it starts.
        self._queued_scan_id: int | None = None
        self._asked_while_running = False
        self._last_scan_id = 0
        self._running_scan_id: int | None = None
        # Every scan id the running follower answers, in the order given out.
        self._follower_scan_ids: list[int] = []
        self._follower: asyncio.Task[None] | None = None
        # Deciding to scan awaits file reads: one decision at a time, so two
        # refreshes arriving together cannot both start a scan.
        self._refresh_lock = asyncio.Lock()
        self._closed = False
        # Names this service's scans in the shared scanning marker.
        self._owner = uuid.uuid4().hex
        worker.add_jobs(_JOBS)

    # -- wiring ---------------------------------------------------------------

    def add_session_root(self, root: Path) -> None:
        """A session store open in this process besides the default one."""
        if root not in self._extra_roots:
            self._extra_roots |= {root}

    def listen(
        self, notify: Notify, telemetry: TelemetryFactory | None = None
    ) -> Callable[[], None]:
        """One channel's ``storage/usage/updated``; call the result when it closes.

        ``telemetry`` builds a client with that channel's credentials. The scan
        event uses the newest open channel's, and none once all are closed.
        """
        key = self._next_listener
        self._next_listener += 1
        self._listeners[key] = notify
        if telemetry is not None:
            self._telemetry.offer(key, telemetry)

        def stop() -> None:
            self._listeners.pop(key, None)
            self._telemetry.withdraw(key)

        return stop

    async def close(self) -> None:
        self._closed = True
        tasks = [task for task in (self._task, self._follower) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        await self._telemetry.close()

    # -- storage/usage/read ---------------------------------------------------

    async def read(self) -> StorageUsageReadResponse:
        return await asyncio.to_thread(self._read_now)

    def _read_now(self) -> StorageUsageReadResponse:
        home = self._paths().home
        self._reload_if_newer(home)
        cached = self._cached
        now = self._clock_ms()
        disk = live.disk_space(home, now)
        if cached is None:
            return StorageUsageReadResponse(
                status=self._status(home, None), snapshot=None, disk=disk
            )
        open_ids = live.open_sessions(self._paths().session_roots)
        snapshot = cached.snapshot.model_copy(
            update={
                "sessions": [
                    row.model_copy(update={"open": row.session_id in open_ids})
                    for row in cached.snapshot.sessions
                ],
                "archive_suggestion": live.archive_suggestion(
                    cached.snapshot, open_ids, cached.never_suggested, now
                ),
            }
        )
        return StorageUsageReadResponse(
            status=self._status(home, cached),
            snapshot=snapshot,
            disk=disk,
            stale=files.stale_marker_ms(home) > snapshot.scan_started_at,
        )

    def _status(self, home: Path, cached: _Cached | None) -> StorageUsageStatus:
        if self._running() or files.scanning_elsewhere(home, self._owner):
            return "refreshing"
        return self._settled_status(cached)

    def _settled_status(self, cached: _Cached | None) -> StorageUsageStatus:
        """The status of the numbers held, whatever scan runs meanwhile: what
        the event ending a scan id says, so it is never ``refreshing``.
        """
        if self._failed:
            return "failed"
        if cached is None:
            return "up_to_date"
        status = cached.document.get("status")
        return "partial" if status == "partial" else "up_to_date"

    def _reload_if_newer(self, home: Path) -> None:
        with self._cache_lock:
            mtime = files.cache_mtime_ns(home)
            cached = self._cached
            if mtime is None or (
                cached is not None
                and cached.mtime_ns is not None
                and mtime <= cached.mtime_ns
            ):
                return
            document = files.read_cache(home, CACHE_VERSION)
            if document is not None and self._adopt_locked(document, mtime):
                # A scan finished after the one that failed here.
                self._failed = False

    def _adopt(self, document: dict[str, Any], mtime_ns: int | None) -> None:
        with self._cache_lock:
            self._adopt_locked(document, mtime_ns)

    def _adopt_locked(self, document: dict[str, Any], mtime_ns: int | None) -> bool:
        try:
            snapshot = parse_snapshot(document.get("snapshot"))
        except ValueError:
            logger.warning("Ignoring an unreadable storage usage cache")
            return False
        never = document.get("neverSuggested")
        self._cached = _Cached(
            document,
            mtime_ns,
            snapshot,
            {item for item in never if isinstance(item, str)}
            if isinstance(never, list)
            else set(),
        )
        return True

    # -- storage/usage/refresh ------------------------------------------------

    async def refresh(
        self, params: StorageUsageRefreshParams
    ) -> StorageUsageRefreshResponse:
        async with self._refresh_lock:
            return await self._refresh_locked(params)

    async def _refresh_locked(
        self, params: StorageUsageRefreshParams
    ) -> StorageUsageRefreshResponse:
        ask = _Ask(params.full, frozenset(params.wait_for_session_ids))
        home = self._paths().home
        if self._running():
            if ask.waits:
                self._queued = ask if self._queued is None else self._queued.merge(ask)
                if self._queued_scan_id is None:
                    self._queued_scan_id = self._new_scan_id()
                scan_id = self._queued_scan_id
            else:
                # Answered by the scan under way; one more runs if it goes stale.
                self._asked_while_running = True
                scan_id = self._running_scan_id
            return StorageUsageRefreshResponse(
                started=False, reason="already_running", scan_id=scan_id
            )
        if not ask.waits and await asyncio.to_thread(self._fresh, home, params):
            return StorageUsageRefreshResponse(started=False, reason="fresh")
        if not ask.waits:
            # Read before the marker: a result landing after it is the other scan's.
            cache_before = await asyncio.to_thread(_cache_version, home)
            if await asyncio.to_thread(self._scan_runs_elsewhere, home):
                return StorageUsageRefreshResponse(
                    started=False,
                    reason="running_elsewhere",
                    scan_id=self._follow_elsewhere(cache_before),
                )
        return StorageUsageRefreshResponse(started=True, scan_id=self._start(ask))

    def _new_scan_id(self) -> int:
        self._last_scan_id += 1
        return self._last_scan_id

    def _scan_runs_elsewhere(self, home: Path) -> bool:
        """The scanning marker, checked against the lock when it claims a scan:
        a crashed scanner's marker, its pid since reused, must not keep every
        later refresh following a scan that is not there.
        """
        if not files.scanning_elsewhere(home, self._owner):
            return False
        lock = files.ScanLock(home, self._owner)
        if not lock.acquire(time.monotonic() + _LOCK_SETTLE_S):
            return True
        files.clear_scanning_marker(home)
        lock.release()
        return False

    def _fresh(self, home: Path, params: StorageUsageRefreshParams) -> bool:
        if params.max_age_ms is None:
            return False
        self._reload_if_newer(home)
        cached = self._cached
        if cached is None:
            return False
        snapshot = cached.snapshot
        if files.stale_marker_ms(home) > snapshot.scan_started_at:
            return False
        return self._clock_ms() - snapshot.measured_at < params.max_age_ms

    def _running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _start(self, ask: _Ask) -> int:
        """The id of the scan started; called under ``_refresh_lock`` only."""
        self._asked_while_running = False
        scan_id = self._new_scan_id()
        self._running_scan_id = scan_id
        self._task = asyncio.create_task(
            self._run_chain(ask, scan_id), name="vibe-storage-scan"
        )
        return scan_id

    async def _run_chain(self, ask: _Ask, scan_id: int) -> None:
        """One scan, then each follow-up asked for while it ran, in this task:
        the task stays running until none is due, so a refresh never sees an
        idle service between a scan and its follow-up and starts another.
        """
        only_if_stale = False
        while True:
            await self._run(ask, scan_id, only_if_stale=only_if_stale)
            # No await from here to the next scan or the end of the task.
            if self._closed:
                return
            queued, self._queued = self._queued, None
            queued_scan_id, self._queued_scan_id = self._queued_scan_id, None
            if queued is not None:
                ask, only_if_stale = queued, False
                scan_id = (
                    queued_scan_id
                    if queued_scan_id is not None
                    else self._new_scan_id()
                )
            elif self._asked_while_running:
                ask, only_if_stale = _Ask(), True
                scan_id = self._new_scan_id()
            else:
                return
            self._asked_while_running = False
            self._running_scan_id = scan_id

    def _stale_now(self) -> bool:
        cached = self._cached
        return cached is not None and (
            files.stale_marker_ms(self._paths().home) > cached.snapshot.scan_started_at
        )

    async def _run(
        self, ask: _Ask, scan_id: int, *, only_if_stale: bool = False
    ) -> None:
        if only_if_stale and not await asyncio.to_thread(self._stale_now):
            # Nothing changed since the scan a refresh joined: its numbers
            # answer this id too, so a caller holding it is not left waiting.
            await self._publish(await self._settled_read(), scan_id=scan_id)
            return
        try:
            await self._run_scan(ask, scan_id)
        except asyncio.CancelledError:
            raise
        # Broad: whatever failed, clients waiting on "refreshing" must hear the end.
        except Exception as exc:
            logger.warning("Storage usage scan failed", exc_info=exc)
            await self._fail("error", scan_id)

    async def _run_scan(self, ask: _Ask, scan_id: int) -> None:
        home = self._paths().home
        await self._publish(await self.read(), scan_id=scan_id)
        cache_before = await asyncio.to_thread(_cache_version, home)
        lock = files.ScanLock(home, self._owner)
        # A plain refresh still rides out a momentary holder (a refresh
        # checking a stale marker) before following a scan elsewhere.
        wait = self._lock_wait_s if ask.waits else _LOCK_SETTLE_S
        deadline = time.monotonic() + wait
        held = await asyncio.to_thread(
            lock.acquire, deadline, cancelled=lambda: self._closed
        )
        if not held:
            if ask.waits:
                await self._fail("lock_timeout", scan_id)
            else:
                # The other process's result answers this scan's callers.
                self._follow_elsewhere(cache_before, scan_id)
            return
        try:
            await asyncio.to_thread(lock.mark_scanning)
            finished = await self._scan_holding_lock(home, ask, scan_id)
        finally:
            await asyncio.to_thread(lock.release)
        # After the release: a client acting on the result finds no scan running.
        if finished is not None:
            await self._publish(
                finished.response, complete=finished.complete, scan_id=scan_id
            )
            self._telemetry.scan_finished(finished.response, finished.scan)

    async def _scan_holding_lock(
        self, home: Path, ask: _Ask, scan_id: int
    ) -> _Finished | None:
        complete = True
        if ask.wait_for:
            folders = await asyncio.to_thread(
                finished_worktrees, ask.wait_for, self._paths().worktrees_root
            )
            complete = await asyncio.to_thread(
                wait_until_gone, folders, self._removal_wait_s, lambda: self._closed
            )
        await asyncio.to_thread(self._reload_if_newer, home)
        previous = Previous.from_cache(
            None if self._cached is None else self._cached.document
        )
        paths = self._paths()
        request = ScanRequest(
            home=paths.home,
            worktrees_root=paths.worktrees_root,
            session_roots=paths.session_roots,
            now_ms=self._clock_ms(),
            time_limit_s=self._time_limit_s,
            full=ask.full,
            allow_daily_full=not ask.wait_for,
            previous=previous,
            submitted_at=time.time(),
        )
        try:
            # Bounds the lock's hold: the budget starts when the worker picks
            # the job up and is checked between folders only. The worker's
            # walk may go on; its result is dropped.
            async with asyncio.timeout(self._time_limit_s + self._scan_margin_s):
                document = await self._worker.run(SCAN_JOB, request.to_args())
        except TimeoutError:
            await self._fail("timeout", scan_id)
            return None
        except BackgroundWorkerError as exc:
            logger.warning("Storage usage scan failed", exc_info=exc)
            await self._fail("error", scan_id)
            return None
        try:
            await asyncio.to_thread(files.write_cache, home, document)
        except OSError as exc:
            logger.warning("Storage usage scan failed", exc_info=exc)
            await self._fail("error", scan_id)
            return None
        self._failed = False
        self._adopt(document, await asyncio.to_thread(files.cache_mtime_ns, home))
        response = await self.read()
        if response.status == "refreshing":
            response = response.model_copy(update={"status": document["status"]})
        return _Finished(response, complete, ScanOutcome.from_document(document))

    async def _settled_read(self) -> StorageUsageReadResponse:
        response = await self.read()
        if response.status != "refreshing":
            return response
        return response.model_copy(
            update={"status": self._settled_status(self._cached)}
        )

    async def _fail(self, reason: str, scan_id: int) -> None:
        self._failed = True
        logger.info("Storage usage scan did not run: %s", reason)
        response = await self.read()
        await self._publish(
            response.model_copy(update={"status": "failed"}), scan_id=scan_id
        )
        self._telemetry.scan_failed(response)

    def _follow_elsewhere(
        self, cache_before: _CacheVersion | None, scan_id: int | None = None
    ) -> int:
        """Forward the other process's result once its lock is released; the
        id that result carries. One follower at a time: a caller arriving
        while one runs gets its id, and a scan of ours that found the lock
        held (``scan_id``) gets the result under its own id as well.

        The result is ``failed`` when no cache other than ``cache_before``
        (the one there before following) landed: the other scan failed,
        outlasted the wait, or never started.
        """
        if self._follower is not None and not self._follower.done():
            if scan_id is None:
                return self._follower_scan_ids[0]
            self._follower_scan_ids.append(scan_id)
            return scan_id
        follower_scan_id = self._new_scan_id() if scan_id is None else scan_id
        self._follower_scan_ids = [follower_scan_id]

        async def follow() -> None:
            home = self._paths().home
            await self._publish(await self.read(), scan_id=follower_scan_id)
            # Watches the marker, never the lock: only a process about to
            # scan takes it, so following cannot block a scan from starting.
            started = time.monotonic()
            deadline = started + self._lock_wait_s
            while not self._closed and time.monotonic() < deadline:
                if not await asyncio.to_thread(
                    files.scanning_elsewhere, home, self._owner
                ) and (
                    # A holder writes its marker just after taking the lock.
                    time.monotonic() - started >= _LOCK_SETTLE_S
                    or await asyncio.to_thread(_cache_landed, home, cache_before)
                ):
                    break
                await asyncio.sleep(_FOLLOW_POLL_S)
            # Settled: a scan of ours started meanwhile has its own id.
            result = await self._settled_read()
            if not await asyncio.to_thread(_cache_landed, home, cache_before):
                result = result.model_copy(update={"status": "failed"})
            # Until none is left: an id handed out while the result is read or
            # published still gets it, since this follower is still running.
            answered = 0
            while answered < len(self._follower_scan_ids):
                scan = self._follower_scan_ids[answered]
                answered += 1
                await self._publish(result, scan_id=scan)

        self._follower = asyncio.create_task(follow(), name="vibe-storage-follow")
        return follower_scan_id

    async def _publish(
        self,
        response: StorageUsageReadResponse,
        *,
        complete: bool = True,
        scan_id: int | None = None,
    ) -> None:
        self._event_id += 1
        params = StorageUsageUpdatedParams(
            **{name: getattr(response, name) for name in type(response).model_fields},
            event_id=self._event_id,
            complete=complete,
            scan_id=scan_id,
        )
        for notify in tuple(self._listeners.values()):
            try:
                await notify(STORAGE_USAGE_UPDATED, params)
            # Broad: one closed channel must not keep the others from the result.
            except Exception:
                logger.debug("Could not deliver storage/usage/updated", exc_info=True)

    def _paths(self) -> _Paths:
        """Resolved alike, so a folder under a symlinked home matches its owner."""
        roots = [self._default_session_root(), *sorted(self._extra_roots)]
        unique: dict[Path, None] = {}
        for root in roots:
            unique.setdefault(root.resolve(), None)
        return _Paths(
            home=self._home().resolve(),
            worktrees_root=self._worktrees_root().resolve(),
            session_roots=tuple(unique),
        )


type _CacheVersion = tuple[int, int]


def _cache_version(home: Path) -> _CacheVersion | None:
    """The cache file's modification time and inode: a write replaces the
    file, so the inode changes even within one tick of a coarse clock.
    """
    try:
        stat = (home / files.CACHE_FILENAME).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_ino


def _cache_landed(home: Path, before: _CacheVersion | None) -> bool:
    """Whether a cache other than ``before`` is there."""
    version = _cache_version(home)
    return version is not None and version != before


@dataclass(frozen=True, slots=True)
class _Paths:
    home: Path
    worktrees_root: Path
    session_roots: tuple[Path, ...]


__all__ = [
    "LOCK_WAIT_S",
    "REMOVAL_WAIT_S",
    "SCAN_MARGIN_S",
    "TIME_LIMIT_S",
    "StorageUsageService",
]
