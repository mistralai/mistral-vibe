"""The storage scan, a job of the background worker process.

It walks every session history, then the data no session owns, then the
worktrees, re-measuring only those a change signal says may have changed
(``signals.py``), all of them on the daily full scan and on Refresh. Past the
time limit a worktree keeps its last size, or none ("not measured yet").

Read only, and pure: the app server passes what it remembered from the last
scan and gets the next cache document back, which it writes itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

from vibe.app_server._background_worker_process import Job, JobSet
from vibe.app_server._storage_usage.attribution import (
    Layout,
    SessionFolder,
    WorktreeFolder,
    read_sessions,
    read_worktrees,
)
from vibe.app_server._storage_usage.signals import (
    Signals,
    git_admin_dir,
    is_copy_on_write,
    worktree_signals,
)
from vibe.app_server._storage_usage.sizes import (
    Budget,
    Measured,
    SeenFiles,
    measure,
    measure_excluding,
    reads_private_sizes,
)
from vibe.app_server.protocol import (
    StorageOtherKind,
    StorageOtherUsage,
    StorageSessionUsage,
    StorageSizeKind,
    StorageUsageSnapshot,
    StorageUsageTotals,
    StorageWorktreeUsage,
)

CACHE_VERSION = 1
SCAN_JOB = "storage.scan"
DAILY_FULL_SCAN_MS = 24 * 60 * 60 * 1000
_SCRATCHPAD = "scratchpad"


@dataclass(frozen=True, slots=True)
class WorktreeMeasure:
    bytes: int | None
    measured_at: int | None
    signals: Signals | None
    # Read as private sizes; kept so a later scan reusing it reports it right.
    private: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "measuredAt": self.measured_at,
            "signals": None if self.signals is None else self.signals.to_json(),
            "private": self.private,
        }

    @classmethod
    def from_json(cls, value: object) -> WorktreeMeasure | None:
        if not isinstance(value, dict):
            return None
        size, measured_at = value.get("bytes"), value.get("measuredAt")
        if not (size is None or _is_int(size)) or not (
            measured_at is None or _is_int(measured_at)
        ):
            return None
        return cls(
            size,
            measured_at,
            Signals.from_json(value.get("signals")),
            value.get("private") is True,
        )


@dataclass(frozen=True, slots=True)
class Previous:
    """What the last scan left in the cache."""

    worktrees: dict[str, WorktreeMeasure] = field(default_factory=dict)
    child_roots: dict[str, str] = field(default_factory=dict)
    last_full_scan_at: int | None = None

    @classmethod
    def from_cache(cls, document: dict[str, Any] | None) -> Previous:
        if document is None:
            return cls()
        raw_worktrees = document.get("worktrees")
        worktrees = (
            {
                key: measured
                for key, value in raw_worktrees.items()
                if isinstance(key, str)
                and (measured := WorktreeMeasure.from_json(value)) is not None
            }
            if isinstance(raw_worktrees, dict)
            else {}
        )
        raw_roots = document.get("childRoots")
        child_roots = (
            {
                key: value
                for key, value in raw_roots.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            if isinstance(raw_roots, dict)
            else {}
        )
        last_full = document.get("lastFullScanAt")
        return cls(worktrees, child_roots, last_full if _is_int(last_full) else None)

    def to_args(self) -> dict[str, Any]:
        return {
            "worktrees": {
                key: value.to_json() for key, value in self.worktrees.items()
            },
            "childRoots": self.child_roots,
            "lastFullScanAt": self.last_full_scan_at,
        }


@dataclass(frozen=True, slots=True)
class ScanRequest:
    home: Path
    worktrees_root: Path
    session_roots: tuple[Path, ...]
    now_ms: int
    time_limit_s: float
    # The Refresh button: re-measure every worktree.
    full: bool = False
    # False for a scan after an archive: it never becomes the daily full one.
    allow_daily_full: bool = True
    previous: Previous = field(default_factory=Previous)
    # ``time.time()`` at submit: the time limit counts the wait in the
    # worker's queue too.
    submitted_at: float | None = None

    def to_args(self) -> dict[str, Any]:
        return {
            "home": str(self.home),
            "worktreesRoot": str(self.worktrees_root),
            "sessionRoots": [str(root) for root in self.session_roots],
            "now": self.now_ms,
            "timeLimitS": self.time_limit_s,
            "full": self.full,
            "allowDailyFull": self.allow_daily_full,
            "previous": self.previous.to_args(),
            "submittedAt": self.submitted_at,
        }

    @classmethod
    def from_args(cls, args: dict[str, Any]) -> ScanRequest:
        return cls(
            home=Path(args["home"]),
            worktrees_root=Path(args["worktreesRoot"]),
            session_roots=tuple(Path(root) for root in args["sessionRoots"]),
            now_ms=int(args["now"]),
            time_limit_s=float(args["timeLimitS"]),
            full=bool(args["full"]),
            allow_daily_full=bool(args["allowDailyFull"]),
            previous=Previous.from_cache(args.get("previous")),
            submitted_at=args.get("submittedAt"),
        )

    def is_full(self) -> bool:
        if self.full:
            return True
        last = self.previous.last_full_scan_at
        return self.allow_daily_full and (
            last is None or self.now_ms - last >= DAILY_FULL_SCAN_MS
        )

    def remaining_s(self) -> float:
        """The time limit less the wait since submit, within ``[0, limit]``."""
        if self.submitted_at is None:
            return self.time_limit_s
        waited = time.time() - self.submitted_at
        return min(max(self.time_limit_s - waited, 0.0), self.time_limit_s)


@dataclass(slots=True)
class _Progress:
    budget: Budget
    seen: SeenFiles = field(default_factory=SeenFiles)
    skipped: int = 0
    cut: bool = False
    remeasured: int = 0

    def take(self, measured: Measured) -> Measured:
        self.skipped += measured.skipped
        self.cut = self.cut or not measured.complete
        return measured


def scan(request: ScanRequest, budget: Budget | None = None) -> dict[str, Any]:
    """The next cache document."""
    started = time.monotonic()
    budget = budget or Budget(deadline=started + request.remaining_s())
    progress = _Progress(budget)
    full = request.is_full()
    layout = Layout()
    read_sessions(list(request.session_roots), request.previous.child_roots, layout)
    read_worktrees(request.worktrees_root, layout)
    histories = {
        session_id: _measure_history(session, progress)
        for session_id, session in layout.sessions.items()
    }
    other = _measure_other(request, layout, progress)
    worktree_rows, measures, all_measured = _measure_worktrees(
        request, layout, histories, full, progress
    )
    sessions = [
        _session_row(session, histories[session_id])
        for session_id, session in layout.sessions.items()
    ]
    _link_sessions(sessions, worktree_rows)
    snapshot_full = full and all_measured
    snapshot = StorageUsageSnapshot(
        scan_started_at=request.now_ms,
        measured_at=request.now_ms + int((time.monotonic() - started) * 1000),
        duration_ms=int((time.monotonic() - started) * 1000),
        full=snapshot_full,
        size_kind=_size_kind(measures.values()),
        skipped_entry_count=progress.skipped,
        not_measured_count=sum(1 for row in worktree_rows if row.bytes is None),
        totals=_totals(sessions, worktree_rows, other),
        sessions=sorted(sessions, key=lambda row: row.history_bytes, reverse=True),
        worktrees=worktree_rows,
        other=other,
    )
    return {
        "version": CACHE_VERSION,
        "status": "partial" if progress.skipped or progress.cut else "up_to_date",
        "snapshot": snapshot.model_dump(mode="json", by_alias=True),
        "lastFullScanAt": (
            request.now_ms if snapshot_full else request.previous.last_full_scan_at
        ),
        "worktrees": {key: value.to_json() for key, value in measures.items()},
        "childRoots": layout.child_roots,
        "neverSuggested": sorted(
            session_id
            for session_id, session in layout.sessions.items()
            if not session.has_meta
        ),
        # For the scan event only: not part of the snapshot clients read.
        "remeasured": progress.remeasured,
        "cutByTimeLimit": progress.cut,
        # Asked to re-measure everything, even when the limit then cut it.
        "fullScan": full,
        "durationMs": int((time.monotonic() - started) * 1000),
    }


def storage_jobs() -> JobSet:
    def run(args: dict[str, Any]) -> dict[str, Any]:
        return scan(ScanRequest.from_args(args))

    return {SCAN_JOB: Job(run)}


def _measure_history(session: SessionFolder, progress: _Progress) -> Measured:
    measured = progress.take(measure(session.path, progress.budget, progress.seen))
    for child in session.children:
        measured.add(progress.take(measure(child, progress.budget, progress.seen)))
    return measured


def _measure_other(
    request: ScanRequest, layout: Layout, progress: _Progress
) -> list[StorageOtherUsage]:
    sizes: dict[StorageOtherKind, int] = {}
    paths: dict[StorageOtherKind, Path] = {}

    def add(kind: StorageOtherKind, path: Path, measured: Measured) -> None:
        sizes[kind] = sizes.get(kind, 0) + progress.take(measured).bytes
        paths.setdefault(kind, path)

    budget, seen = progress.budget, progress.seen
    for child in layout.orphan_children:
        add("orphan_history", child.parent, measure(child, budget, seen))
    scratchpad = request.home / _SCRATCHPAD
    add("shared_scratchpad", scratchpad, measure(scratchpad, budget, seen))
    for kind, path in layout.worktree_other:
        allocated = kind == "worktree_template"
        add(kind, path, measure(path, budget, seen, allocated=allocated))
    owned = frozenset(
        path
        for session in layout.sessions.values()
        for path in (session.path, *session.children)
    ) | frozenset(layout.orphan_children)
    excluded = owned | {request.worktrees_root, scratchpad}
    add(
        "logs_and_caches",
        request.home,
        measure_excluding(request.home, excluded, budget, seen),
    )
    for root in request.session_roots:
        if request.home in root.parents or root == request.home:
            continue
        add(
            "logs_and_caches",
            request.home,
            measure_excluding(root, owned, budget, seen),
        )
    return [
        StorageOtherUsage(kind=kind, path=str(paths[kind]), bytes=size)
        for kind, size in sizes.items()
    ]


def _measure_worktrees(
    request: ScanRequest,
    layout: Layout,
    histories: dict[str, Measured],
    full: bool,
    progress: _Progress,
) -> tuple[list[StorageWorktreeUsage], dict[str, WorktreeMeasure], bool]:
    by_dir = {session.path: session for session in layout.sessions.values()}
    rows: dict[str, StorageWorktreeUsage] = {}
    measures: dict[str, WorktreeMeasure] = {}
    all_measured = True
    # Not measured yet first, then the oldest measurement: a scan the time
    # limit cuts leaves the next one the worktrees it did not reach.
    for worktree in sorted(
        layout.worktrees, key=lambda item: _measure_order(request.previous, item)
    ):
        session_ids = [
            by_dir[folder].session_id
            for folder in sorted(worktree.session_dirs)
            if folder in by_dir
        ]
        admin = git_admin_dir(worktree.path)
        signals = worktree_signals(
            worktree.path,
            max((histories[item].newest_mtime_ms for item in session_ids), default=0),
            admin,
        )
        previous = request.previous.worktrees.get(worktree.id)
        result = previous or WorktreeMeasure(None, None, None)
        if _needs_measure(previous, signals, full):
            measured = progress.take(
                measure(worktree.path, progress.budget, progress.seen)
            )
            if measured.complete:
                progress.remeasured += 1
                result = WorktreeMeasure(
                    measured.bytes, request.now_ms, signals, measured.private
                )
            else:
                all_measured = False
        measures[worktree.id] = result
        rows[worktree.id] = _worktree_row(worktree, result, session_ids, layout, admin)
    return (
        [rows[worktree.id] for worktree in layout.worktrees],
        {worktree.id: measures[worktree.id] for worktree in layout.worktrees},
        all_measured,
    )


def _measure_order(previous: Previous, worktree: WorktreeFolder) -> tuple[bool, int]:
    measured = previous.worktrees.get(worktree.id)
    if measured is None or measured.bytes is None or measured.measured_at is None:
        return (False, 0)
    return (True, measured.measured_at)


def _size_kind(measures: Iterable[WorktreeMeasure]) -> StorageSizeKind:
    """Private only when every worktree size shown was read as one: the
    archive estimate rests on them.
    """
    sized = [item for item in measures if item.bytes is not None]
    if not reads_private_sizes() or not all(item.private for item in sized):
        return "allocated"
    return "private"


def _needs_measure(
    previous: WorktreeMeasure | None, signals: Signals, full: bool
) -> bool:
    if previous is None or previous.bytes is None or previous.signals is None:
        return True
    return full or previous.signals != signals


def _worktree_row(
    worktree: WorktreeFolder,
    measured: WorktreeMeasure,
    session_ids: list[str],
    layout: Layout,
    admin: Path | None,
) -> StorageWorktreeUsage:
    active = any(not layout.sessions[item].archived for item in session_ids)
    return StorageWorktreeUsage(
        id=worktree.id,
        path=str(worktree.path),
        branch=worktree.branch,
        repo_name=worktree.repo_name,
        bytes=measured.bytes,
        measured_at=measured.measured_at,
        session_ids=session_ids,
        leftover=not active,
        managed=worktree.managed,
        is_copy_on_write=is_copy_on_write(admin),
    )


def _session_row(session: SessionFolder, history: Measured) -> StorageSessionUsage:
    return StorageSessionUsage(
        session_id=session.session_id,
        title=session.title,
        cwd=session.cwd,
        archived=session.archived,
        last_activity_at=(
            session.last_activity_at
            if session.last_activity_at is not None
            else history.newest_mtime_ms
        ),
        history_bytes=history.bytes,
        history_path=str(session.path),
    )


def _link_sessions(
    sessions: list[StorageSessionUsage], worktrees: list[StorageWorktreeUsage]
) -> None:
    """A session in several claims takes the worktree its folder is in."""
    rows = {row.session_id: row for row in sessions}
    for worktree in worktrees:
        for session_id in worktree.session_ids:
            row = rows[session_id]
            inside = row.cwd is not None and Path(row.cwd).is_relative_to(worktree.path)
            if row.worktree_id is None or inside:
                row.worktree_id = worktree.id


def _totals(
    sessions: list[StorageSessionUsage],
    worktrees: list[StorageWorktreeUsage],
    other: list[StorageOtherUsage],
) -> StorageUsageTotals:
    active_histories = sum(row.history_bytes for row in sessions if not row.archived)
    archived = sum(row.history_bytes for row in sessions if row.archived)
    active_worktrees = sum(
        row.bytes for row in worktrees if row.bytes is not None and not row.leftover
    )
    leftover = sum(
        row.bytes for row in worktrees if row.bytes is not None and row.leftover
    )
    other_bytes = sum(row.bytes for row in other)
    active = active_histories + active_worktrees
    return StorageUsageTotals(
        active_sessions_bytes=active,
        archived_sessions_bytes=archived,
        leftover_worktrees_bytes=leftover,
        other_vibe_data_bytes=other_bytes,
        total_bytes=active + archived + leftover + other_bytes,
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


__all__ = [
    "CACHE_VERSION",
    "DAILY_FULL_SCAN_MS",
    "SCAN_JOB",
    "Previous",
    "ScanRequest",
    "WorktreeMeasure",
    "scan",
    "storage_jobs",
]
