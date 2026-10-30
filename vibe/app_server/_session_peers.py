"""The ``list_sessions``, ``read_session``, ``send_to_session`` and
``archive_sessions`` tools: the attached client reaches the other sessions it
hosts in the caller's project.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Final, Protocol
import uuid

from pydantic import BaseModel, ConfigDict, Field

from vibe.app_server.protocol import (
    PeerSessionStatus,
    SessionPeerArchiveParams,
    SessionPeerArchiveResponse,
    SessionPeerListParams,
    SessionPeerListResponse,
    SessionPeerReadParams,
    SessionPeerReadResponse,
    SessionPeerSendParams,
    SessionPeerSendResponse,
)

LIST_SESSIONS_TOOL_NAME = "list_sessions"
READ_SESSION_TOOL_NAME = "read_session"
SEND_TO_SESSION_TOOL_NAME = "send_to_session"
ARCHIVE_SESSIONS_TOOL_NAME = "archive_sessions"

LIST_SESSIONS_TOOL_DESCRIPTION = """\
To answer a question about many sessions, such as which have a pull request \
or failing checks, page through them from a `run_typescript` program as \
`tools.vibe.list_sessions` and return only what you need: do not read the \
pages here. Call it directly to look over the latest sessions or to check on \
given ones with `session_ids`, such as one `start_session` returned. Lists \
the other sessions in this project, most recently active first, a page at a \
time, archived ones left out unless `statuses` asks for them. Each has its \
status (`working`, `idle`, `waiting_for_user` when an approval or a question \
blocks it, `failed` or `archived`), its queued messages, where it works, \
and the pull requests it opened with their state, checks and review."""

READ_SESSION_TOOL_DESCRIPTION = """\
Read another session of this project: its status and its user and assistant \
messages, newest page first and oldest first within a page; pass \
`next_cursor` back to read further back. It returns at once and never waits: \
a session that is still `working` has not answered yet, so tell the user and \
read it again when they ask, rather than reading it over and over. Set \
`include_tool_calls` to also see one line per tool call it made. Only the \
user can answer a session that waits for them; tell them when one does."""

SEND_TO_SESSION_TOOL_DESCRIPTION = """\
Send a message to another session in this project, as the user would. It \
runs as that session's next turn: at once when the session is idle, after its \
current turn when it is working. The message reaches it marked as coming from \
this session. Write it as a self-contained instruction or question. It \
returns once the message is queued, not when it is answered: tell the user it \
was sent, and read the session for the answer when they ask. Never send a \
message to this session itself."""

ARCHIVE_SESSIONS_TOOL_DESCRIPTION = """\
Archive other sessions of this project, all in one call: find their ids with \
`list_sessions` first (from a program to filter many), then pass them all \
here. An archived session leaves the user's session list; the user can \
restore it. A session that is working, has queued messages or waits for the \
user is not archived: tell the user, who can archive it once it is done. \
Never archive this session itself."""

DEFAULT_LIST_LIMIT: Final = 20
MAX_LIST_LIMIT: Final = 50
MAX_MESSAGE_CHARS: Final = 4000
DEFAULT_MESSAGE_LIMIT: Final = 10
MAX_MESSAGE_LIMIT: Final = 50
MAX_ARCHIVE_SESSIONS: Final = 50
PEER_REQUEST_TIMEOUT_SECONDS: Final = 30.0
ARCHIVE_SECONDS_PER_SESSION: Final = 5.0

_IDEMPOTENCY_NAMESPACE: Final = uuid.UUID("9d6c0a3e-1f2b-4c7d-8e5a-6b4f3c2d1e0f")


class ListSessionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_LIST_LIMIT,
        description="Only these sessions. Left out, every other session.",
    )
    statuses: list[PeerSessionStatus] | None = Field(
        default=None, min_length=1, description="Only sessions with these statuses."
    )
    limit: int = Field(
        default=DEFAULT_LIST_LIMIT,
        ge=1,
        le=MAX_LIST_LIMIT,
        description="How many sessions per page.",
    )
    cursor: str | None = Field(
        default=None, description="The `next_cursor` of the previous page."
    )


class ReadSessionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, description="The session to read.")
    limit: int = Field(
        default=DEFAULT_MESSAGE_LIMIT,
        ge=1,
        le=MAX_MESSAGE_LIMIT,
        description="How many messages per page.",
    )
    cursor: str | None = Field(
        default=None, description="The `next_cursor` of the previous read."
    )
    include_tool_calls: bool = Field(
        default=False, description="Also return one line per tool call it made."
    )


class SendToSessionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, description="The session to message.")
    message: str = Field(
        min_length=1, max_length=MAX_MESSAGE_CHARS, description="The message to send."
    )


class ArchiveSessionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_ARCHIVE_SESSIONS,
        description="The sessions to archive.",
    )


class SessionPeerError(Exception):
    pass


class SessionPeers(Protocol):
    async def list_peer_sessions(
        self, params: SessionPeerListParams
    ) -> SessionPeerListResponse: ...

    async def read_peer_session(
        self, params: SessionPeerReadParams
    ) -> SessionPeerReadResponse: ...

    async def send_to_peer_session(
        self, params: SessionPeerSendParams
    ) -> SessionPeerSendResponse: ...

    async def archive_peer_sessions(
        self, params: SessionPeerArchiveParams
    ) -> SessionPeerArchiveResponse: ...


type AwaitsUser = Callable[[str], bool]


async def run_list_sessions(
    args: ListSessionsArgs, *, peers: SessionPeers
) -> SessionPeerListResponse:
    return await _within(
        peers.list_peer_sessions(
            SessionPeerListParams(
                session_ids=args.session_ids,
                statuses=args.statuses,
                limit=args.limit,
                cursor=args.cursor,
            )
        ),
        PEER_REQUEST_TIMEOUT_SECONDS,
    )


async def run_read_session(
    args: ReadSessionArgs, *, peers: SessionPeers
) -> SessionPeerReadResponse:
    return await _within(
        peers.read_peer_session(
            SessionPeerReadParams(
                target_session_id=args.session_id,
                limit=args.limit,
                cursor=args.cursor,
                include_tool_calls=args.include_tool_calls,
                max_message_chars=MAX_MESSAGE_CHARS,
            )
        ),
        PEER_REQUEST_TIMEOUT_SECONDS,
    )


async def run_send_to_session(
    args: SendToSessionArgs,
    *,
    session_id: str,
    invocation_id: str,
    peers: SessionPeers,
    awaits_user: AwaitsUser,
) -> SessionPeerSendResponse:
    """The idempotency key only absorbs a repeated delivery of this one call,
    as for ``start_session``.
    """
    _require_user_turn(session_id, awaits_user)
    params = SessionPeerSendParams(
        target_session_id=args.session_id,
        text=args.message,
        idempotency_key=str(
            uuid.uuid5(_IDEMPOTENCY_NAMESPACE, f"{session_id}/{invocation_id}")
        ),
    )
    return await _within(
        peers.send_to_peer_session(params),
        PEER_REQUEST_TIMEOUT_SECONDS,
        unknown_outcome=(
            "The client did not confirm the message in time, so it may or may "
            "not have been sent. Read the session before sending it again."
        ),
    )


async def run_archive_sessions(
    args: ArchiveSessionsArgs,
    *,
    session_id: str,
    peers: SessionPeers,
    awaits_user: AwaitsUser,
) -> SessionPeerArchiveResponse:
    _require_user_turn(session_id, awaits_user)
    session_ids = list(dict.fromkeys(args.session_ids))
    return await _within(
        peers.archive_peer_sessions(SessionPeerArchiveParams(session_ids=session_ids)),
        PEER_REQUEST_TIMEOUT_SECONDS + ARCHIVE_SECONDS_PER_SESSION * len(session_ids),
        unknown_outcome=(
            "The client did not confirm in time, so some sessions may have been "
            "archived. List them before archiving again."
        ),
    )


def _require_user_turn(session_id: str, awaits_user: AwaitsUser) -> None:
    if awaits_user(session_id):
        raise SessionPeerError(
            "Another session started or messaged this one, so it cannot act on "
            "sessions until the user writes to it. Do the task here."
        )


async def _within[T](
    request: Coroutine[Any, Any, T],
    seconds: float,
    *,
    unknown_outcome: str = "The client did not answer in time. Try again.",
) -> T:
    try:
        async with asyncio.timeout(seconds):
            return await request
    except TimeoutError as exc:
        raise SessionPeerError(unknown_outcome) from exc
