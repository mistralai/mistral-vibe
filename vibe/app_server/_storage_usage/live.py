"""What every read computes fresh: the disk, which sessions are open, and the
archive suggestion that follows from both.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import shutil

from vibe.app_server.protocol import (
    StorageArchiveSuggestion,
    StorageDiskSpace,
    StorageUsageSnapshot,
)
from vibe.core.utils.file_lock import acquire_file_lock, release_file_lock

LOW_DISK_CAP_BYTES = 20 * 10**9
LOW_DISK_SHARE = 0.10
SUGGEST_AFTER_DAYS = 30
_DAY_MS = 24 * 60 * 60 * 1000
# The on-disk lease format (active/, {id}.lock, .registry) is shared with
# vibe/core/session/session_lease.py and its twin in
# agents/harness/harness/runtimes/python/.../vibe/_storage.py; change all three.
_LEASES = "active"
_LEASE_SUFFIX = ".lock"
_REGISTRY = ".registry"


def low_disk_threshold(total_bytes: int) -> int:
    """The lower of 10% of the disk and 20 GB."""
    return min(int(total_bytes * LOW_DISK_SHARE), LOW_DISK_CAP_BYTES)


def disk_space(path: Path, now_ms: int) -> StorageDiskSpace:
    """The disk holding ``path``, or its nearest existing parent."""
    while not path.exists() and path.parent != path:
        path = path.parent
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return StorageDiskSpace(read_at=now_ms)
    threshold = low_disk_threshold(usage.total)
    return StorageDiskSpace(
        total_bytes=usage.total,
        free_bytes=usage.free,
        low_threshold_bytes=threshold,
        is_low=usage.free < threshold,
        read_at=now_ms,
    )


def open_sessions(roots: Iterable[Path]) -> set[str]:
    """Sessions whose lease a live process holds.

    One pass per store under the lease directory lock that owners acquire
    under, trying each existing lease file without blocking, as the harness's
    ``session_is_live`` does for one (design doc 4.4). It never deletes a file
    and never creates a lease, so it cannot make an open fail or let a second
    owner in; taking the directory lock once keeps a read with hundreds of
    leases fast. A store whose directory lock cannot be taken counts every
    lease in it as held, as ``session_is_live`` reports a lease it cannot
    probe as live.
    """
    found: set[str] = set()
    for root in roots:
        directory = root / _LEASES
        try:
            with _lease_directory_lock(directory):
                found.update(_held_leases(directory))
        except FileNotFoundError:
            continue
        except OSError:
            found.update(_every_lease(directory))
    return found


def _every_lease(directory: Path) -> set[str]:
    try:
        names = os.listdir(directory)
    except OSError:
        return set()
    return {
        name.removesuffix(_LEASE_SUFFIX)
        for name in names
        if name.endswith(_LEASE_SUFFIX)
    }


@contextmanager
def _lease_directory_lock(directory: Path) -> Iterator[None]:
    # As the harness does: no touch, so the registry's mtime stays unchanged
    # for readers polling it. Never creates the lease directory itself.
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    file = os.fdopen(os.open(directory / _REGISTRY, flags, 0o600), "r+b")
    try:
        acquire_file_lock(file, blocking=True)
    except BaseException:
        file.close()
        raise
    try:
        yield
    finally:
        try:
            release_file_lock(file)
        finally:
            file.close()


def _held_leases(directory: Path) -> set[str]:
    # Plain strings and unbuffered files: a read probes every lease, and the
    # pathlib and buffering overhead per file was most of a read's time.
    folder = os.fspath(directory)
    held: set[str] = set()
    for name in os.listdir(folder):
        if not name.endswith(_LEASE_SUFFIX):
            continue
        try:
            file = open(os.path.join(folder, name), "r+b", buffering=0)
        except FileNotFoundError:
            continue
        except PermissionError:
            # The fail-safe direction: open sessions are never suggested.
            held.add(name.removesuffix(_LEASE_SUFFIX))
            continue
        except OSError:
            continue
        with file:
            try:
                acquire_file_lock(file)
            except BlockingIOError:
                held.add(name.removesuffix(_LEASE_SUFFIX))
                continue
            release_file_lock(file)
    return held


def archive_suggestion(
    snapshot: StorageUsageSnapshot,
    open_ids: set[str],
    never_suggested: set[str],
    now_ms: int,
) -> StorageArchiveSuggestion:
    """Sessions not archived, not open, idle for 30 days or more, and the space
    archiving them all would free: each worktree whose every session would then
    be archived. Histories stay, so they never count.
    """
    cutoff = now_ms - SUGGEST_AFTER_DAYS * _DAY_MS
    suggested = [
        row.session_id
        for row in snapshot.sessions
        if not row.archived
        and row.session_id not in open_ids
        and row.session_id not in never_suggested
        and row.last_activity_at <= cutoff
    ]
    chosen = set(suggested)
    archived = {row.session_id for row in snapshot.sessions if row.archived}
    reclaimable = sum(
        worktree.bytes
        for worktree in snapshot.worktrees
        if worktree.bytes is not None
        and not worktree.leftover
        and worktree.session_ids
        and all(item in chosen or item in archived for item in worktree.session_ids)
        and any(item in chosen for item in worktree.session_ids)
    )
    return StorageArchiveSuggestion(
        inactive_days=SUGGEST_AFTER_DAYS,
        session_ids=suggested,
        reclaimable_bytes=reclaimable,
    )


__all__ = [
    "LOW_DISK_CAP_BYTES",
    "SUGGEST_AFTER_DAYS",
    "archive_suggestion",
    "disk_space",
    "low_disk_threshold",
    "open_sessions",
]
