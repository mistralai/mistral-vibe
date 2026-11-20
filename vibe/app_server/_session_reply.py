"""A session another one started replies to it when a turn that session
started ends, so the model never reports back by hand.

The reply owed is saved with the session before the turn runs, its text added
as soon as the turn ends, and cleared once sent, so a restart in between still
sends it, once: the client resumes a session that `session/list` reports as
owing one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final
import uuid

from vibe.app_server._session_peers import (
    MAX_MESSAGE_CHARS,
    SessionPeerError,
    SessionPeers,
)
from vibe.app_server._session_spawn import MAX_SESSION_HOPS
from vibe.app_server.models import PublicTurn, PublicTurnStatus, PublicTurnStopReason
from vibe.app_server.protocol import (
    SessionMessageSender,
    SessionPeerListParams,
    SessionPeerSendParams,
)
from vibe.core.types import OwedReply
from vibe.observability.logging import logger

# Long enough for the title and queue drain that follow a turn; a turn the
# session runs meanwhile does not hold the reply back.
SETTLE_SECONDS: Final = 30.0
_SETTLE_POLL_SECONDS: Final = 0.05
# A reply the client could not take is sent again for as long as the session
# stays open, waiting longer each time up to the cap.
SEND_RETRY_SECONDS: Final = 2.0
SEND_RETRY_MAX_SECONDS: Final = 60.0
REPLY_CUT_MARK: Final = "\n\n[Cut short. Read the session for the rest.]"
_IDEMPOTENCY_NAMESPACE: Final = uuid.UUID("0c6f1e9a-3f0b-4d55-9b52-6e1a4f0d7c21")


def replies_to(peers: SessionPeers | None) -> bool:
    return peers is not None and peers.client_capabilities().session_peers


def owed_reply(sender: str, turn_id: str, hops: int) -> OwedReply:
    return OwedReply(to_session_id=sender, turn_id=turn_id, hops=hops)


def final_message(entries: Sequence[Mapping[str, object]], turn_id: str) -> str | None:
    """The turn's last assistant text, from raw Harness history entries."""
    for entry in reversed(entries):
        if (
            entry.get("type") != "message"
            or entry.get("role") != "assistant"
            or entry.get("turnId") != turn_id
        ):
            continue
        content = entry.get("content")
        if not isinstance(content, list):
            continue
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        if text := "\n\n".join(texts).strip():
            return text
    return None


@dataclass(frozen=True, slots=True)
class ReplyRoute:
    peers: SessionPeers
    session_id: str
    sender: Callable[[], Awaitable[SessionMessageSender]]
    # True while the session is still finishing the turn's work.
    busy: Callable[[], bool]
    final_message: Callable[[str], Awaitable[str | None]]


async def retrying[T](
    attempt: Callable[[], Awaitable[T]], failure: str, session_id: str
) -> T:
    """Tried again, waiting longer each time, until it succeeds or the session
    closes. Logged once: one that never succeeds would log every minute.
    """
    delay = SEND_RETRY_SECONDS
    logged = False
    while True:
        try:
            return await attempt()
        except Exception:
            if not logged:
                logger.warning(failure, extra={"session_id": session_id}, exc_info=True)
                logged = True
        await asyncio.sleep(delay)
        delay = min(delay * 2, SEND_RETRY_MAX_SECONDS)


async def read_reply_text(route: ReplyRoute, turn: PublicTurn) -> str:
    """What the ended turn tells its starter."""

    async def read() -> str:
        return turn_reply_text(turn, await route.final_message(turn.id))

    return await retrying(
        read, "Failed to read a reply's text; retrying", route.session_id
    )


async def settled(route: ReplyRoute) -> None:
    """The session finished the ended turn's work, or the wait ran out."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SETTLE_SECONDS
    while route.busy() and loop.time() < deadline:
        await asyncio.sleep(_SETTLE_POLL_SECONDS)


def turn_reply_text(turn: PublicTurn, final_message: str | None) -> str:
    message = (final_message or "").strip()
    match turn.status:
        case PublicTurnStatus.FAILED:
            reason = turn.error.message if turn.error is not None else "unknown error"
            outcome = f"Its turn failed: {reason}"
        case PublicTurnStatus.INTERRUPTED:
            outcome = (
                "The user stopped its turn."
                if message
                else "The user stopped its turn before it replied."
            )
        case _ if turn.stop_reason is PublicTurnStopReason.LIMIT:
            outcome = "Stopped at its step limit."
        case _ if turn.stop_reason is PublicTurnStopReason.LENGTH:
            outcome = "Its last answer was cut off at the output limit."
        case _ if message:
            outcome = None
        case _:
            outcome = "Finished its turn without a reply."
    text = "\n\n".join(part for part in (outcome, message) if part)
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return text[: MAX_MESSAGE_CHARS - len(REPLY_CUT_MARK)] + REPLY_CUT_MARK


async def send_reply(route: ReplyRoute, owed: OwedReply, text: str) -> bool:
    """True once the client took it or its target is gone. Retried for as
    long as the session stays open and the client can carry replies; False
    when it no longer can, so it stays owed for the next open.
    """
    delay = SEND_RETRY_SECONDS
    attempt = 0
    while replies_to(route.peers):
        if attempt:
            await asyncio.sleep(delay)
            delay = min(delay * 2, SEND_RETRY_MAX_SECONDS)
        attempt += 1
        try:
            await route.peers.send_to_peer_session(
                SessionPeerSendParams(
                    target_session_id=owed.to_session_id,
                    text=text,
                    sender=await route.sender(),
                    delivery="start_turn",
                    hops=min(owed.hops + 1, MAX_SESSION_HOPS),
                    reply=True,
                    reply_turn_id=owed.turn_id,
                    idempotency_key=str(
                        uuid.uuid5(
                            _IDEMPOTENCY_NAMESPACE,
                            f"{route.session_id}/{owed.turn_id}/{owed.to_session_id}",
                        )
                    ),
                )
            )
        except Exception as exc:
            if isinstance(exc, SessionPeerError) and await _starter_gone(
                route.peers, owed.to_session_id
            ):
                logger.info(
                    "Dropped a reply to a session that is gone or archived",
                    extra={"session_id": route.session_id},
                )
                return True
            # Once: a refusal that never clears would log every minute.
            if attempt == 1:
                logger.warning(
                    "Failed to send a reply to the session that started a turn; "
                    "retrying",
                    extra={"session_id": route.session_id, "error": str(exc)},
                )
            continue
        return True
    return False


async def _starter_gone(peers: SessionPeers, session_id: str) -> bool:
    """No later attempt could land: the session left the project or was
    archived.
    """
    try:
        listed = await peers.list_peer_sessions(
            SessionPeerListParams(
                session_ids=[session_id],
                statuses=["working", "idle", "waiting_for_user", "failed", "archived"],
                limit=1,
            )
        )
    except SessionPeerError:
        return False
    return all(session.status == "archived" for session in listed.sessions)
