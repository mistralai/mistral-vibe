"""App-server import use cases."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging

from vibe.app_server._claude_import import ClaudeImportService
from vibe.app_server._dispatch import DispatchResult, RequestFailure
from vibe.app_server._import_jobs import (
    ImportJobConflictError,
    ImportJobService,
    ImportJobSnapshot,
)
from vibe.app_server._model import ProtocolModel
from vibe.app_server._vibe_projects import VibeProjectService
from vibe.app_server.models import ImportHarness, ImportHarnessDiscovery
from vibe.app_server.protocol import (
    ImportDiscoveryResponse,
    ImportJobReadResponse,
    ImportJobStartResponse,
    ImportJobUpdatedParams,
    ProtocolErrorCode,
)
from vibe.core.imports._harness_port import ImportHarnessPort
from vibe.core.imports._session import ImportedSessionWriter
from vibe.core.imports.adapters.claude_home import ClaudeHome
from vibe.core.imports.adapters.claude_project import ClaudeProjectSource
from vibe.core.imports.adapters.claude_session import ClaudeSessionSource

logger = logging.getLogger(__name__)


class _ImportHarnessesRunner:
    """Runs the selected source harnesses in their requested order."""

    def __init__(self, harnesses: list[ImportHarnessPort]) -> None:
        self._harnesses = harnesses

    async def run(self) -> None:
        for harness in self._harnesses:
            await harness.run()


type Notify = Callable[[str, ProtocolModel], Awaitable[None]]
IMPORTS_UPDATED = "imports/updated"


def build_local_imports_service(
    session_writer_factory: Callable[[], Awaitable[ImportedSessionWriter]],
) -> ImportsService:
    """Builds imports from harnesses installed on this machine."""
    claude_home = ClaudeHome.for_current_user()
    return ImportsService({
        ImportHarness.CLAUDE: ClaudeImportService(
            ClaudeProjectSource(claude_home.configuration_path),
            ClaudeSessionSource(claude_home.projects_path),
            VibeProjectService(),
            session_writer_factory,
        )
    })


class ImportsService:
    """Coordinates discovery and import jobs across registered source harnesses."""

    def __init__(self, harnesses: Mapping[ImportHarness, ImportHarnessPort]) -> None:
        self._harnesses = harnesses
        self._jobs = ImportJobService()

    async def discover(self) -> DispatchResult:
        discoveries: list[ImportHarnessDiscovery] = []
        for harness, importer in self._harnesses.items():
            summary = await asyncio.to_thread(importer.discover)
            logger.info(
                "import_source_discovered harness=%s project_count=%d session_count=%d",
                harness.value,
                summary.project_count,
                summary.session_count,
            )
            discoveries.append(
                ImportHarnessDiscovery(
                    harness=harness,
                    project_count=summary.project_count,
                    session_count=summary.session_count,
                )
            )
        return DispatchResult(ImportDiscoveryResponse(harnesses=discoveries))

    async def start(
        self, harnesses: list[ImportHarness], *, notify: Notify
    ) -> DispatchResult:
        selected_harnesses: list[ImportHarnessPort] = []
        for harness in harnesses:
            try:
                selected_harnesses.append(self._harnesses[harness])
            except KeyError as exc:
                raise ValueError(f"Unsupported import harness: {harness}") from exc
        try:
            job = await self._jobs.reserve(
                _ImportHarnessesRunner(selected_harnesses),
                on_updated=lambda snapshot: self._notify_updated(snapshot, notify),
            )
        except ImportJobConflictError as exc:
            logger.info("import_job_conflict job_id=%s", exc.job.id)
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT, str(exc), {"jobId": exc.job.id}
            ) from exc
        logger.info(
            "import_job_reserved job_id=%s harnesses=%s",
            job.id,
            ",".join(harness.value for harness in harnesses),
        )
        return DispatchResult(
            ImportJobStartResponse(job=job),
            after_response=lambda: self._jobs.start_reserved(job.id),
            on_response_abandoned=lambda: self._jobs.abandon_reserved(job.id),
        )

    async def read(self, after_event_id: int, wait_ms: int) -> DispatchResult:
        snapshot = await self._jobs.read(after_event_id, wait_ms)
        return DispatchResult(
            ImportJobReadResponse(event_id=snapshot.event_id, job=snapshot.job)
        )

    async def close(self) -> None:
        await self._jobs.close()

    @staticmethod
    async def _notify_updated(snapshot: ImportJobSnapshot, notify: Notify) -> None:
        await notify(
            IMPORTS_UPDATED,
            ImportJobUpdatedParams(event_id=snapshot.event_id, job=snapshot.job),
        )
