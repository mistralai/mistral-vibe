from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vibe.core.config import VibeConfigSchema
from vibe.core.session.session_loader import SessionLoader
from vibe.observability.logging import logger
from vibe.utils.session_id import shorten_session_id


def short_session_id(session_id: str) -> str:
    return shorten_session_id(session_id)


@dataclass(frozen=True)
class ResumeSessionInfo:
    session_id: str
    cwd: str
    title: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    updated_at: str = ""
    parent_session_id: str | None = None

    @property
    def option_id(self) -> str:
        return self.session_id


def list_local_resume_sessions(
    config: VibeConfigSchema, cwd: str | None
) -> list[ResumeSessionInfo]:
    return [
        ResumeSessionInfo(
            session_id=session["session_id"],
            cwd=session["cwd"],
            parent_session_id=session["parent_session_id"],
            title=session.get("title"),
            start_time=session.get("start_time"),
            end_time=session.get("end_time"),
            updated_at=session["updated_at"],
        )
        for session in SessionLoader.list_sessions(config.session_logging, cwd=cwd)
    ]


def session_latest_messages(
    sessions: list[ResumeSessionInfo], config: VibeConfigSchema
) -> dict[str, str]:
    messages: dict[str, str] = {}
    for session in sessions:
        messages[session.option_id] = (
            session.title
            or SessionLoader.get_first_user_message(
                session.session_id, config.session_logging
            )
        )
    return messages


def resume_directories(config: VibeConfigSchema) -> tuple[Path, ...]:
    """Every directory a saved session in this store would resume into.

    The sweep's half of the answer, kept beside the listing it reads rather
    than in the lifecycle: the harness keeps its sessions elsewhere and reads
    them its own way, and a sweep that knew both layouts would carry a private
    detail of each.

    Read with no cwd filter. A worktree can hold a session started from
    anywhere, and one omitted here is one the sweep is free to delete.

    Raising is how "I could not see my sessions" is said. It abandons the sweep
    for that run and keeps every worktree, which is the safe way to be wrong.
    """
    try:
        sessions = list_local_resume_sessions(config, None)
    except OSError:
        logger.debug("Could not list resumable sessions before the sweep")
        raise
    return tuple(Path(session.cwd) for session in sessions if session.cwd)
