from __future__ import annotations

from enum import StrEnum, auto
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class RunPhase(StrEnum):
    IDLE = auto()
    MODEL = auto()
    TOOL = auto()
    COMPACTION = auto()
    WAITING_FOR_APPROVAL = auto()
    WAITING_FOR_USER = auto()
    RECOVERY = auto()
    VERIFICATION = auto()


class ObserverState(StrEnum):
    TRUSTED = auto()
    TILT = auto()


class IncidentState(StrEnum):
    SUSPECTED = auto()
    CONFIRMED = auto()
    SELECTING_RECOVERY = auto()
    QUIESCING = auto()
    RECOVERING = auto()
    VERIFYING = auto()
    DEGRADED = auto()
    NEEDS_USER = auto()
    FAILED = auto()
    CLOSED = auto()


class RecoveryStrategy(StrEnum):
    INJECT_CONTEXT = auto()
    REWRITE_COMMAND = auto()
    ALTERNATE_TOOL = auto()
    CANCEL_AND_CONTINUE = auto()
    RESTORE_CHECKPOINT = auto()
    LLM_RECOVERY = auto()
    RESTART_PROCESS = auto()
    ASK_USER = auto()
    STOP = auto()


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_FrozenModel):
    detector: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    observed_sequence: int = Field(ge=1)
    expires_at_sequence: int | None = Field(default=None, ge=1)
    facts: dict[str, JsonValue] = Field(default_factory=dict)


class RecoveryDecision(_FrozenModel):
    strategy: RecoveryStrategy
    requires_approval: bool = False
    reason_code: str = Field(min_length=1)


class Incident(_FrozenModel):
    incident_id: str = Field(min_length=1)
    epoch: int = Field(ge=0)
    state: IncidentState
    owner: str | None = None
    evidence: tuple[Evidence, ...] = ()
    decision: RecoveryDecision | None = None


class RunState(_FrozenModel):
    schema_version: int = Field(default=1, ge=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    phase: RunPhase = RunPhase.IDLE
    observer_state: ObserverState = ObserverState.TRUSTED
    last_applied_sequence: int = Field(default=0, ge=0)
    epoch: int = Field(default=0, ge=0)
    incident: Incident | None = None
    pending_continuation: bool = False

    @classmethod
    def new(cls, *, run_id: str, session_id: str) -> Self:
        return cls(run_id=run_id, session_id=session_id)
