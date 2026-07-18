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
    RecoveryAdvice,
    RecoveryAdviceRequest,
    RecoveryCoordinator,
    RecoveryDecision,
    RecoveryStrategy,
    RestoreResult,
    RunPhase,
    RunState,
    WatchdogPaths,
    WatchdogStore,
)


class FakeRecoveryPort:
    def __init__(self, *, idle: bool = True) -> None:
        self.injected: list[str] = []
        self.continuations: list[str] = []
        self.idle = idle
        self.restores = 0
        self.user_handoffs = 0

    async def quiesce(self, incident: Incident) -> QuiesceResult:
        return QuiesceResult(succeeded=True)

    async def cancel_active(self, incident: Incident) -> CancelResult:
        return CancelResult(succeeded=True)

    async def wait_until_idle(self, incident: Incident) -> IdleResult:
        return IdleResult(succeeded=self.idle)

    async def inject_context(self, content: str) -> None:
        self.injected.append(content)

    async def restore_latest_snapshot(self, incident: Incident) -> RestoreResult:
        del incident
        self.restores += 1
        return RestoreResult(succeeded=True, detail="snapshot-1")

    async def request_user_input(self, content: str, incident: Incident) -> None:
        del content, incident
        self.user_handoffs += 1

    async def continue_once(self, prompt: str, incident: Incident) -> None:
        self.continuations.append(prompt)

    async def request_approval(self, decision: RecoveryDecision) -> bool:
        return False


class FakeRecoveryAdvisor:
    def __init__(self) -> None:
        self.requests: list[RecoveryAdviceRequest] = []

    async def advise(self, request: RecoveryAdviceRequest) -> RecoveryAdvice:
        self.requests.append(request)
        if request.strategy == RecoveryStrategy.REWRITE_COMMAND:
            return RecoveryAdvice(
                strategy=request.strategy,
                reason="rewrite",
                command="pytest -q --cache-clear",
            )
        if request.strategy == RecoveryStrategy.ALTERNATE_TOOL:
            return RecoveryAdvice(
                strategy=request.strategy, reason="alternate", tool="edit"
            )
        return RecoveryAdvice(
            strategy=request.strategy,
            reason="final plan",
            tool="grep",
            command="inspect then patch once",
        )


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
async def test_manual_recovery_advances_verifying_incident_epoch(
    tmp_path: Path,
) -> None:
    store = WatchdogStore(WatchdogPaths.for_run("run-1", root=tmp_path))
    port = FakeRecoveryPort()
    coordinator = RecoveryCoordinator(store=store, port=port)
    first = await coordinator.recover(confirmed_state(), observed_at=1)

    second = await coordinator.recover(first, observed_at=2, manual=True)

    assert second.incident is not None
    assert second.incident.state == IncidentState.VERIFYING
    assert second.incident.epoch == 2
    assert len(port.injected) == 2
    assert "user_requested_recovery" in store.paths.decisions.read_text()


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
async def test_recovery_escalates_once_per_strategy_then_hands_off(
    tmp_path: Path,
) -> None:
    store = WatchdogStore(WatchdogPaths.for_run("run-1", root=tmp_path))
    port = FakeRecoveryPort()
    advisor = FakeRecoveryAdvisor()
    coordinator = RecoveryCoordinator(store=store, port=port, advisor=advisor)
    state = confirmed_state()

    observed_strategies: list[RecoveryStrategy] = []
    for sequence in range(1, 7):
        state = await coordinator.recover(state, observed_at=float(sequence))
        incident = state.incident
        assert incident is not None
        assert incident.decision is not None
        observed_strategies.append(incident.decision.strategy)
        if incident.state == IncidentState.NEEDS_USER:
            break
        state = state.model_copy(
            update={
                "incident": incident.model_copy(
                    update={"state": IncidentState.CONFIRMED}
                )
            }
        )

    assert observed_strategies == [
        RecoveryStrategy.INJECT_CONTEXT,
        RecoveryStrategy.REWRITE_COMMAND,
        RecoveryStrategy.ALTERNATE_TOOL,
        RecoveryStrategy.RESTORE_CHECKPOINT,
        RecoveryStrategy.LLM_RECOVERY,
        RecoveryStrategy.ASK_USER,
    ]
    assert [request.strategy for request in advisor.requests] == [
        RecoveryStrategy.REWRITE_COMMAND,
        RecoveryStrategy.ALTERNATE_TOOL,
        RecoveryStrategy.LLM_RECOVERY,
    ]
    assert port.restores == 1
    assert port.user_handoffs == 1
    assert state.phase == RunPhase.WAITING_FOR_USER


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
