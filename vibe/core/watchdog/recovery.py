from __future__ import annotations

import asyncio

from vibe.core.watchdog.advice import (
    RecoveryAdvice,
    RecoveryAdviceRequest,
    RecoveryAdvisor,
    validate_recovery_advice,
)
from vibe.core.watchdog.authorization import (
    AuthorizationVerdict,
    RecoveryAuthorizationPolicy,
)
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.handoff import RecoveryHandoff
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

RECOVERY_LADDER = (
    RecoveryStrategy.INJECT_CONTEXT,
    RecoveryStrategy.REWRITE_COMMAND,
    RecoveryStrategy.ALTERNATE_TOOL,
    RecoveryStrategy.RESTORE_CHECKPOINT,
    RecoveryStrategy.LLM_RECOVERY,
    RecoveryStrategy.ASK_USER,
)


class RecoveryCoordinator:
    def __init__(
        self,
        *,
        store: WatchdogStore,
        port: RecoveryPort,
        advisor: RecoveryAdvisor | None = None,
        authorization: RecoveryAuthorizationPolicy | None = None,
        objective: str = "Continue the current user task safely",
        available_tools: tuple[str, ...] = (),
    ) -> None:
        self._store = store
        self._port = port
        self._advisor = advisor
        self._authorization = authorization or RecoveryAuthorizationPolicy()
        self._objective = objective
        self._available_tools = available_tools
        self._lock = asyncio.Lock()
        self._claimed: set[tuple[str, int]] = set()

    async def recover(
        self,
        state: RunState,
        *,
        observed_at: float,
        manual: bool = False,
        tilt_score_authorized: bool = False,
    ) -> RunState:
        async with self._lock:
            incident = state.incident
            if incident is None or not self._eligible(
                state,
                incident,
                manual=manual,
                tilt_score_authorized=tilt_score_authorized,
            ):
                return state
            while True:
                epoch = incident.epoch + 1
                strategy = (
                    RecoveryStrategy.INJECT_CONTEXT
                    if manual
                    else _strategy_for_epoch(epoch)
                )
                claim = (incident.incident_id, epoch)
                if claim in self._claimed:
                    return state
                self._claimed.add(claim)
                decision = RecoveryDecision(
                    strategy=strategy,
                    reason_code=_reason_code(
                        strategy,
                        manual=manual,
                        tilt_score_authorized=tilt_score_authorized,
                    ),
                )
                verdict = self._authorization.authorize(strategy)
                if verdict == AuthorizationVerdict.DENY:
                    return state
                if (
                    verdict == AuthorizationVerdict.ASK
                    and not await self._port.request_approval(decision)
                ):
                    strategy = RecoveryStrategy.ASK_USER
                    decision = RecoveryDecision(
                        strategy=strategy, reason_code="recovery_requires_user_approval"
                    )
                if strategy == RecoveryStrategy.ASK_USER:
                    return await self._handoff_to_user(
                        state, incident, epoch, decision, observed_at
                    )
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
                    delivered = await self._execute_strategy(
                        state, recovering, strategy
                    )
                except Exception:
                    failed = recovering.model_copy(
                        update={"state": IncidentState.DEGRADED}
                    )
                    await self._persist_transition(
                        state, failed, EventKind.RECOVERY_FAILED, observed_at
                    )
                    raise
                if not delivered:
                    incident = recovering.model_copy(
                        update={"state": IncidentState.CONFIRMED}
                    )
                    state = await self._persist_transition(
                        state, incident, EventKind.RECOVERY_FAILED, observed_at
                    )
                    continue
                verifying = recovering.model_copy(
                    update={"state": IncidentState.VERIFYING}
                )
                return await self._persist_transition(
                    state, verifying, EventKind.RECOVERY_FINISHED, observed_at
                )

    async def _execute_strategy(  # noqa: PLR0911
        self, state: RunState, incident: Incident, strategy: RecoveryStrategy
    ) -> bool:
        if strategy == RecoveryStrategy.INJECT_CONTEXT:
            await self._port.inject_context(
                _recovery_context(state, incident, self._objective, (strategy,))
            )
            return True
        if strategy in {
            RecoveryStrategy.REWRITE_COMMAND,
            RecoveryStrategy.ALTERNATE_TOOL,
            RecoveryStrategy.LLM_RECOVERY,
        }:
            if self._advisor is None:
                return False
            request = _advice_request(
                incident, strategy, self._objective, self._available_tools
            )
            try:
                advice = await self._advisor.advise(request)
            except Exception:
                return False
            try:
                validate_recovery_advice(advice, request)
            except ValueError:
                return False
            await self._port.inject_context(_advice_context(advice))
            return True
        if strategy == RecoveryStrategy.RESTORE_CHECKPOINT:
            restored = await self._port.restore_latest_snapshot(incident)
            if not restored.succeeded:
                return False
            attempted = tuple(RECOVERY_LADDER[: incident.epoch])
            await self._port.inject_context(
                _recovery_context(state, incident, self._objective, attempted)
            )
            return True
        return False

    async def _handoff_to_user(
        self,
        state: RunState,
        incident: Incident,
        epoch: int,
        decision: RecoveryDecision,
        observed_at: float,
    ) -> RunState:
        needs_user = incident.model_copy(
            update={
                "epoch": epoch,
                "state": IncidentState.NEEDS_USER,
                "decision": decision,
            }
        )
        updated = await self._persist_transition(
            state,
            needs_user,
            EventKind.RECOVERY_STARTED,
            observed_at,
            decision=decision,
        )
        await self._port.request_user_input(
            "Watchcat exhausted automatic mitigation. Stop and ask the user for "
            "a specific next action.",
            needs_user,
        )
        return updated

    @staticmethod
    def _eligible(
        state: RunState,
        incident: Incident,
        *,
        manual: bool,
        tilt_score_authorized: bool,
    ) -> bool:
        eligible_states = {IncidentState.CONFIRMED}
        if manual:
            eligible_states |= {
                IncidentState.DEGRADED,
                IncidentState.VERIFYING,
                IncidentState.NEEDS_USER,
                IncidentState.FAILED,
            }
        if incident.state not in eligible_states:
            return False
        if state.observer_state == ObserverState.TRUSTED:
            return True
        return tilt_score_authorized and incident.state == IncidentState.CONFIRMED

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


def _strategy_for_epoch(epoch: int) -> RecoveryStrategy:
    return RECOVERY_LADDER[min(epoch - 1, len(RECOVERY_LADDER) - 1)]


def _reason_code(
    strategy: RecoveryStrategy, *, manual: bool, tilt_score_authorized: bool
) -> str:
    if manual:
        return "user_requested_recovery"
    if tilt_score_authorized:
        return "signal_score_threshold_met"
    return f"escalate_{strategy.value}"


def _advice_request(
    incident: Incident,
    strategy: RecoveryStrategy,
    objective: str,
    available_tools: tuple[str, ...],
) -> RecoveryAdviceRequest:
    evidence = tuple(item.fingerprint for item in incident.evidence[-4:])
    failed_tool = _last_string_fact(incident, "tool_name")
    failed_action_fingerprint = _last_string_fact(incident, "call")
    attempted = tuple(RECOVERY_LADDER[: max(incident.epoch - 1, 0)])
    return RecoveryAdviceRequest(
        strategy=strategy,
        objective=objective,
        detector=incident.owner or "unknown",
        evidence=evidence,
        attempted_strategies=attempted,
        available_tools=available_tools,
        failed_tool=failed_tool,
        failed_action_fingerprint=failed_action_fingerprint,
    )


def _last_string_fact(incident: Incident, key: str) -> str | None:
    for item in reversed(incident.evidence):
        value = item.facts.get(key)
        if isinstance(value, str):
            return value
    return None


def _advice_context(advice: RecoveryAdvice) -> str:
    return "\n".join([
        "[WATCHCAT MITIGATION ADVICE]",
        f"strategy={advice.strategy.value}",
        f"reason={advice.reason}",
        f"tool={advice.tool or 'unchanged'}",
        f"command={advice.command or 'choose a materially different action'}",
        "required=execute this approach once, then verify objective progress",
    ])


def _recovery_context(
    state: RunState,
    incident: Incident,
    objective: str,
    attempted_strategies: tuple[RecoveryStrategy, ...],
) -> str:
    evidence = incident.evidence[-1]
    repository = evidence.facts.get("repository")
    return RecoveryHandoff(
        objective=objective,
        incident_id=incident.incident_id,
        epoch=incident.epoch,
        detector=evidence.detector,
        evidence_fingerprint=evidence.fingerprint,
        phase=state.phase,
        repository_fingerprint=repository if isinstance(repository, str) else None,
        prohibited_action="repeat the same tool call with unchanged inputs",
        required_next_step="choose a materially different action and verify it",
        attempted_strategies=tuple(item.value for item in attempted_strategies),
        remaining_attempts=max(len(RECOVERY_LADDER) - incident.epoch, 0),
    ).render()
