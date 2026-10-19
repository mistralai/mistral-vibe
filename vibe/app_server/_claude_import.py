"""App-server orchestration for importing Claude projects and sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
import logging
from pathlib import Path

from vibe.core.imports._harness_port import (
    ImportDiscoverySummary,
    ImportHarnessPort,
    ImportProject,
    ImportProjectProvisioner,
)
from vibe.core.imports._session import ImportedSessionWriter
from vibe.core.imports.adapters.claude_project import (
    ClaudeProjectSource,
    ProjectDirectoryStatus,
)
from vibe.core.imports.adapters.claude_session import (
    ClaudeSessionSource,
    ClaudeTranscriptError,
)

logger = logging.getLogger(__name__)


class ClaudeImportService(ImportHarnessPort):
    """Imports Claude source objects into Vibe project and session destinations."""

    def __init__(
        self,
        project_source: ClaudeProjectSource,
        session_source: ClaudeSessionSource,
        project_provisioner: ImportProjectProvisioner,
        session_writer_factory: Callable[[], Awaitable[ImportedSessionWriter]],
    ) -> None:
        self._project_source = project_source
        self._session_source = session_source
        self._project_provisioner = project_provisioner
        self._session_writer_factory = session_writer_factory

    def discover(self) -> ImportDiscoverySummary:
        projects = self._project_source.discover()
        return ImportDiscoverySummary(
            project_count=sum(
                project.directory_status is ProjectDirectoryStatus.DIRECTORY_EXISTS
                for project in projects
            ),
            session_count=len(self._session_source.discover()),
        )

    async def run(self) -> None:
        source_projects = await asyncio.to_thread(self._project_source.discover)
        projects: list[ImportProject] = []
        for project in source_projects:
            if project.directory_status is ProjectDirectoryStatus.DIRECTORY_EXISTS:
                projects.append(self._project_source.translate(project))
        sessions = await asyncio.to_thread(self._session_source.discover)
        logger.info(
            "claude_import_started discovered_project_count=%d importable_project_count=%d discovered_session_count=%d",
            len(source_projects),
            len(projects),
            len(sessions),
        )
        projects = await self._project_provisioner.ensure_projects(projects)

        writer = await self._session_writer_factory()
        processed_session_count = 0
        skipped_session_count = 0
        invalid_session_count = 0
        for session in sessions:
            try:
                transcript = await asyncio.to_thread(self._session_source.read, session)
                project = _project_containing(transcript.cwd, projects)
                if project is None:
                    skipped_session_count += 1
                    continue
                payload = self._session_source.translate(transcript)
                payload = replace(payload, cwd=project.cwd, source_cwd=transcript.cwd)
                await writer.write(payload)
            except (ClaudeTranscriptError, ValueError, OverflowError) as exc:
                invalid_session_count += 1
                logger.warning(
                    "claude_import_session_invalid source_session_id=%s source_path=%s error=%s",
                    session.id,
                    session.path,
                    exc,
                )
                continue
            processed_session_count += 1
        logger.info(
            "claude_import_completed imported_project_count=%d processed_session_count=%d skipped_session_count=%d invalid_session_count=%d",
            len(projects),
            processed_session_count,
            skipped_session_count,
            invalid_session_count,
        )


def _project_containing(
    session_cwd: Path, projects: list[ImportProject]
) -> ImportProject | None:
    """Returns the closest imported project directory containing a session."""
    candidates = [
        project
        for project in projects
        if session_cwd.is_relative_to(project.source_cwd or project.cwd)
    ]
    return max(
        candidates,
        key=lambda project: len((project.source_cwd or project.cwd).parts),
        default=None,
    )
