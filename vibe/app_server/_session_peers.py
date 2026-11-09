"""The ``list_sessions``, ``read_session``, ``send_to_session``,
``archive_sessions``, ``restore_sessions`` and ``mark_sessions_seen`` tools:
the attached client reaches the other sessions it hosts in the caller's
project.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
import difflib
from typing import Any, Final, Protocol
import uuid

from pydantic import BaseModel, ConfigDict, Field

from vibe.app_server._session_spawn import MAX_SESSION_HOPS, MAX_TURN_STARTED_SESSIONS
from vibe.app_server.protocol import (
    SESSION_LIMIT_HOPS,
    SESSION_LIMIT_STARTED_SESSIONS,
    ClientCapabilities,
    PeerDelivery,
    PeerSessionNotArchived,
    PeerSessionNotMarkedSeen,
    PeerSessionNotRestored,
    PeerSessionStatus,
    PeerSessionSummary,
    SessionLimitRefusal,
    SessionMessageSender,
    SessionPeerArchiveParams,
    SessionPeerArchiveResponse,
    SessionPeerListParams,
    SessionPeerListResponse,
    SessionPeerMarkSeenParams,
    SessionPeerMarkSeenResponse,
    SessionPeerReadParams,
    SessionPeerReadResponse,
    SessionPeerRestoreParams,
    SessionPeerRestoreResponse,
    SessionPeerSendParams,
    SessionPeerSendResponse,
)

LIST_SESSIONS_TOOL_NAME = "list_sessions"
READ_SESSION_TOOL_NAME = "read_session"
SEND_TO_SESSION_TOOL_NAME = "send_to_session"
ARCHIVE_SESSIONS_TOOL_NAME = "archive_sessions"
RESTORE_SESSIONS_TOOL_NAME = "restore_sessions"
MARK_SESSIONS_SEEN_TOOL_NAME = "mark_sessions_seen"

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
the pull requests it opened with their state, checks and review, and \
`is_unseen` when the user has not seen its latest activity."""

SESSION_REFERENCE = """\
Name a session by its id, the first 8 characters of its id, or its title \
(case ignored), as `list_sessions` shows them."""

READ_SESSION_TOOL_DESCRIPTION = f"""\
Read another session of this project: its status and its user and assistant \
messages, newest page first and oldest first within a page; pass \
`next_cursor` back to read further back. It returns at once and never waits: \
a session that is still `working` has not answered yet, so tell the user and \
read it again when they ask, rather than reading it over and over. Set \
`include_tool_calls` to also see one line per tool call it made. Only the \
user can answer a session that waits for them; tell them when one does. \
`is_unseen` means the user has not seen its latest activity. \
{SESSION_REFERENCE}"""

SEND_TO_SESSION_TOOL_DESCRIPTION = f"""\
Send a message to another session in this project. It reaches that session's \
current turn when it is working. When it is idle, `delivery` decides: \
'start_turn' starts a turn on it, for a task or a question it must act on or \
answer; 'queue' leaves the message for its next turn without starting one, \
for an update it does not need to act on. The other session knows the message \
comes from this one, so write only the message itself, self-contained, without \
saying who sends it. It returns once the message is delivered, not when it is \
answered: tell the user it was sent. Leave `session_id` out to message the \
session that started this one. A session this one started reports back \
with its own message, which arrives as a new turn here, so do not read it \
over and over to wait for it. Never send a message to this session itself. \
After {MAX_SESSION_HOPS} messages and starts between sessions in a row \
without the user writing, sending is refused until the user writes: tell the \
user instead. One turn sends at most {MAX_TURN_STARTED_SESSIONS} 'start_turn' \
messages and new sessions together. {SESSION_REFERENCE}"""

ARCHIVE_SESSIONS_TOOL_DESCRIPTION = f"""\
Archive other sessions of this project, all in one call: find their ids with \
`list_sessions` first (from a program to filter many), then pass them all \
here. An archived session leaves the user's session list; `restore_sessions` \
brings it back. A session that is working, has queued messages or waits for the \
user is not archived: tell the user, who can archive it once it is done. \
Never archive this session itself. {SESSION_REFERENCE}"""

RESTORE_SESSIONS_TOOL_DESCRIPTION = f"""\
Restore archived sessions of this project, all in one call, so they return to \
the user's session list and can take messages again: find their ids with \
`list_sessions` and `statuses: ["archived"]` first, then pass them all here. \
A session that is not archived is left as it is. {SESSION_REFERENCE}"""

MARK_SESSIONS_SEEN_TOOL_DESCRIPTION = f"""\
Mark other sessions of this project as seen, all in one call, once you have \
relayed to the user what matters in them, so they need not open them. Leave a \
session unread when the user should see it for themselves. A session that is \
working or waits for the user is not marked; a marked session turns unread \
again when it next finishes a turn. {SESSION_REFERENCE}"""

DEFAULT_LIST_LIMIT: Final = 20
MAX_LIST_LIMIT: Final = 50
MAX_MESSAGE_CHARS: Final = 4000
DEFAULT_MESSAGE_LIMIT: Final = 10
MAX_MESSAGE_LIMIT: Final = 50
MAX_SESSIONS_PER_CALL: Final = 50
PEER_REQUEST_TIMEOUT_SECONDS: Final = 30.0
SECONDS_PER_SESSION: Final = 5.0
SHORT_ID_CHARS: Final = 8
# Bounds a lookup in a project with very many sessions.
MAX_RESOLVE_PAGES: Final = 20
_SUGGESTED_SESSIONS: Final = 5
_ALL_STATUSES: Final[list[PeerSessionStatus]] = [
    "working",
    "idle",
    "waiting_for_user",
    "failed",
    "archived",
]

_IDEMPOTENCY_NAMESPACE: Final = uuid.UUID("9d6c0a3e-1f2b-4c7d-8e5a-6b4f3c2d1e0f")


class ListSessionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_LIST_LIMIT,
        description=(
            "Only these sessions, each named as other session tools take them. "
            "Left out, every other session."
        ),
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

    session_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The session to message. Left out, the session that started this one."
        ),
    )
    message: str = Field(
        min_length=1, max_length=MAX_MESSAGE_CHARS, description="The message to send."
    )
    delivery: PeerDelivery = Field(
        description=(
            "'start_turn' when the session must act on or answer it, 'queue' "
            "when it is only for its next turn to read."
        )
    )


class ArchiveSessionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_SESSIONS_PER_CALL,
        description="The sessions to archive.",
    )


class RestoreSessionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_SESSIONS_PER_CALL,
        description="The archived sessions to restore.",
    )


class MarkSessionsSeenArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_SESSIONS_PER_CALL,
        description="The sessions to mark as seen.",
    )


class SessionPeerError(Exception):
    def __init__(
        self, message: str, *, reason: SessionLimitRefusal | None = None
    ) -> None:
        super().__init__(message)
        self.reason = reason


class SessionPeers(Protocol):
    def client_capabilities(self) -> ClientCapabilities: ...

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

    async def restore_peer_sessions(
        self, params: SessionPeerRestoreParams
    ) -> SessionPeerRestoreResponse: ...

    async def mark_peer_sessions_seen(
        self, params: SessionPeerMarkSeenParams
    ) -> SessionPeerMarkSeenResponse: ...


type HopsOf = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class TurnStarts:
    """The sessions and turns a session's running turn started, which
    `MAX_TURN_STARTED_SESSIONS` caps.
    """

    of: Callable[[str], int]
    count: Callable[[str, int], None]


_NO_TURN_STARTS = TurnStarts(of=lambda _session_id: 0, count=lambda _id, _delta: None)
type SenderOf = Callable[[str], Awaitable[SessionMessageSender]]
type StarterOf = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class PeerContext:
    hops_of: HopsOf
    sender_of: SenderOf | None = None
    starter_of: StarterOf | None = None
    turn_starts: TurnStarts = _NO_TURN_STARTS


async def run_list_sessions(
    args: ListSessionsArgs, *, session_id: str, peers: SessionPeers
) -> SessionPeerListResponse:
    """A name that matches no single session is listed in ``not_found``, as
    an unknown id is.
    """
    session_ids = args.session_ids
    not_found: list[str] = []
    if session_ids is not None:
        # The client lists every session but the caller, so naming it is no error.
        others = [name for name in session_ids if name.strip() != session_id]
        session_ids, unresolved = await resolve_sessions(others, session_id, peers)
        not_found = list(unresolved)
        if not session_ids:
            return SessionPeerListResponse(sessions=[], not_found=not_found)
    listed = await _within(
        peers.list_peer_sessions(
            SessionPeerListParams(
                session_ids=session_ids,
                statuses=args.statuses,
                limit=args.limit,
                cursor=args.cursor,
            )
        ),
        PEER_REQUEST_TIMEOUT_SECONDS,
    )
    if not not_found:
        return listed
    return listed.model_copy(update={"not_found": [*listed.not_found, *not_found]})


async def run_read_session(
    args: ReadSessionArgs, *, session_id: str, peers: SessionPeers
) -> SessionPeerReadResponse:
    target = await _resolve_one(args.session_id, session_id, peers)
    return await _within(
        peers.read_peer_session(
            SessionPeerReadParams(
                target_session_id=target,
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
    peer: PeerContext,
) -> SessionPeerSendResponse:
    """The idempotency key only absorbs a repeated delivery of this one call,
    as for ``start_session``. Each message carries one hop more than the turn
    sending it, so two sessions cannot keep each other going on their own past
    ``MAX_SESSION_HOPS``.
    """
    hops = peer.hops_of(session_id)
    if hops >= MAX_SESSION_HOPS:
        raise SessionPeerError(
            f"Sessions have messaged or started each other {MAX_SESSION_HOPS} "
            "times in a row without the user writing, so this one cannot send "
            "messages until the user does. Tell the user instead.",
            reason=SESSION_LIMIT_HOPS,
        )
    target = (
        await _resolve_one(args.session_id, session_id, peers)
        if args.session_id is not None
        else _starter(session_id, peer)
    )
    starts_turn = args.delivery == "start_turn"
    if starts_turn and peer.turn_starts.of(session_id) >= MAX_TURN_STARTED_SESSIONS:
        raise SessionPeerError(
            f"This turn already started {MAX_TURN_STARTED_SESSIONS} turns on "
            "other sessions, the most one turn may. Send updates with "
            '`delivery: "queue"`, do the rest here, or tell the user.',
            reason=SESSION_LIMIT_STARTED_SESSIONS,
        )
    sender_of = peer.sender_of

    async def send() -> SessionPeerSendResponse:
        sender = (
            SessionMessageSender(session_id=session_id)
            if sender_of is None
            else await sender_of(session_id)
        )
        return await peers.send_to_peer_session(
            SessionPeerSendParams(
                target_session_id=target,
                text=args.message,
                sender=sender,
                delivery=args.delivery,
                hops=hops + 1,
                idempotency_key=str(
                    uuid.uuid5(_IDEMPOTENCY_NAMESPACE, f"{session_id}/{invocation_id}")
                ),
            )
        )

    if starts_turn:
        # Counted before the client answers, so sends made at once see each other.
        peer.turn_starts.count(session_id, 1)
    try:
        async with asyncio.timeout(PEER_REQUEST_TIMEOUT_SECONDS):
            return await send()
    except TimeoutError as exc:
        # The message may have gone out, so its slot stays taken.
        raise SessionPeerError(
            "The client did not confirm the message in time, so it may or may "
            "not have been sent. Read the session before sending it again."
        ) from exc
    except BaseException:
        if starts_turn:
            peer.turn_starts.count(session_id, -1)
        raise


def _starter(session_id: str, peer: PeerContext) -> str:
    """From the starter the session saved, so the model never retypes an id."""
    starter = None if peer.starter_of is None else peer.starter_of(session_id)
    if starter is None:
        raise SessionPeerError(
            "No session started this one, so name the session to message with "
            "`session_id`."
        )
    return starter


async def run_archive_sessions(
    args: ArchiveSessionsArgs, *, session_id: str, peers: SessionPeers
) -> SessionPeerArchiveResponse:
    resolved, unresolved = await resolve_sessions(args.session_ids, session_id, peers)
    failed = [
        PeerSessionNotArchived(session_id=reference, error=error)
        for reference, error in unresolved.items()
    ]
    if not resolved:
        return SessionPeerArchiveResponse(archived=[], failed=failed)
    answer = await _run_on_sessions(
        resolved,
        lambda session_ids: peers.archive_peer_sessions(
            SessionPeerArchiveParams(session_ids=session_ids)
        ),
        done="archived",
        doing="archiving",
    )
    return answer.model_copy(update={"failed": [*answer.failed, *failed]})


async def run_restore_sessions(
    args: RestoreSessionsArgs, *, session_id: str, peers: SessionPeers
) -> SessionPeerRestoreResponse:
    resolved, unresolved = await resolve_sessions(args.session_ids, session_id, peers)
    failed = [
        PeerSessionNotRestored(session_id=reference, error=error)
        for reference, error in unresolved.items()
    ]
    if not resolved:
        return SessionPeerRestoreResponse(restored=[], failed=failed)
    answer = await _run_on_sessions(
        resolved,
        lambda session_ids: peers.restore_peer_sessions(
            SessionPeerRestoreParams(session_ids=session_ids)
        ),
        done="restored",
        doing="restoring",
    )
    return answer.model_copy(update={"failed": [*answer.failed, *failed]})


async def run_mark_sessions_seen(
    args: MarkSessionsSeenArgs, *, session_id: str, peers: SessionPeers
) -> SessionPeerMarkSeenResponse:
    resolved, unresolved = await resolve_sessions(args.session_ids, session_id, peers)
    failed = [
        PeerSessionNotMarkedSeen(session_id=reference, error=error)
        for reference, error in unresolved.items()
    ]
    if not resolved:
        return SessionPeerMarkSeenResponse(marked=[], failed=failed)
    answer = await _run_on_sessions(
        resolved,
        lambda session_ids: peers.mark_peer_sessions_seen(
            SessionPeerMarkSeenParams(session_ids=session_ids)
        ),
        done="marked seen",
        doing="marking them",
    )
    return answer.model_copy(update={"failed": [*answer.failed, *failed]})


async def _run_on_sessions[T](
    requested_ids: list[str],
    request: Callable[[list[str]], Coroutine[Any, Any, T]],
    *,
    done: str,
    doing: str,
) -> T:
    session_ids = list(dict.fromkeys(requested_ids))
    return await _within(
        request(session_ids),
        PEER_REQUEST_TIMEOUT_SECONDS + SECONDS_PER_SESSION * len(session_ids),
        unknown_outcome=(
            f"The client did not confirm in time, so some sessions may have been "
            f"{done}. List them before {doing} again."
        ),
    )


async def resolve_sessions(
    references: Sequence[str], session_id: str, peers: SessionPeers
) -> tuple[list[str], dict[str, str]]:
    """The client's id of each reference that names one session, and why each
    other one does not. The model names a session by its id, a unique prefix
    of at least ``SHORT_ID_CHARS`` of it, or its title, case ignored, so a character it
    gets wrong in a long id is answered with the closest sessions to pick
    from, not taken for a session that does not exist.
    """
    wanted = [r for r in dict.fromkeys(references) if r.strip() != session_id]
    known = await _sessions_by_id([r.strip() for r in wanted], peers)
    sessions = (
        await _project_sessions(peers)
        if any(r.strip() not in known for r in wanted)
        else []
    )
    resolved: list[str] = []
    unresolved: dict[str, str] = {}
    for reference in dict.fromkeys(references):
        if reference.strip() == session_id:
            unresolved[reference] = "That is this session itself."
            continue
        if reference.strip() in known:
            resolved.append(reference.strip())
            continue
        matches = _matching(reference.strip(), sessions)
        if len(matches) == 1:
            resolved.append(matches[0].session_id)
        elif matches:
            unresolved[reference] = _ambiguous(reference, matches)
        else:
            unresolved[reference] = _unknown(reference, sessions)
    return list(dict.fromkeys(resolved)), unresolved


async def _resolve_one(reference: str, session_id: str, peers: SessionPeers) -> str:
    resolved, unresolved = await resolve_sessions([reference], session_id, peers)
    if resolved:
        return resolved[0]
    error = unresolved[reference]
    if reference.strip() == session_id:
        raise SessionPeerError(f"{error} Do the work here instead.")
    raise SessionPeerError(
        f"{error} Call again with one of those ids, the first "
        f"{SHORT_ID_CHARS} characters of one, or its title."
    )


async def _sessions_by_id(ids: Sequence[str], peers: SessionPeers) -> set[str]:
    """The ids the client knows as they are, asked for directly: a project's
    full listing is paged, and an id must not depend on where it falls in it.
    """
    known: set[str] = set()
    for start in range(0, len(ids), MAX_LIST_LIMIT):
        chunk = list(dict.fromkeys(ids[start : start + MAX_LIST_LIMIT]))
        page = await _within(
            peers.list_peer_sessions(
                SessionPeerListParams(
                    session_ids=chunk, statuses=_ALL_STATUSES, limit=len(chunk)
                )
            ),
            PEER_REQUEST_TIMEOUT_SECONDS,
        )
        known.update(session.session_id for session in page.sessions)
    return known


async def _project_sessions(peers: SessionPeers) -> list[PeerSessionSummary]:
    sessions: list[PeerSessionSummary] = []
    cursor: str | None = None
    for _ in range(MAX_RESOLVE_PAGES):
        page = await _within(
            peers.list_peer_sessions(
                SessionPeerListParams(
                    statuses=_ALL_STATUSES, limit=MAX_LIST_LIMIT, cursor=cursor
                )
            ),
            PEER_REQUEST_TIMEOUT_SECONDS,
        )
        sessions.extend(page.sessions)
        cursor = page.next_cursor
        if cursor is None:
            break
    return sessions


def _matching(
    reference: str, sessions: Sequence[PeerSessionSummary]
) -> list[PeerSessionSummary]:
    exact = [s for s in sessions if s.session_id == reference]
    if exact:
        return exact
    folded = reference.casefold()
    titled = [s for s in sessions if s.title and s.title.strip().casefold() == folded]
    if titled:
        return titled
    if len(reference) < SHORT_ID_CHARS:
        return []
    return [s for s in sessions if s.session_id.startswith(reference)]


def _unknown(reference: str, sessions: Sequence[PeerSessionSummary]) -> str:
    """Suggests sessions whose id starts as the reference does, then those
    whose id or title reads closest to it.
    """
    head = reference.strip()[:SHORT_ID_CHARS]
    sharing = (
        [s for s in sessions if s.session_id.startswith(head)]
        if len(head) == SHORT_ID_CHARS
        else []
    )
    keys = {s.session_id: s for s in sessions}
    keys.update({s.title: s for s in sessions if s.title})
    close = difflib.get_close_matches(
        reference, list(keys), n=_SUGGESTED_SESSIONS, cutoff=0.6
    )
    suggested = list(
        {s.session_id: s for s in [*sharing, *(keys[key] for key in close)]}.values()
    )[:_SUGGESTED_SESSIONS]
    if not suggested:
        return (
            f"No session of this project is named `{reference}`. Call "
            "`list_sessions` to find it."
        )
    return (
        f"No session of this project is named `{reference}`. The closest: "
        f"{_described(suggested)}."
    )


def _ambiguous(reference: str, matches: Sequence[PeerSessionSummary]) -> str:
    return (
        f"`{reference}` names {len(matches)} sessions: "
        f"{_described(matches[:_SUGGESTED_SESSIONS])}."
    )


def _described(sessions: Sequence[PeerSessionSummary]) -> str:
    return "; ".join(
        f"`{s.session_id}`" + (f' "{s.title}"' if s.title else "") + f" ({s.status})"
        for s in sessions
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
