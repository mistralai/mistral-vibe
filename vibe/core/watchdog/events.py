from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class EventKind(StrEnum):
    RUN_STARTED = auto()
    RUN_FINISHED = auto()
    RUN_FAILED = auto()
    RUN_CANCELLED = auto()
    MODEL_STARTED = auto()
    MODEL_ACTIVITY = auto()
    MODEL_FINISHED = auto()
    TOOL_STARTED = auto()
    TOOL_PROGRESS = auto()
    TOOL_FINISHED = auto()
    COMPACTION_STARTED = auto()
    COMPACTION_FINISHED = auto()
    WAITING_FOR_APPROVAL = auto()
    WAITING_FOR_USER = auto()
    WAIT_ENDED = auto()
    RECOVERY_STARTED = auto()
    RECOVERY_FINISHED = auto()
    VERIFICATION_STARTED = auto()
    VERIFICATION_FINISHED = auto()
    OBSERVER_ANOMALY = auto()
    INCIDENT_SUSPECTED = auto()
    INCIDENT_CONFIRMED = auto()
    INCIDENT_CLOSED = auto()


class WatchdogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    observed_at_monotonic: float = Field(ge=0)
    kind: EventKind
    epoch: int | None = Field(default=None, ge=0)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
