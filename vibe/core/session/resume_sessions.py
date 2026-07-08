from __future__ import annotations

from dataclasses import dataclass

from vibe.core.config import AnyVibeConfig
from vibe.core.session.session_id import shorten_session_id
from vibe.core.session.session_loader import SessionLoader


def short_session_id(session_id: str) -> str:
    return shorten_session_id(session_id)


@dataclass(frozen=True)
class ResumeSessionInfo:
    session_id: str
    cwd: str
    title: str | None
    end_time: str | None

    @property
    def option_id(self) -> str:
        return self.session_id


def list_local_resume_sessions(
    config: AnyVibeConfig, cwd: str | None
) -> list[ResumeSessionInfo]:
    return [
        ResumeSessionInfo(
            session_id=session["session_id"],
            cwd=session["cwd"],
            title=session.get("title"),
            end_time=session.get("end_time"),
        )
        for session in SessionLoader.list_sessions(config.session_logging, cwd=cwd)
    ]


def session_latest_messages(
    sessions: list[ResumeSessionInfo], config: AnyVibeConfig
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
