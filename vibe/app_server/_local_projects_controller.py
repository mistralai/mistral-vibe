"""The ``projects/*`` API over the local project store."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any

from vibe.app_server._dispatch import RequestFailure, method_not_found
from vibe.app_server._local_projects import (
    CloudBacking,
    CloudOwner,
    LocalProject,
    LocalProjectConflict,
    LocalProjectError,
    LocalProjectNotFound,
    LocalProjectStore,
    LocalProjectStoreUnreadable,
)
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._project_links import folder_has_commits
from vibe.app_server.protocol import (
    EmptyResponse,
    LocalProjectCloudOwner,
    LocalProjectCloudView,
    LocalProjectView,
    ProjectMutationResponse,
    ProjectsCreateParams,
    ProjectsLinkParams,
    ProjectsListParams,
    ProjectsListResponse,
    ProjectsRemoveParams,
    ProjectsRenameParams,
    ProjectsRetireCloudParams,
    ProjectsSetCloudParams,
    ProtocolErrorCode,
)
from vibe.core.vibe_code_project import VibeProjectsStore
from vibe.observability.logging import logger
from vibe.observability.sentry import capture_sentry_exception

__all__ = ["LOCAL_PROJECT_METHODS", "LocalProjectsController", "list_local_projects"]

LOCAL_PROJECT_METHODS = frozenset({
    "projects/create",
    "projects/link",
    "projects/list",
    "projects/remove",
    "projects/rename",
    "projects/retireCloud",
    "projects/setCloud",
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
                    projects = await asyncio.to_thread(list_local_projects, store)
                    views = await asyncio.gather(
                        *(asyncio.to_thread(_view, project) for project in projects)
                    )
                    return ProjectsListResponse(projects=list(views))
                case "projects/create":
                    params = validate_wire(ProjectsCreateParams, raw_params)
                    root = _root_path(params.root_path)
                    project = await asyncio.to_thread(
                        lambda: store.create(
                            name=params.name if params.name is not None else root.name,
                            root_path=root,
                        )
                    )
                case "projects/link":
                    params = validate_wire(ProjectsLinkParams, raw_params)
                    root = _root_path(params.root_path)
                    project = await asyncio.to_thread(
                        link_local_project,
                        store,
                        root,
                        params.cloud_project_id,
                        params.name,
                        _cloud_owner(params.owner),
                    )
                case "projects/rename":
                    params = validate_wire(ProjectsRenameParams, raw_params)
                    project = await asyncio.to_thread(
                        store.rename, params.id, params.name
                    )
                case "projects/remove":
                    params = validate_wire(ProjectsRemoveParams, raw_params)
                    await asyncio.to_thread(_remove_local_project, store, params.id)
                    return EmptyResponse()
                case "projects/retireCloud":
                    params = validate_wire(ProjectsRetireCloudParams, raw_params)
                    await asyncio.to_thread(store.retire_cloud, params.cloud_project_id)
                    return EmptyResponse()
                case "projects/setCloud":
                    params = validate_wire(ProjectsSetCloudParams, raw_params)
                    project = await asyncio.to_thread(
                        store.set_cloud,
                        params.id,
                        CloudBacking(
                            project_id=params.cloud_project_id,
                            state="backed",
                            owner=_cloud_owner(params.owner),
                        ),
                    )
                case _:
                    raise method_not_found(method)
            return ProjectMutationResponse(
                project=await asyncio.to_thread(_view, project)
            )
        except LocalProjectNotFound as exc:
            raise RequestFailure(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
        except LocalProjectConflict as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except LocalProjectError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        except LocalProjectStoreUnreadable as exc:
            raise RequestFailure(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc


def list_local_projects(store: LocalProjectStore) -> list[LocalProject]:
    _import_project_links(store)
    return store.list_projects()


def link_local_project(
    store: LocalProjectStore,
    root: Path,
    cloud_project_id: str,
    name: str,
    owner: CloudOwner | None,
) -> LocalProject:
    # Imported first: a folder linked with the flag off but not listed since must still refuse another project.
    _import_project_links(store)
    return store.link_cloud(
        root_path=root, cloud_project_id=cloud_project_id, name=name, owner=owner
    )


def _cloud_owner(owner: LocalProjectCloudOwner | None) -> CloudOwner | None:
    return CloudOwner(**owner.model_dump(by_alias=False)) if owner is not None else None


def _remove_local_project(store: LocalProjectStore, project_id: str) -> None:
    # Imported first: a folder linked to its cloud project since the last listing joins it, so its link goes too.
    _import_project_links(store)
    try:
        store.remove(project_id, VibeProjectsStore())
    except LocalProjectNotFound:
        # The import merged it into the project its folder is now linked to: it is gone already.
        if not any(
            project_id in project.merged_ids for project in store.list_projects()
        ):
            raise


def _import_project_links(store: LocalProjectStore) -> None:
    # Runs on every list and link so links made with the local projects flag off count too.
    # Best effort: a failed purge or import must not hide the projects already stored.
    links_store = VibeProjectsStore()
    try:
        # Purged first, so the import below no longer reads the links of a project an older server hid.
        store.purge_removed(links_store)
    except LocalProjectStoreUnreadable:
        raise
    except Exception as exc:
        _report_failure(exc, "Could not purge removed local projects")
    try:
        store.import_links(links_store)
    except LocalProjectStoreUnreadable:
        raise
    except Exception as exc:
        _report_failure(exc, "Could not import project links")


def _report_failure(exc: Exception, message: str) -> None:
    logger.warning(message, exc_info=True)
    capture_sentry_exception(
        exc,
        fatal=False,
        tags={"vibe_boundary": "local_projects", "error_type": type(exc).__name__},
    )


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
        has_commits=folder_has_commits(project.root_paths[0]),
        created_at=project.created_at,
        updated_at=project.updated_at,
        cloud=(
            LocalProjectCloudView(
                project_id=cloud.project_id,
                state=cloud.state,
                owner=(
                    LocalProjectCloudOwner(**asdict(cloud.owner))
                    if cloud.owner is not None
                    else None
                ),
            )
            if cloud is not None
            else None
        ),
    )
