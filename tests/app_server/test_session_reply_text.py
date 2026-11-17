from __future__ import annotations

from vibe.app_server._session_peers import MAX_MESSAGE_CHARS
from vibe.app_server._session_reply import REPLY_CUT_MARK, turn_reply_text
from vibe.app_server.models import (
    PublicError,
    PublicTurn,
    PublicTurnStatus,
    PublicTurnStopReason,
)


def _turn(
    status: PublicTurnStatus,
    *,
    error: str | None = None,
    stop_reason: PublicTurnStopReason | None = None,
) -> PublicTurn:
    return PublicTurn(
        id="turn-1",
        session_id="child-1",
        status=status,
        started_at=0,
        completed_at=1,
        error=PublicError(message=error) if error is not None else None,
        stop_reason=stop_reason,
    )


def test_a_reply_gives_the_outcome_then_the_final_message() -> None:
    replies = [
        turn_reply_text(_turn(PublicTurnStatus.COMPLETED), "Fixed the flaky test."),
        turn_reply_text(
            _turn(PublicTurnStatus.FAILED, error="Rate limited"), "Halfway there."
        ),
        turn_reply_text(_turn(PublicTurnStatus.INTERRUPTED), None),
        turn_reply_text(_turn(PublicTurnStatus.COMPLETED), "   "),
        turn_reply_text(
            _turn(PublicTurnStatus.COMPLETED, stop_reason=PublicTurnStopReason.LIMIT),
            "Ran out of steps.",
        ),
    ]

    assert replies == [
        "Fixed the flaky test.",
        "Its turn failed: Rate limited\n\nHalfway there.",
        "The user stopped its turn before it replied.",
        "Finished its turn without a reply.",
        "Stopped at its step limit.\n\nRan out of steps.",
    ]


def test_a_long_reply_is_cut_to_what_a_session_message_holds() -> None:
    reply = turn_reply_text(
        _turn(PublicTurnStatus.FAILED, error="Rate limited"), "x" * 10_000
    )

    assert len(reply) == MAX_MESSAGE_CHARS
    assert reply.endswith(REPLY_CUT_MARK)
