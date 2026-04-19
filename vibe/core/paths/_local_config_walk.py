from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class ConfigWalkResult:
    """Aggregated results of a config directory walk."""

    config_dirs: tuple[Path, ...] = ()
    tools: tuple[Path, ...] = ()
    skills: tuple[Path, ...] = ()
    agents: tuple[Path, ...] = ()


@dataclass
class _ConfigWalkCollector:
    """Mutable accumulator used during BFS, frozen into ConfigWalkResult at the end."""

    config_dirs: list[Path] = field(default_factory=list)
    tools: list[Path] = field(default_factory=list)
    skills: list[Path] = field(default_factory=list)
    agents: list[Path] = field(default_factory=list)

    def freeze(self) -> ConfigWalkResult:
        return ConfigWalkResult(
            config_dirs=tuple(self.config_dirs),
            tools=tuple(self.tools),
            skills=tuple(self.skills),
            agents=tuple(self.agents),
        )


def _collect_at(
    path: Path, entry_names: set[str], collector: _ConfigWalkCollector
) -> None:
    """Check a single directory for .vibe/ and .agents/ config subdirs."""
    if _VIBE_DIR in entry_names and (vibe_dir := path / _VIBE_DIR).is_dir():
        has_content = False
        if (candidate := path / _TOOLS_SUBDIR).is_dir():
            collector.tools.append(candidate)
            has_content = True
        if (candidate := path / _VIBE_SKILLS_SUBDIR).is_dir():
            collector.skills.append(candidate)
            has_content = True
        if (candidate := path / _AGENTS_SUBDIR).is_dir():
            collector.agents.append(candidate)
            has_content = True
        if (
            has_content
            or (vibe_dir / "prompts").is_dir()
            or (vibe_dir / "config.toml").is_file()
        ):
            collector.config_dirs.append(vibe_dir)
    if _AGENTS_DIR in entry_names and (agents_dir := path / _AGENTS_DIR).is_dir():
        if (candidate := path / _AGENTS_SKILLS_SUBDIR).is_dir():
            collector.skills.append(candidate)
            collector.config_dirs.append(agents_dir)


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
def walk_local_config_dirs(
    root: Path, *, max_depth: int = WALK_MAX_DEPTH, max_dirs: int = _MAX_DIRS
) -> ConfigWalkResult:
    """Discover .vibe/ and .agents/ config directories under *root*.

    Uses breadth-first search bounded by *max_depth* and *max_dirs*
    to avoid unbounded traversal in large repositories.

    Returns a ``ConfigWalkResult`` containing both the parent config dirs
    (for trust decisions) and the categorised subdirs (for loading).
    """
    collector = _ConfigWalkCollector()
    resolved_root = root.resolve()
    queue: deque[tuple[Path, int]] = deque([(resolved_root, 0)])
    visited = 0

    while queue and visited < max_dirs:
        current, depth = queue.popleft()
        visited += 1

        entry_names, children = _scandir_entries(current)
        if not entry_names:
            continue

        _collect_at(current, entry_names, collector)

        if depth < max_depth:
            queue.extend((child, depth + 1) for child in children)

    if visited >= max_dirs:
        logger.warning(
            "Config directory scan reached directory limit (%d dirs) at %s",
            max_dirs,
            resolved_root,
        )

    return collector.freeze()
