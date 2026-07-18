from __future__ import annotations

import asyncio

from vibe.core.watchdog.authorization import (
    AuthorizationVerdict,
    RecoveryAuthorizationPolicy,
)
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.models import (
    Incident,
    IncidentState,
    ObserverState,
    RecoveryDecision,
    RecoveryStrategy,
    RunState,
)
from vibe.core.watchdog.recovery_port import RecoveryPort
from vibe.core.watchdog.reducer import apply_event
from vibe.core.watchdog.store import WatchdogStore


class RecoveryCoordinator:
    def __init__(
        self,
        *,
        store: WatchdogStore,
        port: RecoveryPort,
        authorization: RecoveryAuthorizationPolicy | None = None,
    ) -> None:
        self._store = store
        self._port = port
        self._authorization = authorization or RecoveryAuthorizationPolicy()
        self._lock = asyncio.Lock()
        self._claimed: set[tuple[str, int]] = set()

    async def recover(self, state: RunState, *, observed_at: float) -> RunState:
        async with self._lock:
            incident = state.incident
            if incident is None or not self._eligible(state, incident):
                return state
            strategy = RecoveryStrategy.INJECT_CONTEXT
            decision = RecoveryDecision(
                strategy=strategy, reason_code="confirmed_repeated_incident"
            )
            if self._authorization.authorize(strategy) != AuthorizationVerdict.ALLOW:
                return state
            epoch = incident.epoch + 1
            claim = (incident.incident_id, epoch)
            if claim in self._claimed:
                return state
            self._claimed.add(claim)
            recovering = incident.model_copy(
                update={
                    "epoch": epoch,
                    "state": IncidentState.RECOVERING,
                    "decision": decision,
                }
            )
            state = await self._persist_transition(
                state,
                recovering,
                EventKind.RECOVERY_STARTED,
                observed_at,
                decision=decision,
            )
            try:
                await self._port.inject_context(_recovery_context(recovering))
            except Exception:
                failed = recovering.model_copy(update={"state": IncidentState.DEGRADED})
                await self._persist_transition(
                    state, failed, EventKind.RECOVERY_FAILED, observed_at
                )
                raise
            verifying = recovering.model_copy(update={"state": IncidentState.VERIFYING})
            return await self._persist_transition(
                state, verifying, EventKind.RECOVERY_FINISHED, observed_at
            )

    @staticmethod
    def _eligible(state: RunState, incident: Incident) -> bool:
        return (
            incident.state == IncidentState.CONFIRMED
            and state.observer_state == ObserverState.TRUSTED
        )

    async def _persist_transition(
        self,
        state: RunState,
        incident: Incident,
        kind: EventKind,
        observed_at: float,
        *,
        decision: RecoveryDecision | None = None,
    ) -> RunState:
        event = WatchdogEvent(
            run_id=state.run_id,
            session_id=state.session_id,
            sequence=state.last_applied_sequence + 1,
            observed_at_monotonic=observed_at,
            kind=kind,
            epoch=incident.epoch,
            payload={"incident": incident.model_dump(mode="json")},
        )
        updated = apply_event(state, event)
        await self._store.persist(event, updated, decision=decision)
        return updated


def _recovery_context(incident: Incident) -> str:
    evidence = incident.evidence[-1]
    return "\n".join((
        "[WATCHDOG RECOVERY]",
        f"incident={incident.incident_id}",
        f"epoch={incident.epoch}",
        f"detector={evidence.detector}",
        f"evidence={evidence.fingerprint}",
        "Do not repeat the same tool call with unchanged inputs and repository.",
        "Choose a materially different next action and verify its result.",
    ))
