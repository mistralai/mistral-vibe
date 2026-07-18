from __future__ import annotations

from vibe.core.watchdog.detectors import RepeatedCallDetector
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.incident import IncidentEngine
from vibe.core.watchdog.models import IncidentState, RunState
from vibe.core.watchdog.reducer import apply_event


def event(sequence: int, kind: EventKind, payload: dict) -> WatchdogEvent:
    return WatchdogEvent(
        run_id="run-1",
        session_id="session-1",
        sequence=sequence,
        observed_at_monotonic=float(sequence),
        kind=kind,
        payload=payload,
    )


def apply_transition(state: RunState, transition) -> RunState:
    transition_event = event(
        state.last_applied_sequence + 1,
        transition.kind,
        {"incident": transition.incident.model_dump(mode="json")},
    )
    return apply_event(state, transition_event)


def test_repeated_call_produces_stable_incident_timeline() -> None:
    engine = IncidentEngine((RepeatedCallDetector(threshold=2),))
    state = RunState.new(run_id="run-1", session_id="session-1")
    source_events = [
        event(
            1,
            EventKind.TOOL_STARTED,
            {"tool_call_id": "a", "tool_name": "bash", "arguments": {"cmd": "x"}},
        ),
        event(
            2,
            EventKind.TOOL_FINISHED,
            {
                "tool_call_id": "a",
                "result": "failed",
                "repository_fingerprint": "repo-1",
            },
        ),
        event(
            3,
            EventKind.TOOL_STARTED,
            {"tool_call_id": "b", "tool_name": "bash", "arguments": {"cmd": "x"}},
        ),
    ]
    for source in source_events:
        source = source.model_copy(update={"sequence": state.last_applied_sequence + 1})
        state = apply_event(state, source)
        transitions = engine.observe(source, state)
        if transitions:
            state = apply_transition(state, transitions[0])

    assert state.incident is not None
    assert state.incident.state == IncidentState.SUSPECTED
    incident_id = state.incident.incident_id

    result = event(
        state.last_applied_sequence + 1,
        EventKind.TOOL_FINISHED,
        {"tool_call_id": "b", "result": "failed", "repository_fingerprint": "repo-1"},
    )
    state = apply_event(state, result)
    transition = engine.observe(result, state)[0]
    state = apply_transition(state, transition)

    assert state.incident is not None
    assert state.incident.incident_id == incident_id
    assert state.incident.state == IncidentState.CONFIRMED
    assert state.incident.epoch == 0
