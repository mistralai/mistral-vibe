"""The ``list_projects`` and ``move_to_project`` tools; the move lands through ``session/move`` once the turn ends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vibe.app_server._local_projects import (
    LocalProject,
    LocalProjectStore,
    LocalProjectStoreUnreadable,
    session_project,
)
from vibe.core.trusted_folders import TrustedFoldersManager
from vibe.core.types import SessionProject

LIST_PROJECTS_TOOL_NAME = "list_projects"
MOVE_TO_PROJECT_TOOL_NAME = "move_to_project"
PROJECT_TOOL_NAMES = (LIST_PROJECTS_TOOL_NAME, MOVE_TO_PROJECT_TOOL_NAME)

LIST_PROJECTS_TOOL_DESCRIPTION = """\
List the user's projects this session can move to with `move_to_project`: \
each one's id, name, folder, whether the user trusts that folder, and whether \
this session is already in it."""

MOVE_TO_PROJECT_TOOL_DESCRIPTION = """\
Move this session into one of the user's projects, by its id from \
`list_projects`, when the user's request is about that project's code. The \
move happens when your turn ends: end your turn right after this call and tell \
the user the session moves there. From the next turn on, commands and relative \
paths run in the project's folder, under its AGENTS.md and config; the current \
folder stays reachable by absolute path if it belongs to no project. Moving \
into a folder the user has not trusted asks for their approval. To cancel a \
planned move, call this again without a `project_id`."""


class ListProjectsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListedProject(BaseModel):
    id: str
    name: str
    folder: str
    trusted: bool
    current: bool


class ListProjectsResult(BaseModel):
    projects: list[ListedProject]


class MoveToProjectArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(
        default=None,
        min_length=1,
        description="The project's id, from `list_projects`; omit it to cancel a planned move.",
    )


class MoveToProjectResult(BaseModel):
    project_id: str | None = None
    name: str | None = None
    folder: str | None = None
    message: str


class ProjectMoveError(Exception):
    pass


type ProjectLister = Callable[[str], Awaitable[ListProjectsResult]]
type ProjectMover = Callable[[str, MoveToProjectArgs], Awaitable[MoveToProjectResult]]


def read_projects() -> list[LocalProject]:
    try:
        return LocalProjectStore().list_projects()
    except LocalProjectStoreUnreadable as exc:
        raise ProjectMoveError(str(exc)) from exc


def find_project(projects: Sequence[LocalProject], project_id: str) -> LocalProject:
    project = next((p for p in projects if p.id == project_id), None)
    if project is None:
        raise ProjectMoveError(
            f"No project has the id {project_id}; call list_projects for the ids"
        )
    return project


def current_project_id(
    projects: Sequence[LocalProject], stamp: SessionProject | None
) -> str | None:
    if stamp is None or stamp.kind == "none" or stamp.id is None:
        return None
    resolved = session_project(projects, stamp.id)
    return resolved.id if resolved.kind == "local" else None


def list_projects(
    projects: Sequence[LocalProject], *, current: str | None
) -> ListProjectsResult:
    trust = _persisted_trust()
    return ListProjectsResult(
        projects=[
            ListedProject(
                id=project.id,
                name=project.name,
                folder=str(project.root_paths[0]),
                trusted=_trusted(trust, project.root_paths[0]),
                current=project.id == current,
            )
            for project in projects
        ]
    )


def move_skips_approval(project_id: object) -> bool:
    """A cancel, or a move into a folder trusted for good, needs no approval."""
    if project_id is None:
        return True
    if not isinstance(project_id, str):
        return False
    try:
        project = find_project(read_projects(), project_id)
    except ProjectMoveError:
        return False
    return _trusted(_persisted_trust(), project.root_paths[0])


def cancelled() -> MoveToProjectResult:
    return MoveToProjectResult(
        message="The planned move is cancelled: this session stays where it is."
    )


def planned_move(project: LocalProject) -> MoveToProjectResult:
    folder = project.root_paths[0]
    return MoveToProjectResult(
        project_id=project.id,
        name=project.name,
        folder=str(folder),
        message=(
            f"This session moves to the project {project.name} when your turn "
            f"ends. End your turn now; from the next turn on, you work in {folder}."
        ),
    )


def _persisted_trust() -> TrustedFoldersManager | None:
    # The file as it is now: a session's own manager also holds temporary grants.
    try:
        return TrustedFoldersManager.read_file()
    except Exception:
        return None


def _trusted(trust: TrustedFoldersManager | None, folder: Path) -> bool:
    return trust is not None and trust.is_trusted(folder) is True


__all__ = [
    "LIST_PROJECTS_TOOL_DESCRIPTION",
    "LIST_PROJECTS_TOOL_NAME",
    "MOVE_TO_PROJECT_TOOL_DESCRIPTION",
    "MOVE_TO_PROJECT_TOOL_NAME",
    "PROJECT_TOOL_NAMES",
    "ListProjectsArgs",
    "ListProjectsResult",
    "ListedProject",
    "MoveToProjectArgs",
    "MoveToProjectResult",
    "ProjectLister",
    "ProjectMoveError",
    "ProjectMover",
    "cancelled",
    "current_project_id",
    "find_project",
    "list_projects",
    "move_skips_approval",
    "planned_move",
    "read_projects",
]
