from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NamedTuple, Protocol

from vibe.core.config import VibeConfig
from vibe.core.logger import logger
from vibe.core.nuage.client import WorkflowsClient
from vibe.core.nuage.workflow import (
    WorkflowExecutionListResponse,
    WorkflowExecutionStatus,
)
from vibe.core.session.session_id import shorten_session_id
from vibe.core.session.session_loader import SessionLoader

ResumeSessionSource = Literal["local", "remote"]


def can_delete_resume_session_source(source: ResumeSessionSource) -> bool:
    return source == "local"


def short_session_id(session_id: str, source: ResumeSessionSource = "local") -> str:
    return shorten_session_id(session_id, from_end=source == "remote")


_ACTIVE_STATUSES = [
    WorkflowExecutionStatus.RUNNING,
    WorkflowExecutionStatus.CONTINUED_AS_NEW,
]


class RemoteWorkflowRunsClient(Protocol):
    async def get_workflow_runs(
        self,
        workflow_identifier: str | None = None,
        page_size: int = 50,
        next_page_token: str | None = None,
        status: Sequence[WorkflowExecutionStatus] | None = None,
        user_id: str = "current",
    ) -> WorkflowExecutionListResponse: ...


class RemoteResumeClient(RemoteWorkflowRunsClient, Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class ResumeSessionInfo:
    session_id: str
    source: ResumeSessionSource
    cwd: str
    title: str | None
    end_time: str | None
    status: str | None = None

    @property
    def option_id(self) -> str:
        return f"{self.source}:{self.session_id}"

    @property
    def can_delete(self) -> bool:
        return can_delete_resume_session_source(self.source)


def list_local_resume_sessions(
    config: VibeConfig, cwd: str | None
) -> list[ResumeSessionInfo]:
    return [
        ResumeSessionInfo(
            session_id=session["session_id"],
            source="local",
            cwd=session["cwd"],
            title=session.get("title"),
            end_time=session.get("end_time"),
        )
        for session in SessionLoader.list_sessions(config.session_logging, cwd=cwd)
    ]


async def list_remote_resume_sessions(
    client: RemoteWorkflowRunsClient, workflow_id: str
) -> list[ResumeSessionInfo]:
    response = await client.get_workflow_runs(
        workflow_identifier=workflow_id, page_size=50, status=_ACTIVE_STATUSES
    )

    seen: dict[str, ResumeSessionInfo] = {}
    latest_start: dict[str, datetime] = {}
    for execution in response.executions:
        session = ResumeSessionInfo(
            session_id=execution.execution_id,
            source="remote",
            cwd="",
            title="Vibe Code",
            end_time=(
                execution.end_time.isoformat()
                if execution.end_time
                else execution.start_time.isoformat()
            ),
            status=execution.status,
        )
        prev_start = latest_start.get(execution.execution_id)
        if prev_start is None or execution.start_time > prev_start:
            seen[execution.execution_id] = session
            latest_start[execution.execution_id] = execution.start_time

    sessions = list(seen.values())

    logger.debug("Remote resume listing filtered sessions: %d", len(sessions))
    return sessions


def session_latest_messages(
    sessions: list[ResumeSessionInfo], config: VibeConfig
) -> dict[str, str]:
    messages: dict[str, str] = {}
    for session in sessions:
        if session.source == "remote":
            status = (session.status or "RUNNING").lower()
            messages[session.option_id] = (
                f"{session.title or 'Remote workflow'} ({status})"
            )
            continue
        messages[session.option_id] = (
            session.title
            or SessionLoader.get_first_user_message(
                session.session_id, config.session_logging
            )
        )
    return messages


class RemoteResumeResult(NamedTuple):
    sessions: list[ResumeSessionInfo]
    error: str | None


def _default_remote_resume_client(config: VibeConfig) -> RemoteResumeClient:
    return WorkflowsClient(
        base_url=config.vibe_code_base_url,
        api_key=config.vibe_code_api_key,
        timeout=config.api_timeout,
    )


class RemoteResumeSessions:
    def __init__(
        self,
        get_config: Callable[[], VibeConfig],
        client_factory: Callable[
            [VibeConfig], RemoteResumeClient
        ] = _default_remote_resume_client,
    ) -> None:
        self._get_config = get_config
        self._client_factory = client_factory
        self._client: RemoteResumeClient | None = None
        self._client_settings: tuple[str, str, float] | None = None
        self._fetch_task: asyncio.Task[RemoteResumeResult] | None = None

    async def _reusable_client(self, config: VibeConfig) -> RemoteResumeClient:
        settings = (
            config.vibe_code_base_url,
            config.vibe_code_api_key,
            config.api_timeout,
        )
        if self._client is not None and self._client_settings == settings:
            return self._client
        if self._client is not None:
            await self._close_client()
        self._client = self._client_factory(config)
        self._client_settings = settings
        return self._client

    def start(self, timeout: float) -> asyncio.Task[RemoteResumeResult]:
        if self._fetch_task is not None and not self._fetch_task.done():
            self._fetch_task.cancel()
        self._fetch_task = asyncio.create_task(self.fetch(timeout))
        return self._fetch_task

    async def fetch(self, timeout: float) -> RemoteResumeResult:
        config = self._get_config()
        if not config.vibe_code_enabled or not config.vibe_code_api_key:
            logger.debug(
                "Remote resume listing skipped: missing Vibe Code configuration"
            )
            return RemoteResumeResult([], None)
        try:
            client = await self._reusable_client(config)
            sessions = await asyncio.wait_for(
                list_remote_resume_sessions(client, config.vibe_code_workflow_id),
                timeout=timeout,
            )
        except TimeoutError:
            return RemoteResumeResult(
                [], f"Timed out while listing remote sessions after {timeout:.0f}s."
            )
        except Exception as e:
            return RemoteResumeResult([], f"Failed to list remote sessions: {e}")
        return RemoteResumeResult(sessions, None)

    async def aclose(self) -> None:
        if self._fetch_task is not None and not self._fetch_task.done():
            self._fetch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._fetch_task
        self._fetch_task = None
        await self._close_client()

    async def _close_client(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.aclose()
        except Exception as exc:
            logger.error("Failed to close resume workflows client", exc_info=exc)
        finally:
            self._client = None
            self._client_settings = None
