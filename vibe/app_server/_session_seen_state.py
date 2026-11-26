from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from vibe.app_server._utils import iso_from_time_ms, optional_time_ms
from vibe.app_server.models import (
    BlockedSessionStatus,
    FailedSessionStatus,
    PublicSessionState,
    PublicTurnStatus,
)
from vibe.core.types import SessionMetadata


@dataclass(frozen=True, slots=True)
class SessionSeenState:
    unseen_at: str | None
    seen_at: str | None

    @classmethod
    def from_metadata(cls, metadata: SessionMetadata | None) -> SessionSeenState:
        if metadata is None:
            return cls(unseen_at=None, seen_at=None)
        return cls(unseen_at=metadata.unseen_at, seen_at=metadata.seen_at)

    @property
    def is_unseen(self) -> bool:
        unseen_at = optional_time_ms(self.unseen_at)
        if unseen_at is None:
            return False
        seen_at = optional_time_ms(self.seen_at)
        return seen_at is None or unseen_at > seen_at

    # The state is compared at millisecond precision, so a mark landing in the
    # same millisecond as the other one, or on a clock that stepped back, is
    # moved just past it: the latest call always wins.
    def mark_unseen(self, at: datetime) -> SessionSeenState:
        seen_at = optional_time_ms(self.seen_at)
        if seen_at is not None and _ms(at) <= seen_at:
            return SessionSeenState(
                unseen_at=iso_from_time_ms(seen_at + 1), seen_at=self.seen_at
            )
        return SessionSeenState(
            unseen_at=at.astimezone(UTC).isoformat(), seen_at=self.seen_at
        )

    def mark_seen(self, at: datetime) -> SessionSeenState:
        unseen_at = optional_time_ms(self.unseen_at)
        if unseen_at is not None and _ms(at) < unseen_at:
            return SessionSeenState(
                unseen_at=self.unseen_at, seen_at=iso_from_time_ms(unseen_at)
            )
        return SessionSeenState(
            unseen_at=self.unseen_at, seen_at=at.astimezone(UTC).isoformat()
        )

    def apply_to(self, metadata: SessionMetadata) -> SessionMetadata:
        return metadata.model_copy(
            update={"unseen_at": self.unseen_at, "seen_at": self.seen_at}
        )


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def should_mark_session_unseen(
    previous: PublicSessionState, current: PublicSessionState
) -> bool:
    if isinstance(current.session.status, BlockedSessionStatus) and (
        not isinstance(previous.session.status, BlockedSessionStatus)
        or previous.session.status.callback_id != current.session.status.callback_id
    ):
        return True
    if isinstance(current.session.status, FailedSessionStatus) and not isinstance(
        previous.session.status, FailedSessionStatus
    ):
        return True

    current_turn = current.latest_turn
    previous_turn = previous.latest_turn
    if (
        previous_turn is not None
        and previous_turn.status is PublicTurnStatus.IN_PROGRESS
        and current_turn is not None
        and current_turn.id != previous_turn.id
    ):
        # A queued turn can start in the same snapshot that makes the prior turn
        # terminal, so the prior terminal state is not always projected.
        return True
    if current_turn is None or current_turn.status not in {
        PublicTurnStatus.COMPLETED,
        PublicTurnStatus.FAILED,
    }:
        return False
    return (
        previous_turn is None
        or previous_turn.id != current_turn.id
        or previous_turn.status != current_turn.status
    )
