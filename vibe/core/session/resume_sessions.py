from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from vibe.core.config import VibeConfig
from vibe.core.logger import logger
from vibe.core.nuage.client import WorkflowsClient
from vibe.core.nuage.workflow import WorkflowExecutionStatus
from vibe.core.session.session_loader import SessionLoader

ResumeSessionSource = Literal["local", "remote"]

SHORT_SESSION_ID_LEN = 8


def short_session_id(session_id: str, source: ResumeSessionSource = "local") -> str:
    if source == "remote":
        return session_id[-SHORT_SESSION_ID_LEN:]
    return session_id[:SHORT_SESSION_ID_LEN]


_ACTIVE_STATUSES = [
    WorkflowExecutionStatus.RUNNING,
    WorkflowExecutionStatus.RETRYING_AFTER_ERROR,
    WorkflowExecutionStatus.CONTINUED_AS_NEW,
]


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


async def list_remote_resume_sessions(config: VibeConfig) -> list[ResumeSessionInfo]:
    if not config.vibe_code_enabled or not config.vibe_code_api_key:
        logger.debug("Remote resume listing skipped: missing Vibe Code configuration")
        return []

    async with WorkflowsClient(
        base_url=config.vibe_code_base_url,
        api_key=config.vibe_code_api_key,
        timeout=config.api_timeout,
    ) as client:
        response = await client.get_workflow_runs(
            workflow_identifier=config.vibe_code_workflow_id,
            page_size=50,
            status=_ACTIVE_STATUSES,
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
