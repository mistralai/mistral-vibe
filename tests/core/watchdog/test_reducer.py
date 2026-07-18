from __future__ import annotations

import pytest

from vibe.core.watchdog import (
    EventKind,
    EventOrderError,
    ObserverState,
    RunPhase,
    RunState,
    WatchdogEvent,
    apply_event,
)


def event(sequence: int, kind: EventKind, *, epoch: int | None = None) -> WatchdogEvent:
    return WatchdogEvent(
        run_id="run-1",
        session_id="session-1",
        sequence=sequence,
        observed_at_monotonic=float(sequence),
        kind=kind,
        epoch=epoch,
    )


def test_same_event_stream_produces_same_state() -> None:
    events = [
        event(1, EventKind.RUN_STARTED),
        event(2, EventKind.MODEL_STARTED),
        event(3, EventKind.TOOL_STARTED),
        event(4, EventKind.TOOL_FINISHED),
    ]

    def reduce_stream() -> RunState:
        state = RunState.new(run_id="run-1", session_id="session-1")
        for item in events:
            state = apply_event(state, item)
        return state

    assert reduce_stream() == reduce_stream()
    assert reduce_stream().phase == RunPhase.IDLE


def test_out_of_order_event_is_rejected() -> None:
    state = apply_event(
        RunState.new(run_id="run-1", session_id="session-1"),
        event(1, EventKind.RUN_STARTED),
    )

    with pytest.raises(EventOrderError):
        apply_event(state, event(1, EventKind.MODEL_STARTED))


def test_sequence_gap_enters_tilt() -> None:
    state = apply_event(
        RunState.new(run_id="run-1", session_id="session-1"),
        event(2, EventKind.MODEL_STARTED),
    )

    assert state.observer_state == ObserverState.TILT
    assert state.phase == RunPhase.IDLE


def test_stale_recovery_result_is_ignored() -> None:
    state = RunState(
        run_id="run-1",
        session_id="session-1",
        phase=RunPhase.RECOVERY,
        last_applied_sequence=3,
        epoch=2,
    )

    updated = apply_event(state, event(4, EventKind.RECOVERY_FINISHED, epoch=1))

    assert updated.phase == RunPhase.RECOVERY
    assert updated.last_applied_sequence == 4
