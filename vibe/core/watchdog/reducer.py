from __future__ import annotations

from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.models import Incident, ObserverState, RunPhase, RunState


class WatchdogReducerError(Exception):
    pass


class EventIdentityError(WatchdogReducerError):
    pass


class EventOrderError(WatchdogReducerError):
    pass


_PHASE_BY_EVENT = {
    EventKind.RUN_STARTED: RunPhase.IDLE,
    EventKind.RUN_FINISHED: RunPhase.IDLE,
    EventKind.RUN_FAILED: RunPhase.IDLE,
    EventKind.RUN_CANCELLED: RunPhase.IDLE,
    EventKind.MODEL_STARTED: RunPhase.MODEL,
    EventKind.MODEL_ACTIVITY: RunPhase.MODEL,
    EventKind.MODEL_FINISHED: RunPhase.IDLE,
    EventKind.TOOL_STARTED: RunPhase.TOOL,
    EventKind.TOOL_FINISHED: RunPhase.IDLE,
    EventKind.COMPACTION_STARTED: RunPhase.COMPACTION,
    EventKind.COMPACTION_FINISHED: RunPhase.IDLE,
    EventKind.WAITING_FOR_APPROVAL: RunPhase.WAITING_FOR_APPROVAL,
    EventKind.WAITING_FOR_USER: RunPhase.WAITING_FOR_USER,
    EventKind.WAIT_ENDED: RunPhase.IDLE,
    EventKind.RECOVERY_STARTED: RunPhase.RECOVERY,
    EventKind.RECOVERY_FINISHED: RunPhase.VERIFICATION,
    EventKind.VERIFICATION_STARTED: RunPhase.VERIFICATION,
    EventKind.VERIFICATION_FINISHED: RunPhase.IDLE,
}

_EPOCH_FENCED_EVENTS = {
    EventKind.RECOVERY_FINISHED,
    EventKind.VERIFICATION_STARTED,
    EventKind.VERIFICATION_FINISHED,
}


def apply_event(state: RunState, event: WatchdogEvent) -> RunState:
    if event.run_id != state.run_id or event.session_id != state.session_id:
        raise EventIdentityError("event does not belong to this Watchdog run")
    if event.sequence <= state.last_applied_sequence:
        raise EventOrderError(
            f"event sequence {event.sequence} is not after "
            f"{state.last_applied_sequence}"
        )

    next_state = state.model_copy(update={"last_applied_sequence": event.sequence})
    if event.kind == EventKind.OBSERVER_ANOMALY:
        return next_state.model_copy(update={"observer_state": ObserverState.TILT})
    if event.kind in {
        EventKind.INCIDENT_SUSPECTED,
        EventKind.INCIDENT_CONFIRMED,
        EventKind.INCIDENT_CLOSED,
    }:
        incident = Incident.model_validate(event.payload["incident"])
        return next_state.model_copy(
            update={"incident": incident, "epoch": incident.epoch}
        )
    if event.sequence != state.last_applied_sequence + 1:
        return next_state.model_copy(update={"observer_state": ObserverState.TILT})

    if (
        event.kind in _EPOCH_FENCED_EVENTS
        and event.epoch is not None
        and event.epoch != state.epoch
    ):
        return next_state

    updates: dict[str, object] = {}
    if phase := _PHASE_BY_EVENT.get(event.kind):
        updates["phase"] = phase
    if event.kind == EventKind.RECOVERY_STARTED and event.epoch is not None:
        updates["epoch"] = event.epoch
    if not updates:
        return next_state
    return next_state.model_copy(update=updates)
