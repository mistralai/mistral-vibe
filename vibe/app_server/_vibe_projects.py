"""Provisioning Vibe projects for imported source directories."""

from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
from typing import Protocol

from vibe.app_server._project_links import ProjectLinksController
from vibe.core.imports._harness_port import ImportProject

logger = logging.getLogger(__name__)


class _ProjectLinkProvisioner(Protocol):
    async def ensure_import_project(
        self, root_path: str, name: str
    ) -> dict[str, object]: ...


class VibeProjectService:
    """Ensures imported directories have a linked, visible Vibe project."""

    def __init__(self, project_links: _ProjectLinkProvisioner | None = None) -> None:
        self._project_links = project_links or ProjectLinksController()

    async def ensure_projects(
        self, projects: list[ImportProject]
    ) -> list[ImportProject]:
        provisioned: list[ImportProject] = []
        for project in projects:
            try:
                result = await self._project_links.ensure_import_project(
                    str(project.cwd), project.name
                )
                # ProjectLinksController._link_dict emits this canonical inspected path.
                link = result.get("link")
                if not isinstance(link, dict):
                    raise ValueError("Import project response has no link")
                directory_path = link.get("directoryPath")
                if not isinstance(directory_path, str) or not directory_path:
                    raise ValueError("Import project response has no directory path")
            except Exception as exc:
                logger.warning(
                    "import_project_provisioning_failed",
                    extra={
                        "project_name": project.name,
                        "project_path": str(project.cwd),
                    },
                    exc_info=exc,
                )
                continue
            provisioned.append(
                replace(
                    project,
                    cwd=Path(directory_path),
                    source_cwd=project.source_cwd or project.cwd,
                )
            )
        return provisioned
