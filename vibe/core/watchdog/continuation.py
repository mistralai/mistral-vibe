from __future__ import annotations

import asyncio

from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.models import IncidentState, RunState
from vibe.core.watchdog.recovery_port import RecoveryPort
from vibe.core.watchdog.reducer import apply_event
from vibe.core.watchdog.store import WatchdogStore

_TERMINAL_INCIDENT_STATES = {
    IncidentState.NEEDS_USER,
    IncidentState.FAILED,
    IncidentState.CLOSED,
}


class ContinuationCoordinator:
    def __init__(self, *, store: WatchdogStore, port: RecoveryPort) -> None:
        self._store = store
        self._port = port
        self._lock = asyncio.Lock()
        self._started: set[tuple[str, int]] = set()

    async def continue_once(
        self, state: RunState, *, prompt: str, observed_at: float
    ) -> RunState:
        async with self._lock:
            incident = state.incident
            if incident is None or incident.state in _TERMINAL_INCIDENT_STATES:
                return state
            claim = (incident.incident_id, incident.epoch)
            if claim in self._started:
                return state
            idle = await self._port.wait_until_idle(incident)
            if not idle.succeeded:
                return state
            state = await self._persist_marker(
                state, EventKind.CONTINUATION_PENDING, observed_at
            )
            await self._port.continue_once(prompt, incident)
            state = await self._persist_marker(
                state, EventKind.CONTINUATION_STARTED, observed_at
            )
            self._started.add(claim)
            return state

    async def _persist_marker(
        self, state: RunState, kind: EventKind, observed_at: float
    ) -> RunState:
        event = WatchdogEvent(
            run_id=state.run_id,
            session_id=state.session_id,
            sequence=state.last_applied_sequence + 1,
            observed_at_monotonic=observed_at,
            kind=kind,
            epoch=state.epoch,
        )
        updated = apply_event(state, event)
        await self._store.persist(event, updated)
        return updated
