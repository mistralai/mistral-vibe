from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.watchdog import (
    DeadlineStatus,
    EventKind,
    ExitClassification,
    Heartbeat,
    HeartbeatWriter,
    PhaseDeadlineTracker,
    RunPhase,
    SentinelAction,
    SentinelPolicy,
    TiltTracker,
    WatchdogEvent,
)


class FakeProbe:
    def __init__(self, responsive: bool) -> None:
        self._responsive = responsive

    async def responsive(self, phase: RunPhase) -> bool:
        return self._responsive


@pytest.mark.asyncio
async def test_deadline_requires_failed_liveness_probe_to_confirm() -> None:
    tracker = PhaseDeadlineTracker({RunPhase.TOOL: 10})
    tracker.activity(RunPhase.TOOL, now=0)

    assert (
        await tracker.evaluate(now=11, probe=FakeProbe(True)) == DeadlineStatus.HEALTHY
    )
    assert (
        await tracker.evaluate(now=22, probe=FakeProbe(False))
        == DeadlineStatus.CONFIRMED
    )


@pytest.mark.asyncio
async def test_user_wait_disables_deadline() -> None:
    tracker = PhaseDeadlineTracker({RunPhase.TOOL: 10})

    status = tracker.activity(RunPhase.WAITING_FOR_USER, now=0)

    assert status == DeadlineStatus.WAITING
    assert (
        await tracker.evaluate(now=1_000, probe=FakeProbe(False))
        == DeadlineStatus.WAITING
    )


def test_tilt_requires_fresh_ordered_window() -> None:
    tracker = TiltTracker(fresh_window=3)
    tracker.enter()

    results = [tracker.observe(event(sequence)) for sequence in (1, 3, 4, 5)]

    assert results == [None, None, None, EventKind.TILT_CLEARED]


@pytest.mark.asyncio
async def test_heartbeat_is_atomic_and_contains_no_prompt(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    heartbeat = Heartbeat.sample(
        run_id="run-1",
        session_id="session-1",
        epoch=1,
        phase=RunPhase.TOOL,
        sequence=4,
        monotonic_sample=10,
    )

    await HeartbeatWriter(path).write(heartbeat)

    restored = Heartbeat.model_validate_json(path.read_text())
    assert restored == heartbeat
    assert "prompt" not in path.read_text().lower()


def test_sentinel_restarts_only_confirmed_unexpected_failures() -> None:
    policy = SentinelPolicy(restart_cap=2)

    assert (
        policy.decide(
            exit_classification=ExitClassification.UNEXPECTED,
            heartbeat_stale=False,
            clock_discontinuity=False,
            process_responsive=False,
            grace_elapsed=True,
            restart_count=0,
        )
        == SentinelAction.RESTART
    )
    assert (
        policy.decide(
            exit_classification=ExitClassification.RUNNING,
            heartbeat_stale=True,
            clock_discontinuity=True,
            process_responsive=False,
            grace_elapsed=True,
            restart_count=0,
        )
        == SentinelAction.TILT_GRACE
    )
    assert (
        policy.decide(
            exit_classification=ExitClassification.UNEXPECTED,
            heartbeat_stale=False,
            clock_discontinuity=False,
            process_responsive=False,
            grace_elapsed=True,
            restart_count=2,
        )
        == SentinelAction.NEEDS_USER
    )


def event(sequence: int) -> WatchdogEvent:
    return WatchdogEvent(
        run_id="run-1",
        session_id="session-1",
        sequence=sequence,
        observed_at_monotonic=float(sequence),
        kind=EventKind.MODEL_ACTIVITY,
    )
