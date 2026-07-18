from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from vibe.core.watchdog.advice import RecoveryAdvice, RecoveryAdviceRequest
from vibe.core.watchdog.detectors import RepeatedCallDetector, TerminalDetector
from vibe.core.watchdog.evaluation import TiltEvaluation, TiltEvaluationRequest
from vibe.core.watchdog.incident import IncidentEngine
from vibe.core.watchdog.models import Incident, RecoveryDecision
from vibe.core.watchdog.paths import WatchdogPaths
from vibe.core.watchdog.recovery import RecoveryCoordinator
from vibe.core.watchdog.recovery_port import (
    CancelResult,
    IdleResult,
    QuiesceResult,
    RestoreResult,
)
from vibe.core.watchdog.repository import repository_fingerprint
from vibe.core.watchdog.snapshots import ConversationSnapshotStore
from vibe.core.watchdog.store import WatchdogStore
from vibe.core.watchdog.supervisor import ObserveOnlySupervisor

if TYPE_CHECKING:
    from vibe.core.agent_loop import AgentLoop


class AgentLoopRecoveryPort:
    def __init__(self, agent_loop: AgentLoop, paths: WatchdogPaths) -> None:
        self._agent_loop = agent_loop
        self._snapshots = ConversationSnapshotStore(paths.snapshots / "conversations")

    async def quiesce(self, incident: Incident) -> QuiesceResult:
        return QuiesceResult(succeeded=True, detail="context injection only")

    async def cancel_active(self, incident: Incident) -> CancelResult:
        return CancelResult(succeeded=False, detail="requires CLI approval adapter")

    async def wait_until_idle(self, incident: Incident) -> IdleResult:
        return IdleResult(succeeded=False, detail="not used for context injection")

    async def inject_context(self, content: str) -> None:
        await self._agent_loop.queue_user_context(content)

    async def restore_latest_snapshot(self, incident: Incident) -> RestoreResult:
        cutoff = min((item.observed_sequence for item in incident.evidence), default=0)
        snapshots = await self._snapshots.list()
        snapshot = next(
            (
                item
                for item in snapshots
                if item.session_id == self._agent_loop.session_id
                and item.messages
                and item.watchdog_state.last_applied_sequence < cutoff
            ),
            None,
        )
        if snapshot is None:
            return RestoreResult(succeeded=False, detail="no pre-incident snapshot")
        await self._agent_loop.queue_conversation_restore(snapshot.messages)
        return RestoreResult(succeeded=True, detail=snapshot.snapshot_id)

    async def request_user_input(self, content: str, incident: Incident) -> None:
        del incident
        await self._agent_loop.queue_user_context(content)

    async def continue_once(self, prompt: str, incident: Incident) -> None:
        raise RuntimeError("continuation requires a delivery-surface adapter")

    async def request_approval(self, decision: RecoveryDecision) -> bool:
        return False


class AgentLoopTiltEvaluator:
    def __init__(self, agent_loop: AgentLoop) -> None:
        self._agent_loop = agent_loop

    async def evaluate(self, request: TiltEvaluationRequest) -> TiltEvaluation:
        return await self._agent_loop.evaluate_watchdog_tilt(request)


class AgentLoopRecoveryAdvisor:
    def __init__(self, agent_loop: AgentLoop) -> None:
        self._agent_loop = agent_loop

    async def advise(self, request: RecoveryAdviceRequest) -> RecoveryAdvice:
        return await self._agent_loop.advise_watchdog_recovery(request)


@dataclass(frozen=True, slots=True)
class WatchdogRuntime:
    run_id: str
    paths: WatchdogPaths
    supervisor: ObserveOnlySupervisor


def attach_watchdog(
    agent_loop: AgentLoop,
    *,
    objective: str,
    cwd: Path | None = None,
    run_id: str | None = None,
) -> WatchdogRuntime:
    resolved_run_id = run_id or uuid4().hex
    paths = WatchdogPaths.for_run(resolved_run_id)
    store = WatchdogStore(paths)
    recovery = RecoveryCoordinator(
        store=store,
        port=AgentLoopRecoveryPort(agent_loop, paths),
        advisor=AgentLoopRecoveryAdvisor(agent_loop),
        objective=objective,
        available_tools=tuple(sorted(agent_loop.tool_manager.available_tools)),
    )
    root = cwd or Path.cwd()
    supervisor = ObserveOnlySupervisor(
        run_id=resolved_run_id,
        session_id=agent_loop.session_id,
        store=store,
        incident_engine=IncidentEngine((RepeatedCallDetector(), TerminalDetector())),
        recovery=recovery,
        tilt_evaluator=AgentLoopTiltEvaluator(agent_loop),
        repository_probe=lambda: repository_fingerprint(root),
    )
    agent_loop.set_event_observer(supervisor)
    return WatchdogRuntime(run_id=resolved_run_id, paths=paths, supervisor=supervisor)
