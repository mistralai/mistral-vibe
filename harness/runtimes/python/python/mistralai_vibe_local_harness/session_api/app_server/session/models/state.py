"""Public Session catalog and retained state views."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ....session_values import (
    CancelledCause as CancelledCause,
)
from ....session_values import (
    CancelledTermination as CancelledTermination,
)
from ....session_values import (
    CompletedStopReason as CompletedStopReason,
)
from ....session_values import (
    CompletedTermination as CompletedTermination,
)
from ....session_values import (
    FailedTermination as FailedTermination,
)
from ....session_values import (
    Termination as Termination,
)
from ....session_values import (
    UnknownTermination as UnknownTermination,
)
from .base import SessionModel
from .callbacks import SessionCallback
from .common import (
    EventId,
    HistoryEntryId,
    SessionId,
    SessionStatus,
    TurnId,
    TurnStatus,
    UnixMs,
)
from .history import HistoryEntry

type SortDirection = Literal["forward", "backward"]


class ListPageRequest(SessionModel):
    """Continue a fixed-order catalog or graph listing with its opaque cursor."""

    cursor: str | None = None
    limit: Annotated[int, Field(ge=1)] = 50


class PageRequest(SessionModel):
    cursor: str | None = None
    limit: Annotated[int, Field(ge=1)] = 50
    direction: SortDirection = "backward"


class Page[ItemT](SessionModel):
    items: list[ItemT] = Field(default_factory=list)
    next_cursor: str | None = None
    previous_cursor: str | None = None


class TurnState(SessionModel):
    id: TurnId
    status: TurnStatus
    started_at: UnixMs | None = None
    completed_at: UnixMs | None = None
    history_entry_ids: list[HistoryEntryId] = Field(default_factory=list)
    termination: Termination | None = None
    started_after_event_id: EventId | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "The id of the Session event just before this Turn's turn_started event. Reading "
            "the Session's events after it gives the Turn's events from its start. Absent when "
            "the host does not record it."
        ),
    )

    @model_validator(mode="after")
    def validate_termination(self) -> Self:
        if self.status == "terminated" and self.termination is None:
            raise ValueError("A terminated turn requires termination details")
        if self.status != "terminated" and self.termination is not None:
            raise ValueError("A non-terminal turn cannot have termination details")
        return self


class Session(SessionModel):
    """Catalog view for one stored Session."""

    id: SessionId
    root_session_id: SessionId
    parent_session_id: SessionId | None = None
    title: str | None = None
    preview: str = ""
    status: SessionStatus
    termination: Termination | None = None
    created_at: UnixMs
    updated_at: UnixMs
    archived_at: UnixMs | None = None

    @model_validator(mode="after")
    def validate_termination(self) -> Self:
        if self.status == "terminated" and self.termination is None:
            raise ValueError("A terminated Session requires termination details")
        if self.status != "terminated" and self.termination is not None:
            raise ValueError("A non-terminal Session cannot have termination details")
        return self


class SessionState(SessionModel):
    """Public projection returned by stateless Session reads."""

    session: Session
    active_turn_id: TurnId | None = None
    history: Page[HistoryEntry] | None = None
    turns: Page[TurnState] | None = None
    active_callbacks: list[SessionCallback] = Field(default_factory=list)
