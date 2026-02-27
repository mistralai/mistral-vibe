from __future__ import annotations

from functools import cache
import os
from pathlib import Path

from vibe.core.autocompletion.file_indexer.ignore_rules import WALK_SKIP_DIR_NAMES

_VIBE_DIR = ".vibe"
_TOOLS_SUBDIR = Path(_VIBE_DIR) / "tools"
_VIBE_SKILLS_SUBDIR = Path(_VIBE_DIR) / "skills"
_AGENTS_SUBDIR = Path(_VIBE_DIR) / "agents"
_AGENTS_DIR = ".agents"
_AGENTS_SKILLS_SUBDIR = Path(_AGENTS_DIR) / "skills"


@cache
def walk_local_config_dirs_all(
    root: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
    tools_dirs: list[Path] = []
    skills_dirs: list[Path] = []
    agents_dirs: list[Path] = []
    resolved_root = root.resolve()
    for dirpath, dirnames, _ in os.walk(resolved_root, topdown=True):
        dir_set = frozenset(dirnames)
        path = Path(dirpath)
        if _VIBE_DIR in dir_set:
            if (candidate := path / _TOOLS_SUBDIR).is_dir():
                tools_dirs.append(candidate)
            if (candidate := path / _VIBE_SKILLS_SUBDIR).is_dir():
                skills_dirs.append(candidate)
            if (candidate := path / _AGENTS_SUBDIR).is_dir():
                agents_dirs.append(candidate)
        if _AGENTS_DIR in dir_set:
            if (candidate := path / _AGENTS_SKILLS_SUBDIR).is_dir():
                skills_dirs.append(candidate)
        dirnames[:] = sorted(d for d in dirnames if d not in WALK_SKIP_DIR_NAMES)
    return (tuple(tools_dirs), tuple(skills_dirs), tuple(agents_dirs))
