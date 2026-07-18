from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from vibe.core.watchdog.detectors import RepeatedCallDetector, TerminalDetector
from vibe.core.watchdog.incident import IncidentEngine
from vibe.core.watchdog.models import Incident, RecoveryDecision
from vibe.core.watchdog.paths import WatchdogPaths
from vibe.core.watchdog.recovery import RecoveryCoordinator
from vibe.core.watchdog.recovery_port import CancelResult, IdleResult, QuiesceResult
from vibe.core.watchdog.repository import repository_fingerprint
from vibe.core.watchdog.store import WatchdogStore
from vibe.core.watchdog.supervisor import ObserveOnlySupervisor

if TYPE_CHECKING:
    from vibe.core.agent_loop import AgentLoop


class AgentLoopRecoveryPort:
    def __init__(self, agent_loop: AgentLoop) -> None:
        self._agent_loop = agent_loop

    async def quiesce(self, incident: Incident) -> QuiesceResult:
        return QuiesceResult(succeeded=True, detail="context injection only")

    async def cancel_active(self, incident: Incident) -> CancelResult:
        return CancelResult(succeeded=False, detail="requires CLI approval adapter")

    async def wait_until_idle(self, incident: Incident) -> IdleResult:
        return IdleResult(succeeded=False, detail="not used for context injection")

    async def inject_context(self, content: str) -> None:
        await self._agent_loop.inject_user_context(content)

    async def continue_once(self, prompt: str, incident: Incident) -> None:
        raise RuntimeError("continuation requires a delivery-surface adapter")

    async def request_approval(self, decision: RecoveryDecision) -> bool:
        return False


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
        store=store, port=AgentLoopRecoveryPort(agent_loop), objective=objective
    )
    root = cwd or Path.cwd()
    supervisor = ObserveOnlySupervisor(
        run_id=resolved_run_id,
        session_id=agent_loop.session_id,
        store=store,
        incident_engine=IncidentEngine((RepeatedCallDetector(), TerminalDetector())),
        recovery=recovery,
        repository_probe=lambda: repository_fingerprint(root),
    )
    agent_loop.set_event_observer(supervisor)
    return WatchdogRuntime(run_id=resolved_run_id, paths=paths, supervisor=supervisor)
