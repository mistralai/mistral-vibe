"""Which session, or which category, each folder of ``~/.vibe`` belongs to.

- ``<root>/unified/<id>/`` with ``meta.json`` is a session row, forks and
  rewinds included; Active or Archived from ``archived_at``.
- ``<root>/unified/child-<hex>/`` (a subagent, no ``meta.json``) adds to its
  root's history, the root named by ``root_session_id`` in its newest
  ``runtime-state.json``; with that root gone, it is Other Vibe data.
- Any other folder of ``unified/`` is a row with no title, never suggested.
- ``worktrees/<name>-<12 hex>/<folder>`` is a worktree: its claim lists the
  sessions using it; no claim, or no live session, makes it a leftover. A
  claim no session ever joined, written less than ``RESERVATION_GRACE_S``
  ago, is a worktree being started: listed, never offered for cleanup. A
  folder there with neither a claim nor a ``.git`` is no worktree: Other.
- The rest (legacy ``session_*`` folders, ``active/``, plugins, scratchpad,
  ``.templates``, ``.trash``, logs, caches) is Other Vibe data.

Claims are read without the prune lock, so a scan never blocks a worktree
being created or removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import time

from vibe.app_server._storage_usage.rules import (
    BUCKET_PATTERN,
    META_FILENAME,
    UNIFIED_DIR,
    meta_cwd,
    unattached_and_young,
)
from vibe.app_server.protocol import StorageOtherKind
from vibe.core.git.worktree.record import (
    CLAIMS_DIR_NAME,
    TEMPLATES_DIR_NAME,
    TRASH_DIR_NAME,
    WorktreeClaim,
)

_UNIFIED = UNIFIED_DIR
_CHILD_PREFIX = "child-"
_BUCKET = BUCKET_PATTERN
_META = META_FILENAME
_GIT = ".git"
# Vibe's own metadata, as older versions laid it out inside a bucket: counted
# in Other Vibe data, never offered for cleanup.
_VIBE_FOLDERS = frozenset({CLAIMS_DIR_NAME, TRASH_DIR_NAME, TEMPLATES_DIR_NAME})


@dataclass(slots=True)
class SessionFolder:
    session_id: str
    path: Path
    title: str | None = None
    cwd: str | None = None
    archived: bool = False
    # None: no meta.json, so the newest file time stands in.
    last_activity_at: int | None = None
    has_meta: bool = True
    children: list[Path] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class WorktreeFolder:
    id: str
    bucket: str
    name: str
    path: Path
    managed: bool
    branch: str | None
    repo_name: str | None
    session_dirs: frozenset[Path]
    # No session joined its claim yet, and the claim is new.
    starting: bool = False


@dataclass(slots=True)
class Layout:
    sessions: dict[str, SessionFolder] = field(default_factory=dict)
    orphan_children: list[Path] = field(default_factory=list)
    worktrees: list[WorktreeFolder] = field(default_factory=list)
    # Folders of a worktree root that hold no worktree.
    worktree_other: list[tuple[StorageOtherKind, Path]] = field(default_factory=list)
    child_roots: dict[str, str] = field(default_factory=dict)
    # Folders of a worktree bucket with no claim and no ``.git``, by
    # ``<bucket>/<name>``: also in ``worktree_other``, and the cleanup offers them.
    unknown_folders: dict[str, Path] = field(default_factory=dict)


def read_sessions(
    roots: list[Path], known_child_roots: dict[str, str], layout: Layout
) -> None:
    """``known_child_roots`` saves re-reading a child's runtime state each scan."""
    pending_children: list[tuple[str, Path]] = []
    for root in roots:
        for entry in _subfolders(root / _UNIFIED):
            name = entry.name
            if name.startswith(_CHILD_PREFIX) and not (entry / _META).exists():
                pending_children.append((name, entry))
                continue
            layout.sessions.setdefault(name, _read_session(name, entry))
    for name, path in pending_children:
        root_id = known_child_roots.get(name) or _child_root(path)
        session = layout.sessions.get(root_id) if root_id is not None else None
        if session is None:
            layout.orphan_children.append(path)
            continue
        session.children.append(path)
        layout.child_roots[name] = session.session_id


def read_worktrees(worktrees_root: Path, layout: Layout) -> None:
    claims = {(claim.bucket, claim.name): claim for claim in WorktreeClaim.all()}
    for entry in _subfolders(worktrees_root):
        name = entry.name
        if name == CLAIMS_DIR_NAME:
            layout.worktree_other.append(("logs_and_caches", entry))
        elif name == TEMPLATES_DIR_NAME:
            layout.worktree_other.append(("worktree_template", entry))
        elif name == TRASH_DIR_NAME:
            layout.worktree_other.append(("worktree_trash", entry))
        elif not _BUCKET.match(name):
            layout.worktree_other.append(("worktrees_unknown", entry))
        else:
            for folder in _subfolders(entry):
                claim = claims.get((name, folder.name))
                if claim is None and not (folder / _GIT).exists():
                    # A build cache or scratch folder put there by hand: no
                    # worktree to list under Leftover worktrees.
                    layout.worktree_other.append(("worktrees_unknown", folder))
                    if folder.name not in _VIBE_FOLDERS:
                        layout.unknown_folders[f"{name}/{folder.name}"] = folder
                    continue
                layout.worktrees.append(_worktree(name, folder, claim))


def _worktree(bucket: str, path: Path, claim: WorktreeClaim | None) -> WorktreeFolder:
    record = claim.read() if claim is not None else None
    if record is None:
        return WorktreeFolder(
            id=f"{bucket}/{path.name}",
            bucket=bucket,
            name=path.name,
            path=path,
            managed=False,
            branch=None,
            repo_name=None,
            session_dirs=frozenset(),
        )
    return WorktreeFolder(
        id=f"{bucket}/{path.name}",
        bucket=bucket,
        name=path.name,
        path=path,
        managed=True,
        branch=record.branch,
        repo_name=record.repo_root.name,
        session_dirs=frozenset(item.resolve() for item in record.session_dirs),
        starting=claim is not None and unattached_and_young(claim, record, time.time()),
    )


def _read_session(session_id: str, path: Path) -> SessionFolder:
    try:
        raw = json.loads((path / _META).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SessionFolder(session_id, path, has_meta=False)
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, dict):
        return SessionFolder(session_id, path, has_meta=False)
    title = raw.get("title")
    return SessionFolder(
        session_id,
        path,
        title=title if isinstance(title, str) and title else None,
        cwd=meta_cwd(raw),
        archived=raw.get("archived_at") is not None,
        last_activity_at=_last_activity(raw),
    )


def _last_activity(raw: dict[str, object]) -> int | None:
    for key in ("bumped_at", "end_time", "start_time"):
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        try:
            return int(datetime.fromisoformat(value).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _child_root(path: Path) -> str | None:
    """``root_session_id`` from the child's newest generation that has one."""
    generations = sorted(_subfolders(path / "generations"), reverse=True)
    for generation in generations:
        try:
            raw = json.loads((generation / "runtime-state.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        identity = raw.get("identity") if isinstance(raw, dict) else None
        root = identity.get("root_session_id") if isinstance(identity, dict) else None
        if isinstance(root, str) and root:
            return root
    return None


def _subfolders(path: Path) -> list[Path]:
    try:
        with os.scandir(path) as entries:
            return [
                Path(entry.path)
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return []


__all__ = [
    "Layout",
    "SessionFolder",
    "WorktreeFolder",
    "read_sessions",
    "read_worktrees",
]
