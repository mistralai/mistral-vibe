from __future__ import annotations

from enum import StrEnum, auto
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.watchdog.events import WatchdogEvent
from vibe.core.watchdog.models import (
    IncidentState,
    ObserverState,
    RecoveryStrategy,
    RunPhase,
    RunState,
)


class WatchdogMetricKind(StrEnum):
    RUN_STARTED = auto()
    RUN_FINISHED = auto()
    RUN_FAILED = auto()
    RUN_CANCELLED = auto()
    OBSERVER_ANOMALY = auto()
    INCIDENT_SUSPECTED = auto()
    INCIDENT_CONFIRMED = auto()
    INCIDENT_CLOSED = auto()
    RECOVERY_STARTED = auto()
    RECOVERY_FINISHED = auto()
    RECOVERY_FAILED = auto()
    VERIFICATION_FINISHED = auto()
    TILT_ENTERED = auto()
    TILT_CLEARED = auto()
    TILT_EVALUATED = auto()
    TILT_EVALUATION_FAILED = auto()
    SNAPSHOT_CREATED = auto()
    SNAPSHOT_APPLIED = auto()
    SNAPSHOT_DROPPED = auto()


class WatchdogDetectorKind(StrEnum):
    REPEATED_CALL = auto()
    TERMINAL = auto()
    OTHER = auto()


class WatchdogTelemetryEvent(BaseModel):
    """Secret-safe metric envelope: fixed enums and counters only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    kind: WatchdogMetricKind
    sequence: int = Field(ge=0)
    epoch: int = Field(ge=0)
    phase: RunPhase
    observer_state: ObserverState
    incident_state: IncidentState | None = None
    detector: WatchdogDetectorKind | None = None
    recovery_strategy: RecoveryStrategy | None = None


class WatchdogTelemetryPort(Protocol):
    def record(self, event: WatchdogTelemetryEvent) -> None: ...


class NullWatchdogTelemetry:
    def record(self, event: WatchdogTelemetryEvent) -> None:
        del event


def metric_from_event(
    event: WatchdogEvent, state: RunState
) -> WatchdogTelemetryEvent | None:
    try:
        kind = WatchdogMetricKind(event.kind.value)
    except ValueError:
        return None
    incident = state.incident
    detector: WatchdogDetectorKind | None = None
    if incident is not None and incident.owner is not None:
        try:
            detector = WatchdogDetectorKind(incident.owner)
        except ValueError:
            detector = WatchdogDetectorKind.OTHER
    return WatchdogTelemetryEvent(
        kind=kind,
        sequence=event.sequence,
        epoch=state.epoch,
        phase=state.phase,
        observer_state=state.observer_state,
        incident_state=incident.state if incident is not None else None,
        detector=detector,
        recovery_strategy=(
            incident.decision.strategy
            if incident is not None and incident.decision is not None
            else None
        ),
    )
