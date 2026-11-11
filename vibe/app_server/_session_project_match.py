"""The project an unstamped session falls back to: the one whose folder it runs in."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from vibe.app_server._local_projects import LocalProject
from vibe.core.paths import WORKTREES_DIR

__all__ = ["ProjectFolders"]


class ProjectFolders:
    """A folder matches itself and its subfolders; the deepest project folder wins."""

    def __init__(self, projects: Sequence[LocalProject]) -> None:
        candidates: dict[Path, list[LocalProject]] = {}
        for project in projects:
            for root in dict.fromkeys(_canonical(root) for root in project.root_paths):
                candidates.setdefault(root, []).append(project)
        self._by_folder = {
            folder: _preferred(folder, projects)
            for folder, projects in candidates.items()
        }
        self._worktrees = _canonical(WORKTREES_DIR.path)

    def match(self, cwd: str | None) -> LocalProject | None:
        if cwd is None:
            return None
        folder = _canonical(Path(cwd))
        # A managed worktree joins a project only through its stamp or its exact folder.
        if folder.is_relative_to(self._worktrees):
            return self._by_folder.get(folder)
        return next(
            (
                self._by_folder[candidate]
                for candidate in (folder, *folder.parents)
                if candidate in self._by_folder
            ),
            None,
        )


def _preferred(folder: Path, projects: Sequence[LocalProject]) -> LocalProject:
    # Ties go to: a single-folder project, then one whose primary folder it is,
    # then the oldest, then the lowest id.
    return min(
        projects,
        key=lambda project: (
            len(project.root_paths) != 1,
            _canonical(project.root_paths[0]) != folder,
            project.created_at,
            project.id,
        ),
    )


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve()
