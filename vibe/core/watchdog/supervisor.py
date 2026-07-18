from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterable
import time

from vibe.core.types import BaseEvent
from vibe.core.watchdog.event_adapter import PendingWatchdogEvent, normalize_event
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.incident import IncidentEngine
from vibe.core.watchdog.models import RunState
from vibe.core.watchdog.queue import WatchdogEventQueue
from vibe.core.watchdog.reducer import apply_event
from vibe.core.watchdog.store import WatchdogStore


class ObserveOnlySupervisor:
    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        store: WatchdogStore,
        queue_capacity: int = 256,
        incident_engine: IncidentEngine | None = None,
    ) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.store = store
        self.state = RunState.new(run_id=run_id, session_id=session_id)
        self._incident_engine = incident_engine
        self._queue = WatchdogEventQueue(capacity=queue_capacity)
        self._worker: asyncio.Task[None] | None = None
        self._integrity_lost = False

    async def start(self) -> None:
        if self._worker is not None:
            raise RuntimeError("Watchdog supervisor already started")
        self._worker = asyncio.create_task(self._run())
        self._enqueue_boundary(EventKind.RUN_STARTED)

    def observe(self, event: BaseEvent) -> None:
        if pending := normalize_event(event):
            result = self._queue.put_nowait(pending)
            self._integrity_lost |= result.integrity_lost

    async def finish(self, outcome: EventKind = EventKind.RUN_FINISHED) -> None:
        self._enqueue_boundary(outcome)
        self._queue.close()
        if self._worker is not None:
            await asyncio.wait_for(self._worker, timeout=2)

    def _enqueue_boundary(self, kind: EventKind) -> None:
        result = self._queue.put_nowait(
            PendingWatchdogEvent(
                kind=kind,
                observed_at_monotonic=time.monotonic(),
                payload={},
                critical=True,
            )
        )
        self._integrity_lost |= result.integrity_lost

    async def _run(self) -> None:
        while pending := await self._queue.get():
            if self._integrity_lost:
                self._integrity_lost = False
                await self._apply_pending(
                    PendingWatchdogEvent(
                        kind=EventKind.OBSERVER_ANOMALY,
                        observed_at_monotonic=time.monotonic(),
                        payload={"reason": "critical_queue_overflow"},
                        critical=True,
                    )
                )
            await self._apply_pending(pending)

    async def _apply_pending(self, pending: PendingWatchdogEvent) -> None:
        event = WatchdogEvent(
            run_id=self.run_id,
            session_id=self.session_id,
            sequence=self.state.last_applied_sequence + 1,
            observed_at_monotonic=pending.observed_at_monotonic,
            kind=pending.kind,
            payload=pending.payload,
        )
        self.state = apply_event(self.state, event)
        await self.store.persist(event, self.state)
        if self._incident_engine is None:
            return
        for transition in self._incident_engine.observe(event, self.state):
            transition_event = WatchdogEvent(
                run_id=self.run_id,
                session_id=self.session_id,
                sequence=self.state.last_applied_sequence + 1,
                observed_at_monotonic=pending.observed_at_monotonic,
                kind=transition.kind,
                payload={"incident": transition.incident.model_dump(mode="json")},
            )
            self.state = apply_event(self.state, transition_event)
            await self.store.persist(transition_event, self.state)


async def observe_stream(
    events: AsyncIterable[BaseEvent], supervisor: ObserveOnlySupervisor
) -> AsyncGenerator[BaseEvent, None]:
    outcome = EventKind.RUN_FINISHED
    await supervisor.start()
    try:
        async for event in events:
            supervisor.observe(event)
            yield event
    except asyncio.CancelledError:
        outcome = EventKind.RUN_CANCELLED
        raise
    except Exception:
        outcome = EventKind.RUN_FAILED
        raise
    finally:
        await supervisor.finish(outcome)
