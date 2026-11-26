"""Single-flight, app-server-owned import job lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Protocol
from uuid import uuid4

from vibe.app_server._time import now_ms
from vibe.app_server.models import ImportJob, ImportJobStatus

logger = logging.getLogger(__name__)


class ImportJobRunner(Protocol):
    """The business importer plugged into the app-server job plumbing."""

    async def run(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ImportJobSnapshot:
    event_id: int
    job: ImportJob | None


type ImportJobUpdated = Callable[[ImportJobSnapshot], Awaitable[None]]


class ImportJobService:
    """Owns one process-wide import job and its monotonic status revisions."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._event_id = 0
        self._job: ImportJob | None = None
        self._task: asyncio.Task[None] | None = None
        self._on_updated: ImportJobUpdated | None = None

    async def reserve(
        self, runner: ImportJobRunner, on_updated: ImportJobUpdated | None = None
    ) -> ImportJob:
        async with self._condition:
            if self._job is not None and self._job.status in {
                ImportJobStatus.QUEUED,
                ImportJobStatus.RUNNING,
            }:
                raise ImportJobConflictError(self._job)
            timestamp = now_ms()
            self._job = ImportJob(
                id=str(uuid4()),
                status=ImportJobStatus.QUEUED,
                message=None,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._runner = runner
            self._on_updated = on_updated
            self._publish_locked()
            logger.info("import_job_queued job_id=%s", self._job.id)
            return self._job

    def start_reserved(self, job_id: str) -> None:
        """Start a reserved job after its accepting RPC response is sent."""
        logger.info("import_job_starting job_id=%s", job_id)
        self._task = asyncio.create_task(
            self._run(job_id), name=f"vibe-import-{job_id}"
        )

    def abandon_reserved(self, job_id: str) -> None:
        """Release a reservation when its accepting response could not be delivered."""
        asyncio.create_task(self._abandon(job_id), name=f"vibe-import-abandon-{job_id}")

    async def read(self, after_event_id: int, wait_ms: int) -> ImportJobSnapshot:
        async with self._condition:
            if after_event_id == self._event_id and wait_ms > 0:
                try:
                    await asyncio.wait_for(
                        self._condition.wait(), timeout=wait_ms / 1000
                    )
                except TimeoutError:
                    pass
            return ImportJobSnapshot(event_id=self._event_id, job=self._job)

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self, job_id: str) -> None:
        await self._report(
            await self._set_status(job_id, ImportJobStatus.RUNNING, None)
        )
        try:
            await self._runner.run()
        except asyncio.CancelledError:
            logger.info("import_job_cancelled job_id=%s", job_id)
            raise
        except Exception as exc:
            logger.error("import_job_failed job_id=%s", job_id, exc_info=exc)
            await self._report(
                await self._set_status(job_id, ImportJobStatus.FAILED, str(exc))
            )
        else:
            logger.info("import_job_completed job_id=%s", job_id)
            await self._report(
                await self._set_status(job_id, ImportJobStatus.COMPLETED, None)
            )

    async def _abandon(self, job_id: str) -> None:
        async with self._condition:
            if self._job is None or self._job.id != job_id:
                return
            if self._job.status is not ImportJobStatus.QUEUED:
                return
            logger.info("import_job_abandoned job_id=%s", job_id)
            self._job = None
            self._publish_locked()

    async def _set_status(
        self, job_id: str, status: ImportJobStatus, message: str | None
    ) -> ImportJobSnapshot | None:
        async with self._condition:
            job = self._job
            if job is None or job.id != job_id:
                return None
            self._job = job.model_copy(
                update={"status": status, "message": message, "updated_at": now_ms()}
            )
            self._publish_locked()
            return ImportJobSnapshot(event_id=self._event_id, job=self._job)

    async def _report(self, snapshot: ImportJobSnapshot | None) -> None:
        if snapshot is None or self._on_updated is None:
            return
        await self._on_updated(snapshot)

    def _publish_locked(self) -> None:
        self._event_id += 1
        self._condition.notify_all()


class ImportJobConflictError(Exception):
    def __init__(self, job: ImportJob) -> None:
        super().__init__(f"Import job already running: {job.id}")
        self.job = job
