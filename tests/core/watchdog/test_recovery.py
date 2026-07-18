from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vibe.core.watchdog import (
    CancelResult,
    ContinuationCoordinator,
    Evidence,
    IdleResult,
    Incident,
    IncidentState,
    QuiesceResult,
    RecoveryCoordinator,
    RecoveryDecision,
    RunState,
    WatchdogPaths,
    WatchdogStore,
)


class FakeRecoveryPort:
    def __init__(self, *, idle: bool = True) -> None:
        self.injected: list[str] = []
        self.continuations: list[str] = []
        self.idle = idle

    async def quiesce(self, incident: Incident) -> QuiesceResult:
        return QuiesceResult(succeeded=True)

    async def cancel_active(self, incident: Incident) -> CancelResult:
        return CancelResult(succeeded=True)

    async def wait_until_idle(self, incident: Incident) -> IdleResult:
        return IdleResult(succeeded=self.idle)

    async def inject_context(self, content: str) -> None:
        self.injected.append(content)

    async def continue_once(self, prompt: str, incident: Incident) -> None:
        self.continuations.append(prompt)

    async def request_approval(self, decision: RecoveryDecision) -> bool:
        return False


def confirmed_state() -> RunState:
    return RunState(
        run_id="run-1",
        session_id="session-1",
        incident=Incident(
            incident_id="incident-1",
            epoch=0,
            state=IncidentState.CONFIRMED,
            owner="repeated_call",
            evidence=(
                Evidence(
                    detector="repeated_call",
                    fingerprint="evidence-1",
                    observed_sequence=1,
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_context_injection_is_persisted_before_execution(tmp_path: Path) -> None:
    paths = WatchdogPaths.for_run("run-1", root=tmp_path)
    store = WatchdogStore(paths)
    port = FakeRecoveryPort()
    coordinator = RecoveryCoordinator(store=store, port=port)

    recovered = await coordinator.recover(confirmed_state(), observed_at=1)

    assert recovered.incident is not None
    assert recovered.incident.state == IncidentState.VERIFYING
    assert recovered.incident.epoch == 1
    assert len(port.injected) == 1
    decisions = paths.decisions.read_text()
    assert "inject_context" in decisions
    assert await store.replay() == recovered


@pytest.mark.asyncio
async def test_concurrent_recovery_triggers_inject_exactly_once(tmp_path: Path) -> None:
    store = WatchdogStore(WatchdogPaths.for_run("run-1", root=tmp_path))
    port = FakeRecoveryPort()
    coordinator = RecoveryCoordinator(store=store, port=port)
    state = confirmed_state()

    await asyncio.gather(
        coordinator.recover(state, observed_at=1),
        coordinator.recover(state, observed_at=1),
    )

    assert len(port.injected) == 1


@pytest.mark.asyncio
async def test_continuation_starts_exactly_once_after_idle(tmp_path: Path) -> None:
    store = WatchdogStore(WatchdogPaths.for_run("run-1", root=tmp_path))
    port = FakeRecoveryPort()
    coordinator = ContinuationCoordinator(store=store, port=port)
    base_state = confirmed_state()
    incident = base_state.incident
    assert incident is not None
    state = base_state.model_copy(
        update={
            "incident": incident.model_copy(
                update={"state": IncidentState.VERIFYING, "epoch": 1}
            ),
            "epoch": 1,
        }
    )

    first, second = await asyncio.gather(
        coordinator.continue_once(state, prompt="recover", observed_at=1),
        coordinator.continue_once(state, prompt="recover", observed_at=1),
    )

    assert len(port.continuations) == 1
    assert not first.pending_continuation
    assert not second.pending_continuation


@pytest.mark.asyncio
async def test_active_loop_does_not_start_continuation(tmp_path: Path) -> None:
    store = WatchdogStore(WatchdogPaths.for_run("run-1", root=tmp_path))
    port = FakeRecoveryPort(idle=False)
    coordinator = ContinuationCoordinator(store=store, port=port)

    state = await coordinator.continue_once(
        confirmed_state(), prompt="recover", observed_at=1
    )

    assert port.continuations == []
    assert not state.pending_continuation
