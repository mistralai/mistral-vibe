"""The ``projects/*`` API over the local project store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from vibe.app_server._dispatch import RequestFailure, method_not_found
from vibe.app_server._local_projects import (
    LocalProject,
    LocalProjectError,
    LocalProjectNotFound,
    LocalProjectStore,
    LocalProjectStoreUnreadable,
)
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.protocol import (
    EmptyResponse,
    LocalProjectCloudView,
    LocalProjectView,
    ProjectMutationResponse,
    ProjectsCreateParams,
    ProjectsListParams,
    ProjectsListResponse,
    ProjectsRemoveParams,
    ProjectsRenameParams,
    ProtocolErrorCode,
)
from vibe.core.vibe_code_project import VibeProjectsStore
from vibe.observability.logging import logger
from vibe.observability.sentry import capture_sentry_exception

__all__ = ["LOCAL_PROJECT_METHODS", "LocalProjectsController"]

LOCAL_PROJECT_METHODS = frozenset({
    "projects/create",
    "projects/list",
    "projects/remove",
    "projects/rename",
})


class LocalProjectsController:
    def __init__(self, store: LocalProjectStore | None = None) -> None:
        self._store = store

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> ProtocolModel:
        # Resolved per call so the store follows the current Vibe home.
        store = self._store or LocalProjectStore()
        try:
            match method:
                case "projects/list":
                    validate_wire(ProjectsListParams, raw_params)
                    projects = await asyncio.to_thread(_list_with_links, store)
                    return ProjectsListResponse(projects=[_view(p) for p in projects])
                case "projects/create":
                    params = validate_wire(ProjectsCreateParams, raw_params)
                    root = _root_path(params.root_path)
                    project = await asyncio.to_thread(
                        lambda: store.create(
                            name=params.name if params.name is not None else root.name,
                            root_path=root,
                        )
                    )
                    return ProjectMutationResponse(project=_view(project))
                case "projects/rename":
                    params = validate_wire(ProjectsRenameParams, raw_params)
                    project = await asyncio.to_thread(
                        store.rename, params.id, params.name
                    )
                    return ProjectMutationResponse(project=_view(project))
                case "projects/remove":
                    params = validate_wire(ProjectsRemoveParams, raw_params)
                    await asyncio.to_thread(store.remove, params.id)
                    return EmptyResponse()
                case _:
                    raise method_not_found(method)
        except LocalProjectNotFound as exc:
            raise RequestFailure(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
        except LocalProjectError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        except LocalProjectStoreUnreadable as exc:
            raise RequestFailure(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc


def _list_with_links(store: LocalProjectStore) -> list[LocalProject]:
    # Runs on every list so links made with the local projects flag off show up too.
    # Best effort: a failed import must not hide the projects already stored.
    try:
        store.import_links(VibeProjectsStore().list_project_links())
    except LocalProjectStoreUnreadable:
        raise
    except Exception as exc:
        logger.warning("Could not import project links", exc_info=True)
        capture_sentry_exception(
            exc,
            fatal=False,
            tags={"vibe_boundary": "local_projects", "error_type": type(exc).__name__},
        )
    return store.list_projects()


def _root_path(value: str) -> Path:
    root = Path(value).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise LocalProjectError(f"Not a folder: {value!r}")
    resolved = root.resolve()
    if resolved.parent == resolved:
        raise LocalProjectError(f"A disk root can't be a project: {value!r}")
    return resolved


def _view(project: LocalProject) -> LocalProjectView:
    cloud = project.cloud
    return LocalProjectView(
        id=project.id,
        name=project.name,
        root_paths=[str(root) for root in project.root_paths],
        created_at=project.created_at,
        updated_at=project.updated_at,
        cloud=(
            LocalProjectCloudView(project_id=cloud.project_id, state=cloud.state)
            if cloud is not None
            else None
        ),
    )
