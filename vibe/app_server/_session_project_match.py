"""The project an unstamped session falls back to: the one whose folder it runs in."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from vibe.app_server._local_projects import LocalProject

__all__ = ["ProjectFolders"]


class ProjectFolders:
    """Only an exact folder matches: a session in a subfolder stays projectless."""

    def __init__(self, projects: Sequence[LocalProject]) -> None:
        candidates: dict[Path, list[LocalProject]] = {}
        for project in projects:
            for root in dict.fromkeys(_canonical(root) for root in project.root_paths):
                candidates.setdefault(root, []).append(project)
        self._by_folder = {
            folder: _preferred(folder, projects)
            for folder, projects in candidates.items()
        }

    def match(self, cwd: str | None) -> LocalProject | None:
        if cwd is None:
            return None
        return self._by_folder.get(_canonical(Path(cwd)))


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
