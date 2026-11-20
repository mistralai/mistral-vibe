"""``storage/leftovers/clean``: remove leftover worktrees and unknown folders.

What the scan offers (``StorageLeftoverCleanup``) is only a list of ids; every
check that decides a removal runs again here, at removal time:

- A worktree is kept while a session folder its claim names is still on
  disk, and while its claim names no session at all and was written less
  than ``RESERVATION_GRACE_S`` ago (a session may be about to join it).
  Otherwise it goes through ``ManagedWorktree.release``, the plain
  ``workspace/git/worktrees/remove`` path: kept while a live process holds
  it or it is still being created. When it has uncommitted changes,
  untracked files or commits since it was created, they are saved before
  the folder goes as one commit on top of its HEAD, at
  ``refs/vibe/reaped/<name>``, so commits not pushed stay reachable there;
  ignored files, and commits no longer reachable from HEAD, are not. The
  branch goes with the folder when Vibe created it. A later removal of a
  worktree of the same name replaces the ref. No recovery record is
  written: the ref and a log line are the only trace of the removal.
- An unknown folder (no claim, no ``.git``, not named like Vibe's own
  ``.claims``, ``.trash`` or ``.templates``) must sit directly in a worktree
  bucket, strictly inside the worktrees root, be a real folder (never a
  symlink), have no claim, holder or recovery record, hold no open session's
  working directory, and have no folder in it, at any depth, changed in the
  last ``RESERVATION_GRACE_S``. Only folders are looked at, never files:
  creating, renaming or deleting a file changes its folder's time, so a
  build adding files shows, but a file rewritten in place does not. That
  walk stops after ``FRESH_CHECK_BUDGET_S`` or ``FRESH_CHECK_MAX_ENTRIES``
  entries; a folder whose walk did not finish is kept as
  ``too_large_to_check``, with its top folder's time in
  ``CleanProgress.unverified`` so the service stops offering it until that
  time changes. Otherwise it moves to the
  trash under the prune lock and is deleted once the lock is released,
  without following symlinks.

Creating a worktree does not take the prune lock: a new worktree's empty
folder, and its claim before any session joins it, are protected by the
grace period alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum, auto
import json
import os
from pathlib import Path
import stat
import time
from uuid import uuid4

from vibe.app_server._storage_usage import live
from vibe.app_server._storage_usage.files import touch_stale_marker
from vibe.app_server._storage_usage.rules import (
    BUCKET_PATTERN,
    META_FILENAME,
    RESERVATION_GRACE_S,
    UNIFIED_DIR,
    meta_cwd,
    unattached_and_young,
)
from vibe.app_server.protocol import (
    StorageLeftoverKept,
    StorageLeftoverKeptReason,
    StorageLeftoversCleanResponse,
)
from vibe.core.git.errors import GitError
from vibe.core.git.worktree import ManagedWorktree, WorktreeReleaseOutcome
from vibe.core.git.worktree.record import (
    CLAIMS_DIR_NAME,
    TEMPLATES_DIR_NAME,
    TRASH_DIR_NAME,
    WorktreeClaim,
    WorktreeRecordError,
    worktree_prune_lock,
)
from vibe.core.trusted_folders import TrustedFoldersManager
from vibe.observability.logging import logger

_RESERVED = frozenset({CLAIMS_DIR_NAME, TEMPLATES_DIR_NAME, TRASH_DIR_NAME})
# In an id, a Windows separator or a NUL could name another path or none.
_FORBIDDEN = frozenset({"\\", "\x00"})
# Bounds on the walk that looks for a recent change in an unknown folder,
# measured at about 30,000 folders a second: a folder of about 150,000
# finishes inside them. Neither Desktop nor the CLI times the request out.
FRESH_CHECK_BUDGET_S = 5.0
FRESH_CHECK_MAX_ENTRIES = 1_000_000
_ID_PARTS = 2


@dataclass(frozen=True, slots=True)
class CleanRequest:
    worktrees_root: Path
    session_roots: tuple[Path, ...]
    worktree_ids: tuple[str, ...]
    folder_ids: tuple[str, ...]
    trust_store: TrustedFoldersManager | None = None


@dataclass(slots=True)
class CleanProgress:
    """What ``clean`` decided so far, read by a caller whose ``clean``
    raised: an id in ``removed`` is gone even if the call did not finish.
    """

    removed: list[str] = field(default_factory=list)
    kept: list[StorageLeftoverKept] = field(default_factory=list)
    # Folder ids kept as ``too_large_to_check``, with their top folder's
    # ``st_mtime_ns`` when the walk gave up.
    unverified: dict[str, int] = field(default_factory=dict)


def clean(
    request: CleanRequest, progress: CleanProgress | None = None
) -> StorageLeftoversCleanResponse:
    """Each id removed or kept with its reason; never raises for one item."""
    root = request.worktrees_root.resolve()
    progress = CleanProgress() if progress is None else progress
    removed = progress.removed
    kept = progress.kept
    for item in dict.fromkeys(request.worktree_ids):
        reason = remove_worktree_safely(root, item, request.trust_store)
        if reason is None:
            removed.append(item)
        else:
            kept.append(StorageLeftoverKept(id=item, reason=reason))
    if request.folder_ids:
        _clean_folders(request, root, progress)
    if removed:
        touch_stale_marker()
        # Outside the prune lock: deleting a large folder takes long, and
        # ``rmtree`` never follows a symlink inside it. What stays in the
        # trash is deleted by the next removal or sweep.
        try:
            ManagedWorktree.empty_trash()
        except OSError:
            logger.warning("Failed to empty the worktree trash", exc_info=True)
    return StorageLeftoversCleanResponse(removed=list(removed), kept=list(kept))


def _clean_folders(request: CleanRequest, root: Path, progress: CleanProgress) -> None:
    def keep(item: str, reason: StorageLeftoverKeptReason) -> None:
        progress.kept.append(StorageLeftoverKept(id=item, reason=reason))

    # One folder at a time: its deep walk, then right away the quick checks
    # and the move, so a walk is never a whole request old when its folder
    # moves. The walk runs before the prune lock, which attaching a session to
    # a worktree waits on; the lock covers the quick checks and the move.
    for item in dict.fromkeys(request.folder_ids):
        path = _inside(root, item)
        if not isinstance(path, Path):
            keep(item, path)
            continue
        match _freshness_safely(path, time.time()):
            case Freshness.CHANGED:
                keep(item, "in_use")
                continue
            case Freshness.UNVERIFIED:
                keep(item, "too_large_to_check")
                if (mtime := _mtime_ns(path)) is not None:
                    progress.unverified[item] = mtime
                continue
            case Freshness.UNCHANGED:
                pass
        reason = _move_folder(request, root, item)
        if reason is None:
            progress.removed.append(item)
        else:
            keep(item, reason)


def _move_folder(
    request: CleanRequest, root: Path, item: str
) -> StorageLeftoverKeptReason | None:
    """The quick checks and the move of one folder, under the prune lock;
    never raises.
    """
    try:
        open_cwds = _open_session_cwds(request.session_roots)
        with worktree_prune_lock():
            return _remove_folder(root, item, open_cwds)
    # Broad: one folder failing, or the lock, must not keep the others.
    except Exception:
        logger.warning("Failed to clean up folder %s", item, exc_info=True)
        return "error"


def _inside(root: Path, item: str) -> Path | StorageLeftoverKeptReason:  # noqa: PLR0911 - one return per refusal
    """The folder ``item`` names, directly in a bucket of ``root``; else why
    not: ``unsafe`` when the id or what is on disk could lead anywhere else.
    """
    parts = item.split("/")
    if len(parts) != _ID_PARTS:
        return "unsafe"
    bucket, name = parts
    if (
        not BUCKET_PATTERN.match(bucket)
        or bucket in _RESERVED
        or name in _RESERVED
        or name in {"", ".", ".."}
        or not _FORBIDDEN.isdisjoint(item)
    ):
        return "unsafe"
    bucket_path = root / bucket
    path = bucket_path / name
    try:
        bucket_mode = os.lstat(bucket_path).st_mode
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return "not_found"
    # ValueError: a name no path may hold.
    except ValueError:
        return "unsafe"
    except OSError:
        return "error"
    if not stat.S_ISDIR(bucket_mode) or not stat.S_ISDIR(mode):
        return "unsafe"
    if path.resolve().parent != bucket_path or bucket_path.resolve().parent != root:
        return "unsafe"
    return path


def remove_worktree_safely(
    root: Path, item: str, trust_store: TrustedFoldersManager | None
) -> StorageLeftoverKeptReason | None:
    """``remove_worktree``, an unexpected failure kept as ``error``."""
    try:
        return remove_worktree(root, item, trust_store)
    # Broad: one worktree failing must not keep the others.
    except Exception:
        logger.warning("Failed to clean up worktree %s", item, exc_info=True)
        return "error"


def _freshness_safely(path: Path, now_s: float) -> Freshness:
    try:
        return freshness(path, now_s)
    # Broad: a check that failed counts as a recent change, which keeps it.
    except Exception:
        logger.warning("Failed to check folder %s", path, exc_info=True)
        return Freshness.CHANGED


def _mtime_ns(path: Path) -> int | None:
    try:
        return os.lstat(path).st_mtime_ns
    except OSError:
        return None


def remove_worktree(  # noqa: PLR0911 - one return per kept reason
    root: Path, item: str, trust_store: TrustedFoldersManager | None
) -> StorageLeftoverKeptReason | None:
    path = _inside(root, item)
    if not isinstance(path, Path):
        return path
    claim = WorktreeClaim(bucket=path.parent.name, name=path.name)
    record = claim.read()
    if record is None:
        return "unsafe"
    if any(folder.exists() for folder in record.session_dirs):
        return "claimed"
    if unattached_and_young(claim, record, time.time(), RESERVATION_GRACE_S):
        return "in_use"
    managed = ManagedWorktree(claim=claim)
    if managed.root != path:
        return "unsafe"
    try:
        release = managed.release(trust_store=trust_store)
    # WorktreeRecordError: a claim the release refuses, such as a holder
    # file that is a symlink.
    except (GitError, OSError, WorktreeRecordError) as exc:
        logger.warning("Failed to clean up worktree %s: %s", item, exc)
        return "error"
    match release.outcome:
        case WorktreeReleaseOutcome.REMOVED | WorktreeReleaseOutcome.NOT_FOUND:
            return None
        case WorktreeReleaseOutcome.KEPT_IN_USE:
            return "in_use"
        case WorktreeReleaseOutcome.KEPT_DIRTY:
            return "kept_dirty"
        case _:
            return "unsafe"


def _remove_folder(  # noqa: PLR0911 - one return per kept reason
    root: Path, item: str, open_cwds: list[Path]
) -> StorageLeftoverKeptReason | None:
    path = _inside(root, item)
    if not isinstance(path, Path):
        return path
    claim = WorktreeClaim(bucket=path.parent.name, name=path.name)
    if claim.read() is not None or claim.has_recovery() or claim.directory.exists():
        return "claimed"
    if os.path.lexists(path / ".git"):
        return "unsafe"
    try:
        if time.time() - os.lstat(path).st_mtime < RESERVATION_GRACE_S:
            return "in_use"
    except OSError:
        return "not_found"
    if any(cwd == path or path in cwd.parents for cwd in open_cwds):
        return "in_use"
    # The deep walk ran before the lock; only the top folder is read again.
    trash = WorktreeClaim.trash_root()
    target = trash / f"unknown-{path.parent.name}-{path.name}-{uuid4().hex}"
    try:
        trash.mkdir(parents=True, exist_ok=True)
        path.rename(target)
    except OSError as exc:
        logger.warning("Failed to clean up folder %s: %s", item, exc)
        return "error"
    # Only the rename runs under the prune lock: ``clean`` empties the trash
    # once it is released.
    return None


class Freshness(Enum):
    UNCHANGED = auto()
    CHANGED = auto()
    # The walk ran out of time or entries before it could tell.
    UNVERIFIED = auto()


def freshness(
    path: Path,
    now_s: float,
    *,
    budget_s: float | None = None,
    max_entries: int | None = None,
) -> Freshness:
    """Whether ``path`` or a folder in it, at any depth, changed less than
    ``RESERVATION_GRACE_S`` before ``now_s``. A folder that cannot be read
    counts as changed. Folders only, never following a symlink or leaving
    ``path``'s device.
    """
    cutoff = now_s - RESERVATION_GRACE_S
    try:
        top = os.lstat(path)
    except OSError:
        # Gone: the removal reports it.
        return Freshness.UNCHANGED
    if top.st_mtime >= cutoff:
        return Freshness.CHANGED
    budget = FRESH_CHECK_BUDGET_S if budget_s is None else budget_s
    return _changed_below(
        os.fspath(path),
        cutoff,
        top.st_dev,
        time.monotonic() + budget,
        FRESH_CHECK_MAX_ENTRIES if max_entries is None else max_entries,
    )


def recently_changed(path: Path, now_s: float) -> bool:
    """``freshness`` as a keep-or-not answer: an unverified folder is kept."""
    return freshness(path, now_s) is not Freshness.UNCHANGED


def _changed_below(
    top: str, cutoff: float, device: int, deadline: float, max_entries: int
) -> Freshness:
    entries = 0
    stack = [top]
    while stack:
        if time.monotonic() >= deadline:
            return Freshness.UNVERIFIED
        try:
            with os.scandir(stack.pop()) as listing:
                for entry in listing:
                    entries += 1
                    if entries > max_entries:
                        return Freshness.UNVERIFIED
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if info.st_mtime >= cutoff:
                        return Freshness.CHANGED
                    if info.st_dev == device:
                        stack.append(entry.path)
        except OSError:
            return Freshness.CHANGED
    return Freshness.UNCHANGED


def _open_session_cwds(session_roots: Iterable[Path]) -> list[Path]:
    """The working directory of every session a live process holds."""
    roots = tuple(session_roots)
    cwds: list[Path] = []
    for session_id in live.open_sessions(roots):
        for root in roots:
            meta = root / UNIFIED_DIR / session_id / META_FILENAME
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            cwd = meta_cwd(raw)
            if cwd:
                try:
                    cwds.append(Path(cwd).resolve())
                except OSError:
                    continue
            break
    return cwds


__all__ = [
    "FRESH_CHECK_BUDGET_S",
    "FRESH_CHECK_MAX_ENTRIES",
    "RESERVATION_GRACE_S",
    "CleanProgress",
    "CleanRequest",
    "Freshness",
    "clean",
    "freshness",
    "recently_changed",
    "remove_worktree",
    "remove_worktree_safely",
]
