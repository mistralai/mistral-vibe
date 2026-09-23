from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vibe.app_server._session_seen_state import (
    SessionSeenState,
    should_mark_session_unseen,
)
from vibe.app_server.models import (
    BlockedSessionStatus,
    FailedSessionStatus,
    IdleSessionStatus,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    RunningSessionStatus,
)
from vibe.core.types import SessionMetadata


def _state(
    *,
    status: IdleSessionStatus
    | RunningSessionStatus
    | BlockedSessionStatus
    | FailedSessionStatus,
    turn_status: PublicTurnStatus | None,
    turn_id: str = "turn-1",
    updated_at: int = 1,
) -> PublicSessionState:
    return PublicSessionState(
        event_id=0,
        session=PublicSession(
            id="session-1", status=status, created_at=0, updated_at=updated_at
        ),
        turns=(
            [
                PublicTurn(
                    id=turn_id,
                    session_id="session-1",
                    status=turn_status,
                    started_at=0,
                    completed_at=(
                        updated_at
                        if turn_status is not PublicTurnStatus.IN_PROGRESS
                        else None
                    ),
                )
            ]
            if turn_status is not None
            else []
        ),
    )


@pytest.mark.parametrize(
    "current",
    [
        _state(
            status=BlockedSessionStatus(
                active_turn_id="turn-1", callback_id="callback-1", reason="approval"
            ),
            turn_status=PublicTurnStatus.IN_PROGRESS,
            updated_at=2,
        ),
        _state(
            status=IdleSessionStatus(),
            turn_status=PublicTurnStatus.COMPLETED,
            updated_at=2,
        ),
        _state(
            status=FailedSessionStatus(message="failed"),
            turn_status=PublicTurnStatus.FAILED,
            updated_at=2,
        ),
    ],
)
def test_terminal_or_blocking_transitions_mark_a_session_unseen(
    current: PublicSessionState,
) -> None:
    previous = _state(
        status=RunningSessionStatus(active_turn_id="turn-1"),
        turn_status=PublicTurnStatus.IN_PROGRESS,
    )

    assert should_mark_session_unseen(previous, current)


@pytest.mark.parametrize(
    "current",
    [
        _state(
            status=RunningSessionStatus(active_turn_id="turn-1"),
            turn_status=PublicTurnStatus.IN_PROGRESS,
            updated_at=2,
        ),
        _state(
            status=IdleSessionStatus(),
            turn_status=PublicTurnStatus.INTERRUPTED,
            updated_at=2,
        ),
    ],
)
def test_streaming_and_user_interruption_do_not_mark_a_session_unseen(
    current: PublicSessionState,
) -> None:
    previous = _state(
        status=RunningSessionStatus(active_turn_id="turn-1"),
        turn_status=PublicTurnStatus.IN_PROGRESS,
    )

    assert not should_mark_session_unseen(previous, current)


def test_a_queued_turn_does_not_hide_the_completed_turn() -> None:
    previous = _state(
        status=RunningSessionStatus(active_turn_id="turn-1"),
        turn_status=PublicTurnStatus.IN_PROGRESS,
    )
    current = _state(
        status=RunningSessionStatus(active_turn_id="turn-2"),
        turn_id="turn-2",
        turn_status=PublicTurnStatus.IN_PROGRESS,
        updated_at=2,
    )

    assert should_mark_session_unseen(previous, current)


def test_a_new_blocking_callback_marks_a_blocked_session_unseen_again() -> None:
    previous = _state(
        status=BlockedSessionStatus(
            active_turn_id="turn-1", callback_id="callback-1", reason="approval"
        ),
        turn_status=PublicTurnStatus.IN_PROGRESS,
    )
    current = _state(
        status=BlockedSessionStatus(
            active_turn_id="turn-1", callback_id="callback-2", reason="user_input"
        ),
        turn_status=PublicTurnStatus.IN_PROGRESS,
        updated_at=2,
    )

    assert should_mark_session_unseen(previous, current)


def test_repeated_terminal_state_does_not_mark_a_session_unseen_again() -> None:
    state = _state(status=IdleSessionStatus(), turn_status=PublicTurnStatus.COMPLETED)

    assert not should_mark_session_unseen(state, state)


def test_metadata_before_unseen_tracking_defaults_to_seen() -> None:
    metadata = SessionMetadata.model_validate({
        "session_id": "session-1",
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-01T00:01:00Z",
        "git_commit": None,
        "git_branch": None,
        "environment": {},
        "username": "test",
    })

    assert metadata.unseen_at is None
    assert metadata.seen_at is None
    assert not SessionSeenState.from_metadata(metadata).is_unseen


def test_unseen_state_is_derived_from_metadata_timestamps() -> None:
    metadata = SessionMetadata(
        session_id="session-1",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:01:00Z",
        git_commit=None,
        git_branch=None,
        environment={},
        username="test",
        unseen_at="2026-01-01T00:03:00Z",
        seen_at="2026-01-01T00:02:00Z",
    )

    seen_state = SessionSeenState.from_metadata(metadata)

    assert seen_state.is_unseen
    assert not seen_state.mark_seen(datetime(2026, 1, 1, 0, 4, tzinfo=UTC)).is_unseen
