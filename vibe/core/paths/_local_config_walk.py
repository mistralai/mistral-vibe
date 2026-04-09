from __future__ import annotations

from collections import deque
from functools import cache
import logging
import os
from pathlib import Path

from vibe.core.autocompletion.file_indexer.ignore_rules import WALK_SKIP_DIR_NAMES

logger = logging.getLogger("vibe")

_VIBE_DIR = ".vibe"
_TOOLS_SUBDIR = Path(_VIBE_DIR) / "tools"
_VIBE_SKILLS_SUBDIR = Path(_VIBE_DIR) / "skills"
_AGENTS_SUBDIR = Path(_VIBE_DIR) / "agents"
_AGENTS_DIR = ".agents"
_AGENTS_SKILLS_SUBDIR = Path(_AGENTS_DIR) / "skills"

WALK_MAX_DEPTH = 4
_MAX_DIRS = 2000


def _collect_config_dirs_at(
    path: Path,
    entry_names: set[str],
    tools: list[Path],
    skills: list[Path],
    agents: list[Path],
) -> None:
    """Check a single directory for .vibe/ and .agents/ config subdirs."""
    if _VIBE_DIR in entry_names:
        if (candidate := path / _TOOLS_SUBDIR).is_dir():
            tools.append(candidate)
        if (candidate := path / _VIBE_SKILLS_SUBDIR).is_dir():
            skills.append(candidate)
        if (candidate := path / _AGENTS_SUBDIR).is_dir():
            agents.append(candidate)
    if _AGENTS_DIR in entry_names:
        if (candidate := path / _AGENTS_SKILLS_SUBDIR).is_dir():
            skills.append(candidate)


def _scandir_entries(path: Path) -> tuple[set[str], list[Path]]:
    """Scan a directory, returning entry names and sorted child directories to descend into.

    Uses ``os.scandir`` so that ``DirEntry.is_dir()`` leverages the dirent
    d_type field and avoids a separate ``stat`` syscall on most filesystems.
    """
    try:
        entries = list(os.scandir(path))
    except OSError:
        return set(), []

    entry_names = {e.name for e in entries}
    children: list[Path] = []
    for entry in entries:
        name = entry.name
        if name in WALK_SKIP_DIR_NAMES or name.startswith("."):
            continue
        try:
            if entry.is_dir():
                children.append(path / name)
        except OSError:
            continue
    children.sort()
    return entry_names, children


@cache
def walk_local_config_dirs_all(
    root: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
    """Discover .vibe/ and .agents/ config directories under *root*.

    Uses breadth-first search bounded by ``WALK_MAX_DEPTH`` and ``_MAX_DIRS``
    to avoid unbounded traversal in large repositories.
    """
    tools_dirs: list[Path] = []
    skills_dirs: list[Path] = []
    agents_dirs: list[Path] = []

    resolved_root = root.resolve()
    queue: deque[tuple[Path, int]] = deque([(resolved_root, 0)])
    visited = 0

    while queue and visited < _MAX_DIRS:
        current, depth = queue.popleft()
        visited += 1

        entry_names, children = _scandir_entries(current)
        if not entry_names:
            continue

        _collect_config_dirs_at(
            current, entry_names, tools_dirs, skills_dirs, agents_dirs
        )

        if depth < WALK_MAX_DEPTH:
            queue.extend((child, depth + 1) for child in children)

    if visited >= _MAX_DIRS:
        logger.warning(
            "Config directory scan reached directory limit (%d dirs) at %s",
            _MAX_DIRS,
            resolved_root,
        )

    return (tuple(tools_dirs), tuple(skills_dirs), tuple(agents_dirs))


def has_config_dirs_nearby(
    root: Path, *, max_depth: int = WALK_MAX_DEPTH, max_dirs: int = 200
) -> bool:
    """Quick check for .vibe/ or .agents/ config dirs in the near subtree.

    Returns ``True`` as soon as any config directory is found, without
    enumerating all of them.
    """
    resolved = root.resolve()
    queue: deque[tuple[Path, int]] = deque([(resolved, 0)])
    visited = 0
    found: list[Path] = []

    while queue and visited < max_dirs:
        current, depth = queue.popleft()
        visited += 1

        entry_names, children = _scandir_entries(current)
        if not entry_names:
            continue

        _collect_config_dirs_at(current, entry_names, found, found, found)
        if found:
            return True

        if depth < max_depth:
            queue.extend((child, depth + 1) for child in children)

    return False
