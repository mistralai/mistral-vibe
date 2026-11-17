"""Waiting for the worktrees an archive freed to be gone before measuring.

Whichever process held the session reaps its worktree, in the background, a
few seconds after the archive. A scan asked for right after it waits, at most
a minute, for the folders of every worktree whose sessions are now all
archived or deleted, so its numbers already show the space freed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
import time

from vibe.core.git.worktree.record import WorktreeClaim
from vibe.core.session.session_loader import SessionLoader

_POLL_S = 0.25


def finished_worktrees(session_ids: Iterable[str], worktrees_root: Path) -> list[Path]:
    """Folders under ``worktrees_root`` of claimed worktrees used by one of
    ``session_ids`` whose every session is archived or deleted. Claims are
    read without the prune lock, as the scan reads them.
    """
    wanted = set(session_ids)
    folders: list[Path] = []
    for claim in WorktreeClaim.all():
        record = claim.read()
        if record is None or not record.session_dirs:
            continue
        if not any(folder.name in wanted for folder in record.session_dirs):
            continue
        if all(_finished(folder) for folder in record.session_dirs):
            folders.append(worktrees_root / claim.bucket / claim.name)
    return folders


def wait_until_gone(
    folders: list[Path],
    timeout_s: float,
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Whether every folder was gone in time."""
    deadline = clock() + timeout_s
    while True:
        if not any(folder.exists() for folder in folders):
            return True
        if cancelled() or clock() >= deadline:
            return False
        time.sleep(_POLL_S)


def _finished(session_dir: Path) -> bool:
    if not session_dir.exists():
        return True
    try:
        return SessionLoader.load_metadata(session_dir).archived_at is not None
    except ValueError:
        return False


__all__ = ["finished_worktrees", "wait_until_gone"]
