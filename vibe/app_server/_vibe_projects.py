"""Resolving the Vibe project of each imported source directory."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
import logging
from pathlib import Path
from typing import Any, Protocol

from vibe.app_server._local_projects import LocalProjectStore
from vibe.app_server._local_projects_controller import list_local_projects
from vibe.app_server._project_links import ProjectLinksController
from vibe.core.imports._harness_port import ImportProject

logger = logging.getLogger(__name__)


class _ProjectLinkInspector(Protocol):
    async def inspect_root(self, root_path: str) -> dict[str, Any]: ...


type LocalProjectStoreFactory = Callable[[], Awaitable[LocalProjectStore | None]]


async def _no_local_projects() -> LocalProjectStore | None:
    return None


class VibeProjectService:
    """Reuses a directory's saved project link, or its local project when enabled.

    Never creates a cloud project: an unlinked directory stays unlinked and its
    sessions fall back to folder matching.
    """

    def __init__(
        self,
        project_links: _ProjectLinkInspector | None = None,
        local_projects: LocalProjectStoreFactory = _no_local_projects,
    ) -> None:
        self._project_links = project_links or ProjectLinksController()
        self._local_projects = local_projects

    async def ensure_projects(
        self, projects: list[ImportProject]
    ) -> list[ImportProject]:
        store = await self._local_projects()
        resolved: list[ImportProject] = []
        for project in projects:
            try:
                resolved.append(await self._resolve(project, store))
            except Exception as exc:
                logger.warning(
                    "import_project_resolution_failed",
                    extra={
                        "project_name": project.name,
                        "project_path": str(project.cwd),
                    },
                    exc_info=exc,
                )
                resolved.append(project)
        return resolved

    async def _resolve(
        self, project: ImportProject, store: LocalProjectStore | None
    ) -> ImportProject:
        inspection = await self._project_links.inspect_root(str(project.cwd))
        root = inspection.get("root")
        directory_path = root.get("directoryPath") if isinstance(root, dict) else None
        if not isinstance(directory_path, str) or not directory_path:
            return project
        saved_link = inspection.get("savedLink")
        project_id = (
            saved_link.get("projectId") if isinstance(saved_link, dict) else None
        )
        if not isinstance(project_id, str) and store is not None:
            project_id = await asyncio.to_thread(
                _local_project_id, store, Path(directory_path), project.name
            )
        if not isinstance(project_id, str):
            return project
        return replace(
            project,
            cwd=Path(directory_path),
            source_cwd=project.source_cwd or project.cwd,
            project_id=project_id,
        )


def _local_project_id(store: LocalProjectStore, root: Path, name: str) -> str:
    for local_project in list_local_projects(store):
        if root in local_project.root_paths:
            return local_project.id
    return store.create(name=name, root_path=root).id
