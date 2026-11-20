"""Rules the storage estimates, the scan's offer and the removals share, so
a number or an offer never promises a removal that will not happen.

The estimates and the offer read the last scan; the removals apply the same
rule again to the disk at removal time.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
import os
import re

from vibe.core.git.worktree.record import RECORD_FILENAME, WorktreeClaim, WorktreeRecord

# Where a session store keeps its sessions, and each session's metadata.
UNIFIED_DIR = "unified"
META_FILENAME = "meta.json"
# A worktree bucket: ``<repo name>-<12 hex>``.
BUCKET_PATTERN = re.compile(r"^.+-[0-9a-f]{12}$")

# A new worktree's folder exists, empty and unclaimed, until its claim is
# written, and its claim names no session until the session starting in it
# joins: either, younger than this, may be about to be used.
RESERVATION_GRACE_S = 10 * 60


def frees_worktree(claimant_ids: Iterable[str], deleting: Collection[str]) -> bool:
    """Whether deleting the sessions in ``deleting`` leaves a worktree no
    session uses. ``claimant_ids`` are the sessions that claim it: those
    still on disk, plus those of ``deleting`` its claim names. True when
    there is at least one and every one is being deleted.
    """
    claimants = set(claimant_ids)
    return bool(claimants) and claimants.issubset(deleting)


def unattached_and_young(
    claim: WorktreeClaim,
    record: WorktreeRecord,
    now_s: float,
    grace_s: float = RESERVATION_GRACE_S,
) -> bool:
    """A claim no session ever joined, made or rewritten less than
    ``grace_s`` ago: the session it was made for may not have joined yet.
    The newer of ``claimed_at`` and the record's file time counts, so a
    checkout that took long, or a skewed clock, still gets the grace.
    """
    if record.session_dirs:
        return False
    newest = record.claimed_at.timestamp()
    try:
        newest = max(newest, os.stat(claim.directory / RECORD_FILENAME).st_mtime)
    except OSError:
        pass
    return now_s - newest < grace_s


def meta_cwd(raw: object) -> str | None:
    """The working directory a session's parsed ``meta.json`` records, or
    None when it records none a path could be.
    """
    environment = raw.get("environment") if isinstance(raw, dict) else None
    cwd = (
        environment.get("working_directory") if isinstance(environment, dict) else None
    )
    return cwd if isinstance(cwd, str) else None


__all__ = [
    "BUCKET_PATTERN",
    "META_FILENAME",
    "RESERVATION_GRACE_S",
    "UNIFIED_DIR",
    "frees_worktree",
    "meta_cwd",
    "unattached_and_young",
]
