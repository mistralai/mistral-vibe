"""What ``storage/sessions/delete`` does once the sessions are deleted.

Each session goes through the plain ``session/delete`` first (the server's
own path, in ``server.py``). Then every worktree a deleted session claimed is
removed when ``rules.frees_worktree`` says no session uses it any more, the
rule the delete estimates use, applied to the claims on disk now: the
claimants are the sessions its claim names whose folder is still on disk,
plus the deleted ones it names. Removal goes through the same release as the
leftover cleanup (``cleanup.remove_worktree``), which checks everything again.
So every client removes the same worktrees, and the answer says what went and
what stayed, one worktree failing never stopping the others.

This is the server's rule for worktrees on delete. Desktop's runner has a
second one for its single-session delete (``#removeSessionWorktree`` in
``agent-local-runner``'s ``backend.ts``): it sends
``workspace/git/worktrees/remove`` for the session's cwd unless a saved
session's cwd is still inside that worktree, judging by working directory
rather than by claim. Both are kept for now; making the runner use this one
is a follow-up of the PR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vibe.app_server._storage_usage import cleanup
from vibe.app_server._storage_usage.files import touch_stale_marker
from vibe.app_server._storage_usage.rules import frees_worktree
from vibe.app_server.protocol import StorageLeftoverKept, StorageUsageSnapshot
from vibe.core.git.worktree import ManagedWorktree
from vibe.core.git.worktree.record import WorktreeClaim, WorktreeRecord
from vibe.core.trusted_folders import TrustedFoldersManager
from vibe.observability.logging import logger


@dataclass(slots=True)
class Freed:
    removed_worktree_ids: list[str] = field(default_factory=list)
    kept_worktrees: list[StorageLeftoverKept] = field(default_factory=list)
    worktree_bytes: int = 0
    # A removed worktree was not measured yet: ``worktree_bytes`` is a floor.
    at_least: bool = False


def remove_orphaned_worktrees(
    deleted: list[str],
    worktrees_root: Path,
    snapshot: StorageUsageSnapshot | None,
    trust_store: TrustedFoldersManager | None,
) -> Freed:
    """Removes each worktree a deleted session claimed that no other session
    claims any more; a worktree another session still claims stays. Never
    raises for one worktree: it is kept with ``error``.
    """
    freed = Freed()
    wanted = set(deleted)
    if not wanted:
        return freed
    rows = {} if snapshot is None else {row.id: row for row in snapshot.worktrees}
    root = worktrees_root.resolve()
    for claim in WorktreeClaim.all():
        item = f"{claim.bucket}/{claim.name}"
        try:
            record = claim.read()
            if record is None or not frees_worktree(_claimants(record, wanted), wanted):
                continue
        # Broad: one claim failing to read must not keep the others.
        except Exception:
            logger.warning("Failed to read worktree claim %s", item, exc_info=True)
            # Kept, not left out, when the last measurement ties it to a
            # deleted session: the answer must not claim it went.
            row = rows.get(item)
            if row is not None and wanted & set(row.session_ids):
                freed.kept_worktrees.append(
                    StorageLeftoverKept(id=item, reason="error")
                )
            continue
        reason = cleanup.remove_worktree_safely(root, item, trust_store)
        if reason is None:
            freed.removed_worktree_ids.append(item)
            measured = rows[item].bytes if item in rows else None
            if measured is None:
                freed.at_least = True
            else:
                freed.worktree_bytes += measured
        elif reason != "not_found":
            freed.kept_worktrees.append(StorageLeftoverKept(id=item, reason=reason))
    if freed.removed_worktree_ids:
        touch_stale_marker()
        # Broad: the worktrees are already gone; the answer must say so.
        try:
            ManagedWorktree.empty_trash()
        except Exception:
            logger.warning("Failed to empty the worktree trash", exc_info=True)
    return freed


def _claimants(record: WorktreeRecord, deleting: set[str]) -> set[str]:
    """The sessions the claim names whose folder is on disk, plus the
    deleted ones it names, by folder name.
    """
    return {
        folder.name
        for folder in record.session_dirs
        if folder.name in deleting or folder.exists()
    }


__all__ = ["Freed", "remove_orphaned_worktrees"]
