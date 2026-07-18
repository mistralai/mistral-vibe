from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable
from dataclasses import replace
import time

from pydantic import JsonValue

from vibe.core.types import BaseEvent
from vibe.core.watchdog.evaluation import (
    TiltEvaluationRequest,
    TiltEvaluator,
    TiltScorePolicy,
)
from vibe.core.watchdog.event_adapter import PendingWatchdogEvent, normalize_event
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.fingerprint import fingerprint_call
from vibe.core.watchdog.incident import IncidentEngine
from vibe.core.watchdog.models import Incident, IncidentState, ObserverState, RunState
from vibe.core.watchdog.queue import WatchdogEventQueue
from vibe.core.watchdog.recovery import RecoveryCoordinator
from vibe.core.watchdog.reducer import apply_event
from vibe.core.watchdog.store import WatchdogStore
from vibe.core.watchdog.verification import (
    VerificationInput,
    VerificationStatus,
    VerifierRegistry,
)


class ObserveOnlySupervisor:
    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        store: WatchdogStore,
        queue_capacity: int = 256,
        incident_engine: IncidentEngine | None = None,
        recovery: RecoveryCoordinator | None = None,
        repository_probe: Callable[[], Awaitable[str | None]] | None = None,
        verifiers: VerifierRegistry | None = None,
        tilt_evaluator: TiltEvaluator | None = None,
        tilt_score_policy: TiltScorePolicy | None = None,
    ) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.store = store
        self.state = RunState.new(run_id=run_id, session_id=session_id)
        self._incident_engine = incident_engine
        self._recovery = recovery
        self._repository_probe = repository_probe
        self._verifiers = verifiers or VerifierRegistry()
        self._tilt_evaluator = tilt_evaluator
        self._tilt_score_policy = tilt_score_policy or TiltScorePolicy()
        self._tilt_evaluated_incidents: set[str] = set()
        self._queue_capacity = queue_capacity
        self._queue = WatchdogEventQueue(capacity=queue_capacity)
        self._worker: asyncio.Task[None] | None = None
        self._integrity_lost = False
        self._interventions_enabled = True

    @property
    def interventions_enabled(self) -> bool:
        return self._interventions_enabled

    async def pause(self) -> None:
        self._require_idle_control()
        self._interventions_enabled = False
        if self._incident_engine is not None:
            self._incident_engine.reset()
        incident = self.state.incident
        if incident is None or incident.state == IncidentState.CLOSED:
            return
        await self._persist_incident(
            EventKind.INCIDENT_CLOSED,
            incident.model_copy(update={"state": IncidentState.CLOSED}),
            time.monotonic(),
        )

    def resume(self) -> None:
        self._require_idle_control()
        if self._incident_engine is not None:
            self._incident_engine.reset()
        self._interventions_enabled = True

    async def request_recovery(self) -> bool:
        self._require_idle_control()
        if not self._interventions_enabled or self._recovery is None:
            return False
        previous_sequence = self.state.last_applied_sequence
        self.state = await self._recovery.recover(
            self.state, observed_at=time.monotonic(), manual=True
        )
        return self.state.last_applied_sequence > previous_sequence

    def _require_idle_control(self) -> None:
        if self._worker is not None and not self._worker.done():
            raise RuntimeError("Watchdog controls require an idle agent turn")

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            raise RuntimeError("Watchdog supervisor already started")
        self._queue = WatchdogEventQueue(capacity=self._queue_capacity)
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
            self._worker = None

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
        if pending.kind == EventKind.TOOL_FINISHED and self._repository_probe:
            repository = await self._repository_probe()
            if repository is not None:
                pending = replace(
                    pending,
                    payload={**pending.payload, "repository_fingerprint": repository},
                )
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
        if self._interventions_enabled:
            await self._verify_next_action(event)
        if self._incident_engine is None or not self._interventions_enabled:
            return
        for transition in self._incident_engine.observe(event, self.state):
            await self._persist_incident(
                transition.kind, transition.incident, pending.observed_at_monotonic
            )
            if self._recovery is not None:
                if self.state.observer_state == ObserverState.TILT:
                    await self._evaluate_tilt(pending.observed_at_monotonic)
                else:
                    self.state = await self._recovery.recover(
                        self.state, observed_at=pending.observed_at_monotonic
                    )

    async def _evaluate_tilt(self, observed_at: float) -> None:
        incident = self.state.incident
        if (
            self._tilt_evaluator is None
            or incident is None
            or incident.state != IncidentState.CONFIRMED
            or incident.incident_id in self._tilt_evaluated_incidents
        ):
            return
        self._tilt_evaluated_incidents.add(incident.incident_id)
        repeat_count = max(
            (
                count
                for evidence in incident.evidence
                if isinstance((count := evidence.facts.get("repeat_count")), int)
            ),
            default=0,
        )
        request = TiltEvaluationRequest(
            detector=incident.owner or "unknown",
            phase=self.state.phase,
            evidence_count=len(incident.evidence),
            exact_repeat_count=repeat_count,
        )
        try:
            evaluation = await self._tilt_evaluator.evaluate(request)
        except Exception:
            await self._persist_tilt_score(
                EventKind.TILT_EVALUATION_FAILED, observed_at, payload={}
            )
            return
        authorized = self._tilt_score_policy.authorizes_context_injection(evaluation)
        await self._persist_tilt_score(
            EventKind.TILT_EVALUATED,
            observed_at,
            payload={
                "score": evaluation.score,
                "threshold": self._tilt_score_policy.recovery_threshold,
                "context_injection_authorized": authorized,
            },
        )
        if authorized and self._recovery is not None:
            self.state = await self._recovery.recover(
                self.state, observed_at=observed_at, tilt_score_authorized=True
            )

    async def _persist_tilt_score(
        self, kind: EventKind, observed_at: float, *, payload: dict[str, JsonValue]
    ) -> None:
        event = WatchdogEvent(
            run_id=self.run_id,
            session_id=self.session_id,
            sequence=self.state.last_applied_sequence + 1,
            observed_at_monotonic=observed_at,
            kind=kind,
            payload=payload,
        )
        self.state = apply_event(self.state, event)
        await self.store.persist(event, self.state)

    async def _persist_incident(
        self, kind: EventKind, incident: Incident, observed_at: float
    ) -> None:
        transition_event = WatchdogEvent(
            run_id=self.run_id,
            session_id=self.session_id,
            sequence=self.state.last_applied_sequence + 1,
            observed_at_monotonic=observed_at,
            kind=kind,
            payload={"incident": incident.model_dump(mode="json")},
        )
        self.state = apply_event(self.state, transition_event)
        await self.store.persist(transition_event, self.state)

    async def _verify_next_action(self, event: WatchdogEvent) -> None:
        incident = self.state.incident
        if (
            event.kind != EventKind.TOOL_STARTED
            or incident is None
            or incident.state != IncidentState.VERIFYING
            or incident.decision is None
            or not incident.evidence
        ):
            return
        tool_name = event.payload.get("tool_name")
        arguments = event.payload.get("arguments")
        evidence = incident.evidence[-1]
        previous = evidence.facts.get("call")
        if not isinstance(previous, str):
            previous = evidence.fingerprint
        if not isinstance(tool_name, str) or not isinstance(arguments, dict):
            return
        result = self._verifiers.verify(
            incident.decision.strategy,
            VerificationInput(
                previous_action_fingerprint=previous,
                next_action_fingerprint=fingerprint_call(tool_name, arguments),
            ),
        )
        await self._persist_verification(
            EventKind.VERIFICATION_STARTED,
            incident,
            event.observed_at_monotonic,
            status="started",
            reason="next_action_observed",
        )
        next_state = (
            IncidentState.CLOSED
            if result.status == VerificationStatus.PASSED
            else IncidentState.NEEDS_USER
            if result.status == VerificationStatus.NEEDS_USER
            else IncidentState.VERIFYING
        )
        await self._persist_verification(
            EventKind.VERIFICATION_FINISHED,
            incident.model_copy(update={"state": next_state}),
            event.observed_at_monotonic,
            status=result.status.value,
            reason=result.reason,
        )

    async def _persist_verification(
        self,
        kind: EventKind,
        incident: Incident,
        observed_at: float,
        *,
        status: str,
        reason: str,
    ) -> None:
        verification = WatchdogEvent(
            run_id=self.run_id,
            session_id=self.session_id,
            sequence=self.state.last_applied_sequence + 1,
            observed_at_monotonic=observed_at,
            kind=kind,
            epoch=incident.epoch,
            payload={
                "incident": incident.model_dump(mode="json"),
                "verification": {"status": status, "reason": reason},
            },
        )
        self.state = apply_event(self.state, verification)
        await self.store.persist(verification, self.state)


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
