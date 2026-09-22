"""Durable parent-side orchestration for Core stateful-subagent Actions."""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Literal, Protocol, cast

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import JsonValue, ValidationError

from mistralai_vibe_local_harness.protocol import (
    RustEvent,
    RustFailTurnEvent,
    RustHarnessNotification,
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe._storage import RuntimeStateV3
from mistralai_vibe_local_harness.vibe._observability import (
    add_subagent_active_turns,
    add_subagent_recovery_failure,
    record_subagent_notification_lag,
    record_subagent_operation,
)
from mistralai_vibe_local_harness.vibe._subagents._host import (
    ChildSessionHandle,
    ChildSessionHost,
    ResolvedChildSessionBinding,
)
from mistralai_vibe_local_harness.vibe._subagents._models import (
    AbandoningReceipt,
    ActiveSubagentReceiptState,
    ChildCommandAcceptedReceipt,
    ChildGenerationRef,
    ChildLifecycleState,
    ChildSessionRecord,
    ChildTombstone,
    ChildTurnOutcome,
    CloseCleanupStartedReceipt,
    CloseIdleTarget,
    CloseNotificationCommittedReceipt,
    CloseOutcomeRecordedReceipt,
    CloseRunningTarget,
    CompletedChildTurnOutcome,
    CreationCleanupPendingChild,
    CreationFailedChild,
    DeletingIdleChild,
    DeletingRunningChild,
    FailedChildTurnOutcome,
    FailedReceipt,
    IdleChild,
    InterruptedChildTurnOutcome,
    InterruptTarget,
    PreparedReceipt,
    ReservedChild,
    ResolvedSubagentPolicyCeiling,
    RunningChild,
    SendIntentTarget,
    SendStartTarget,
    SendSteerTarget,
    SessionIdentity,
    SpawnTarget,
    SubagentFailure,
    SubagentLimits,
    SubagentOperationReceipt,
    SubagentOperationTarget,
    SubagentRuntimeState,
    SubagentSessionIdentity,
    SucceededReceipt,
    TurnFailedChild,
    WaitTarget,
)
from mistralai_vibe_local_harness.vibe._subagents._notifications import pending_notification
from mistralai_vibe_local_harness.vibe._subagents._operations import (
    AgentInput,
    ListInput,
    MessageInput,
    SUBAGENT_TOOL_NAMES,
    SpawnInput,
    WaitInput,
    child_is_sendable,
    child_session_id,
    child_unavailable_failure,
    error_output,
    failure,
    listed_agent,
    live_child_count,
    request_digest,
    running_child_count,
    success_output,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class ParentRuntime(Protocol):
    @property
    def session_id(self) -> str: ...

    @property
    def runtime_state(self) -> RuntimeStateV3: ...

    @property
    def identity(self) -> SessionIdentity: ...

    async def update_runtime_state(
        self, update: Callable[[RuntimeStateV3], RuntimeStateV3]
    ) -> None: ...

    async def deliver_notification(self, notification: RustHarnessNotification) -> object: ...

    def configure_subagent_controller(self, controller: "SubagentController") -> None: ...


class SubagentController:
    """Own the parent graph, receipts, child operations, and notifications."""

    def __init__(
        self,
        *,
        runtime: ParentRuntime,
        child_host: ChildSessionHost,
        bindings: Mapping[str | None, ResolvedChildSessionBinding],
        policy_ceiling: ResolvedSubagentPolicyCeiling,
        limits: SubagentLimits,
    ) -> None:
        self._runtime = runtime
        self._child_host = child_host
        self._bindings = dict(bindings)
        self._policy_ceiling = policy_ceiling
        self._limits = limits
        self._graph_lock = asyncio.Lock()
        self._notification_lock = asyncio.Lock()
        self._turn_admission_lock = asyncio.Lock()
        self._name_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._watchers: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._closing = False

    @classmethod
    async def open(
        cls,
        *,
        runtime: ParentRuntime,
        child_host: ChildSessionHost,
        bindings: Mapping[str | None, ResolvedChildSessionBinding],
        policy_ceiling: ResolvedSubagentPolicyCeiling,
        limits: SubagentLimits | None = None,
    ) -> "SubagentController":
        def initialize_subagents(state: RuntimeStateV3) -> RuntimeStateV3:
            if isinstance(state.identity, SubagentSessionIdentity):
                raise ValueError("a depth-one child cannot install a subagent controller")
            if state.subagents is None:
                return state.model_copy(
                    update={"subagents": SubagentRuntimeState(policy_ceiling=policy_ceiling)},
                    deep=True,
                )
            drifted = not _same_enforced_policy(state.subagents.policy_ceiling, policy_ceiling)
            unbindable = _unbindable_recoverable_children(state.subagents, bindings)
            if drifted or unbindable:
                # ``open`` is always a cold boundary: nothing here is live.
                subagents = state.subagents
                if drifted and not _policy_can_be_rebound(subagents):
                    # Enforced policy changed: nothing was admitted under it, so
                    # settle every child and adopt the new ceiling for future work.
                    subagents = _settle_children_for_restore(
                        subagents, frozenset(subagents.children), observed_at=_now_milliseconds()
                    )
                elif unbindable:
                    # Same policy, but some children can no longer be bound; settle
                    # just those and leave bindable siblings to recover.
                    subagents = _settle_children_for_restore(
                        subagents, unbindable, observed_at=_now_milliseconds()
                    )
                return state.model_copy(
                    update={
                        "subagents": subagents.model_copy(
                            update={"policy_ceiling": policy_ceiling}, deep=True
                        )
                    },
                    deep=True,
                )
            return state

        await runtime.update_runtime_state(initialize_subagents)
        controller = cls(
            runtime=runtime,
            child_host=child_host,
            bindings=bindings,
            policy_ceiling=policy_ceiling,
            limits=limits or SubagentLimits(),
        )
        # A recovered notification can immediately ask the model for more child
        # work, so install the controller before replaying any queued delivery.
        runtime.configure_subagent_controller(controller)
        await controller._preflight_children()
        await controller.deliver_pending_notifications()
        controller.start_watchers()
        return controller

    async def rebind_agent_types(
        self,
        bindings: Mapping[str | None, ResolvedChildSessionBinding],
        policy_ceiling: ResolvedSubagentPolicyCeiling,
    ) -> None:
        # The ceiling is durable state every live child was admitted under.
        # A plugin re-pin must not silently change it, so a drift there is
        # refused; ``reconfigure_subagents`` updates it explicitly via
        # ``update_policy_ceiling`` instead.
        if not _same_enforced_policy(self._policy_ceiling, policy_ceiling):
            raise ValueError("cannot rebind subagent agent types under a different policy ceiling")
        async with self._graph_lock:
            self._bindings = dict(bindings)

    async def update_policy_ceiling(
        self,
        policy_ceiling: ResolvedSubagentPolicyCeiling,
    ) -> None:
        """Replace the enforced policy ceiling.

        Used when the parent's adapter config changes mid-session (e.g. an
        agent mode switch): the ceiling's ``approval_bypass`` follows the
        parent's, and the new bindings (already rebound by the caller) carry
        the matching ``bypass_approval`` and ``tool_modes``.
        """
        if self._policy_ceiling == policy_ceiling:
            return
        self._policy_ceiling = policy_ceiling
        async with self._graph_lock:
            state = self._subagents()
            state = state.model_copy(update={"policy_ceiling": policy_ceiling}, deep=True)
            await self._commit(state)

    async def reconfigure_children(self) -> None:
        """Push the live approval policy into every existing child session.

        Called when the parent's adapter config changes mid-session (e.g. an
        agent mode switch): the child's ``bypass_approval`` and ``tool_modes``
        were frozen at spawn time from the binding, and a mode switch on the
        parent must propagate so the child stops (or starts) prompting.

        The child record's ``template_digest`` and ``policy_ceiling_digest``
        are updated to match the new binding so ``_binding`` does not reject
        the child on its next interaction.

        Every existing child is reconfigured, not just running ones: idle,
        reserved, and turn-failed children also need their stored digests
        updated so they remain reusable after a mode switch. The child host's
        ``reconfigure_child`` returns ``False`` when it cannot update the
        child (e.g. the session was evicted from memory and storage is
        unavailable); in that case the parent record is left untouched so the
        child's persisted metadata still matches on restore.
        """
        async with self._graph_lock:
            state = self._subagents()
            changed = False
            for name, child in list(state.children.items()):
                binding = self._bindings.get(child.agent_type)
                if binding is None:
                    continue
                reconfigured = await self._child_host.reconfigure_child(
                    ChildSessionHandle(session_id=child.child_session_id),
                    binding,
                )
                if not reconfigured:
                    continue
                if (
                    child.template_digest != binding.template_digest
                    or child.policy_ceiling_digest != binding.policy_ceiling_digest
                ):
                    state.children[name] = child.model_copy(
                        update={
                            "template_digest": binding.template_digest,
                            "policy_ceiling_digest": binding.policy_ceiling_digest,
                        },
                        deep=True,
                    )
                    changed = True
            if changed:
                await self._commit(state)

    def handles(self, action: RustRuntimeBuiltinToolCallAction) -> bool:
        return action.call.name in SUBAGENT_TOOL_NAMES

    async def reconcile_actions(self, pending_action_ids: frozenset[str]) -> None:
        """Prune consumed receipts and finish effects abandoned by Core."""

        abandoning: list[tuple[int, str]] = []
        changed = False
        async with self._graph_lock:
            state = self._subagents()
            for action_id, receipt in tuple(state.operation_receipts.items()):
                if action_id in pending_action_ids:
                    continue
                if isinstance(receipt.state, SucceededReceipt | FailedReceipt):
                    del state.operation_receipts[action_id]
                    changed = True
                    continue
                if not isinstance(receipt.state, AbandoningReceipt):
                    receipt.state = AbandoningReceipt(previous=receipt.state)
                    changed = True
                abandoning.append((receipt.admission_sequence, action_id))
            if changed:
                await self._commit(state)
        for _sequence, action_id in sorted(abandoning):
            await self._finish_abandonment(action_id)

    async def execute(
        self, action: RustRuntimeBuiltinToolCallAction, *, recovering: bool
    ) -> RustEvent:
        operation = _operation_name(action.call.name)
        started_at = time.perf_counter()
        with tracer.start_as_current_span("subagent_operation") as span:
            span.set_attribute("mistral_ai.vibe_harness.subagent.operation", operation)
            span.set_attribute("mistral_ai.vibe_harness.recovering", recovering)
            try:
                event = await self._execute(action, recovering=recovering)
            except BaseException as exc:
                elapsed_s = time.perf_counter() - started_at
                failure_code = type(exc).__name__
                record_subagent_operation(
                    elapsed_s,
                    operation=operation,
                    outcome="failure",
                    recovering=recovering,
                )
                span.set_attribute("mistral_ai.vibe_harness.outcome", "failure")
                span.set_attribute("error.type", failure_code)
                logger.warning(
                    "Unified subagent operation failed unexpectedly",
                    extra={
                        "harness_backend": "unified",
                        "subagent_operation": operation,
                        "subagent_outcome": "failure",
                        "subagent_recovering": recovering,
                        "failure_code": failure_code,
                        "duration_ms": elapsed_s * 1000,
                    },
                    exc_info=exc,
                )
                raise
            outcome, failure_code = _event_outcome(event, operation=operation)
            elapsed_s = time.perf_counter() - started_at
            record_subagent_operation(
                elapsed_s,
                operation=operation,
                outcome=outcome,
                recovering=recovering,
            )
            span.set_attribute("mistral_ai.vibe_harness.outcome", outcome)
            if failure_code is not None:
                span.set_attribute("error.type", failure_code)
                span.set_status(Status(StatusCode.ERROR))
            if isinstance(event, RustFailTurnEvent):
                add_subagent_recovery_failure(
                    failure_code=event.error.code,
                    phase="action_reconciliation",
                )
            logger.info(
                "Unified subagent operation finished",
                extra={
                    "harness_backend": "unified",
                    "subagent_operation": operation,
                    "subagent_outcome": outcome,
                    "subagent_recovering": recovering,
                    "failure_code": failure_code,
                    "duration_ms": elapsed_s * 1000,
                },
            )
            return event

    async def _execute(
        self, action: RustRuntimeBuiltinToolCallAction, *, recovering: bool
    ) -> RustEvent:
        try:
            digest = request_digest(action)
            receipt = self._subagents().operation_receipts.get(action.action_id)
            if receipt is not None and receipt.request_digest != digest:
                if recovering:
                    return _unsafe_recovery_event(
                        action,
                        "subagent_recovery_receipt_conflict",
                        "Pending subagent Action conflicts with its durable parent receipt",
                    )
                return _result_event(
                    action,
                    error_output(
                        failure(
                            "subagent_operation_conflict",
                            "Subagent Action ID was reused with different arguments",
                            retryable=False,
                        )
                    ),
                )
            if receipt is not None and isinstance(receipt.state, AbandoningReceipt):
                return _unsafe_recovery_event(
                    action,
                    "subagent_recovery_abandonment_conflict",
                    "Pending subagent Action is already being abandoned",
                )
            output, annotations = await self._dispatch(action, receipt)
            return _result_event(action, output, annotations)
        except ValidationError as exc:
            return _result_event(
                action,
                error_output(failure("subagent_invalid_arguments", str(exc), retryable=False)),
            )
        except ExpectedSubagentError as exc:
            return _result_event(action, error_output(exc.failure))
        except UnsafeSubagentRecovery as exc:
            return _unsafe_recovery_event(action, exc.code, str(exc))

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        watchers = list(self._watchers.values())
        for watcher in watchers:
            watcher.cancel()
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)
        self._watchers.clear()
        children = sorted(
            (
                child
                for child in self._subagents().children.values()
                if not isinstance(
                    child.state,
                    CreationFailedChild | CreationCleanupPendingChild | ChildTombstone,
                )
            ),
            key=lambda child: child.child_session_id,
        )
        for child in children:
            await self._child_host.unload_child(
                ChildSessionHandle(session_id=child.child_session_id)
            )

    async def _finish_abandonment(self, action_id: str) -> None:
        async with self._graph_lock:
            receipt = self._subagents().operation_receipts.get(action_id)
            if receipt is None:
                return
            if not isinstance(receipt.state, AbandoningReceipt):
                raise RuntimeError("abandoned receipt lost its durable abandonment marker")
            previous = receipt.state.previous
            agent_name = receipt.agent_name

        if isinstance(previous, SucceededReceipt | FailedReceipt | PreparedReceipt) and isinstance(
            previous.target, WaitTarget
        ):
            await self._remove_receipt(action_id)
            return
        if isinstance(previous, SucceededReceipt | FailedReceipt):
            await self._remove_receipt(action_id)
            return
        if isinstance(previous.target, CloseIdleTarget | CloseRunningTarget):
            await self._stop(action_id, AgentInput(agentName=agent_name), receipt)
            await self._remove_receipt(action_id)
            return
        if isinstance(previous.target, SpawnTarget | SendIntentTarget):
            async with self._turn_admission_lock:
                await self._finish_abandoned_child_command(action_id, agent_name, previous)
            return
        if isinstance(previous.target, SendStartTarget | SendSteerTarget | InterruptTarget):
            await self._finish_abandoned_child_command(action_id, agent_name, previous)
            return
        raise RuntimeError("unsupported abandoned subagent receipt")

    async def _finish_abandoned_child_command(
        self,
        action_id: str,
        agent_name: str,
        previous: ActiveSubagentReceiptState,
    ) -> None:
        child = self._require_child(agent_name)
        target = previous.target
        if isinstance(previous, PreparedReceipt):
            admission = await self._child_host.child_command_admission(
                child.child_session_id,
                operation_key=action_id,
            )
            if admission is None:
                if isinstance(target, SpawnTarget):
                    await self._cleanup_abandoned_spawn(action_id, agent_name, target)
                else:
                    await self._remove_receipt(action_id)
                return
            target = admission.target
            if admission.turn_id != target.command.turn_id:
                raise UnsafeSubagentRecovery(
                    "subagent_recovery_unsafe",
                    "Abandoned child command receipt has a conflicting Turn identity",
                )
            if isinstance(previous.target, SpawnTarget) and target != previous.target:
                raise UnsafeSubagentRecovery(
                    "subagent_recovery_unsafe",
                    "Abandoned spawn command receipt changed its target",
                )
            if isinstance(previous.target, SendIntentTarget) and not isinstance(
                target, SendStartTarget | SendSteerTarget
            ):
                raise UnsafeSubagentRecovery(
                    "subagent_recovery_unsafe",
                    "Abandoned send command receipt has an invalid target",
                )
            async with self._graph_lock:
                state = self._subagents()
                current = state.operation_receipts[action_id]
                if not isinstance(current.state, AbandoningReceipt):
                    raise RuntimeError("abandoned command receipt changed phase unexpectedly")
                current.state = AbandoningReceipt(
                    previous=ChildCommandAcceptedReceipt(target=target)
                )
                if isinstance(target, SpawnTarget | SendStartTarget | SendSteerTarget):
                    state.children[agent_name].state = RunningChild(active=target.command)
                await self._commit(state)
            previous = ChildCommandAcceptedReceipt(target=target)
        if not isinstance(previous, ChildCommandAcceptedReceipt):
            raise UnsafeSubagentRecovery(
                "subagent_recovery_unsafe",
                "Abandoned child command has an invalid durable phase",
            )
        handle = await self._open_child(child, self._binding(child), require_existing=True)
        await self._child_host.acknowledge_child_command(handle, operation_key=action_id)
        if isinstance(previous.target, SpawnTarget | SendStartTarget | SendSteerTarget):
            self._ensure_watcher(agent_name, previous.target.command)
        await self._remove_receipt(action_id)

    async def _cleanup_abandoned_spawn(
        self,
        action_id: str,
        agent_name: str,
        target: SpawnTarget,
    ) -> None:
        abandoned = failure(
            "subagent_action_abandoned",
            "Subagent spawn was abandoned before child Turn admission",
            retryable=False,
        )
        async with self._graph_lock:
            state = self._subagents()
            state.children[agent_name].state = CreationCleanupPendingChild(failure=abandoned)
            await self._commit(state)
        try:
            await self._child_host.delete_child(target.child_session_id)
        except Exception:
            return
        async with self._graph_lock:
            state = self._subagents()
            state.children[agent_name].state = CreationFailedChild(failure=abandoned)
            state.operation_receipts.pop(action_id, None)
            await self._commit(state)

    async def _remove_receipt(self, action_id: str) -> None:
        async with self._graph_lock:
            state = self._subagents()
            if state.operation_receipts.pop(action_id, None) is not None:
                await self._commit(state)

    def start_watchers(self) -> None:
        for name, child in self._subagents().children.items():
            if isinstance(child.state, RunningChild):
                self._ensure_watcher(name, child.state.active)

    async def deliver_pending_notifications(self) -> None:
        async with self._notification_lock:
            while self._subagents().notifications.pending:
                async with self._graph_lock:
                    before = self._subagents()
                    pending = before.notifications.pending[0]
                    outcome = _pending_notification_outcome(
                        before, pending.agent_name, pending.generation
                    )
                await self._runtime.deliver_notification(pending.notification)
                async with self._graph_lock:
                    state = self._subagents()
                    current = state.notifications.pending[0]
                    if current != pending:
                        raise RuntimeError("child notification queue changed during delivery")
                    state.notifications.pending.pop(0)
                    state.notifications.last_committed_sequence = pending.sequence
                    child = state.children[pending.agent_name]
                    child.last_notified_generation = max(
                        child.last_notified_generation, pending.generation
                    )
                    close_receipt = _close_receipt_for_generation(
                        state, pending.agent_name, pending.generation
                    )
                    if close_receipt is not None and isinstance(
                        close_receipt.state, CloseOutcomeRecordedReceipt
                    ):
                        if not isinstance(child.state, DeletingRunningChild):
                            raise RuntimeError(
                                "close outcome notification has no deleting-running child"
                            )
                        child.state = DeletingIdleChild()
                        close_receipt.state = CloseNotificationCommittedReceipt(
                            target=close_receipt.state.target,
                            notification_id=pending.notification.id,
                        )
                    await self._commit(state)
                record_subagent_notification_lag(
                    max(0, _now_milliseconds() - outcome.completed_at_unix_ms) / 1000,
                    outcome=outcome.type,
                )

    async def _dispatch(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        receipt: SubagentOperationReceipt | None,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        match action.call.name:
            case "subagent.list":
                ListInput.model_validate(action.call.arguments)
                return await self._list(), {}
            case "subagent.spawn":
                request = SpawnInput.model_validate(action.call.arguments)
                return await self._spawn(action, request, receipt)
            case "subagent.wait":
                request = WaitInput.model_validate(action.call.arguments)
                return await self._wait(action, request, receipt), {}
            case "subagent.send_message":
                request = MessageInput.model_validate(action.call.arguments)
                return await self._send(action, request, receipt), {}
            case "subagent.interrupt":
                request = AgentInput.model_validate(action.call.arguments)
                return await self._interrupt(action, request, receipt), {}
            case "subagent.stop":
                request = AgentInput.model_validate(action.call.arguments)
                return await self._stop(action, request, receipt), {}
            case _:
                raise TypeError(f"unsupported subagent action: {action.call.name}")

    async def _list(self) -> dict[str, JsonValue]:
        async with self._graph_lock:
            agents: list[JsonValue] = []
            for child in self._subagents().children.values():
                match child.state:
                    case ReservedChild() | RunningChild():
                        agents.append(listed_agent(child, "running"))
                    case IdleChild():
                        agents.append(listed_agent(child, "idle"))
                    case ChildTombstone():
                        agents.append(listed_agent(child, "stopped"))
                    case (
                        CreationFailedChild(failure=stored_failure)
                        | CreationCleanupPendingChild(failure=stored_failure)
                    ):
                        agents.append(listed_agent(child, "failed", stored_failure.message))
                    case TurnFailedChild(outcome=outcome):
                        agents.append(listed_agent(child, "failed", outcome.failure.message))
                    case DeletingRunningChild() | DeletingIdleChild():
                        agents.append(listed_agent(child, "failed", "Subagent is closing"))
            return {"agents": agents}

    async def _spawn(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        request: SpawnInput,
        receipt: SubagentOperationReceipt | None,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        binding = self._bindings.get(request.agent_type)
        if binding is None:
            return error_output(
                failure("subagent_type_not_found", "Unknown subagent type", retryable=False)
            ), {}
        async with self._name_locks[request.agent_name], self._turn_admission_lock:
            if receipt is None:
                async with self._graph_lock:
                    state = self._subagents()
                    if request.agent_name in state.children:
                        return error_output(
                            failure(
                                "subagent_name_conflict",
                                f"Subagent name is already reserved: {request.agent_name}",
                                retryable=False,
                            )
                        ), {}
                    if len(state.children) >= self._limits.max_lifetime_names:
                        return error_output(
                            failure(
                                "subagent_name_limit",
                                "Subagent lifetime name limit reached",
                                retryable=False,
                            )
                        ), {}
                    if live_child_count(state) >= self._limits.max_children:
                        return error_output(
                            failure(
                                "subagent_child_limit",
                                "Subagent child limit reached",
                                retryable=True,
                            )
                        ), {}
                    if running_child_count(state) >= self._limits.max_concurrent_turns:
                        return error_output(
                            failure(
                                "subagent_concurrency_limit",
                                "Subagent concurrent Turn limit reached",
                                retryable=True,
                            )
                        ), {}
                    child_id = child_session_id(self._runtime.session_id, action.action_id)
                    command = ChildGenerationRef(
                        generation=1,
                        turn_id=f"{child_id}:turn:1",
                    )
                    target = SpawnTarget(
                        child_session_id=child_id,
                        command=command,
                        child_command_id=f"{action.action_id}:start",
                    )
                    state.children[request.agent_name] = ChildSessionRecord(
                        agent_name=request.agent_name,
                        agent_type=request.agent_type,
                        child_session_id=child_id,
                        spawn_action_id=action.action_id,
                        spawn_request_digest=request_digest(action),
                        template_digest=binding.template_digest,
                        policy_ceiling_digest=binding.policy_ceiling_digest,
                        state=ReservedChild(),
                    )
                    _add_receipt(state, action, request.agent_name, target)
                    await self._commit(state)
                receipt_state: ActiveSubagentReceiptState = PreparedReceipt(target=target)
            else:
                receipt_state = _active_receipt(receipt)
            output = await self._resume_spawn(action, request, binding, receipt_state)
            annotations = (
                self._spawn_annotations(request.agent_name) if output["type"] == "success" else {}
            )
            return output, annotations

    async def _resume_spawn(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        request: SpawnInput,
        binding: ResolvedChildSessionBinding,
        receipt_state: ActiveSubagentReceiptState,
    ) -> dict[str, JsonValue]:
        replay = _terminal_receipt_output(receipt_state)
        if replay is not None:
            return replay
        if not isinstance(receipt_state, PreparedReceipt | ChildCommandAcceptedReceipt):
            raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Invalid spawn receipt phase")
        target = receipt_state.target
        if not isinstance(target, SpawnTarget):
            raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Spawn receipt target changed")
        record = self._require_child(request.agent_name)
        child = await self._open_child(record, binding)
        if isinstance(receipt_state, PreparedReceipt):
            try:
                admission = await self._child_host.start_child_turn(
                    child,
                    message=request.message,
                    target=target,
                    operation_key=action.action_id,
                )
            except Exception as exc:
                return await self._fail_spawn(action.action_id, request.agent_name, target, exc)
            if admission.target != target or admission.turn_id != target.command.turn_id:
                return await self._fail_spawn(
                    action.action_id,
                    request.agent_name,
                    target,
                    RuntimeError("child rejected its initial Turn identity"),
                )
            async with self._graph_lock:
                state = self._subagents()
                state.children[request.agent_name].state = RunningChild(active=target.command)
                _set_receipt_state(
                    state, action.action_id, ChildCommandAcceptedReceipt(target=target)
                )
                await self._commit(state)
        await self._child_host.acknowledge_child_command(child, operation_key=action.action_id)
        result = success_output()
        await self._finish_receipt(action.action_id, SucceededReceipt(target=target, result=result))
        self._ensure_watcher(request.agent_name, target.command)
        return result

    async def _fail_spawn(
        self,
        action_id: str,
        agent_name: str,
        target: SpawnTarget,
        error: Exception,
    ) -> dict[str, JsonValue]:
        stored_failure = failure("subagent_spawn_failed", str(error), retryable=True)
        async with self._graph_lock:
            state = self._subagents()
            state.children[agent_name].state = CreationCleanupPendingChild(failure=stored_failure)
            _set_receipt_state(
                state,
                action_id,
                FailedReceipt(target=target, failure=stored_failure),
            )
            await self._commit(state)
        try:
            await self._child_host.delete_child(target.child_session_id)
        except Exception:
            pass
        else:
            async with self._graph_lock:
                state = self._subagents()
                state.children[agent_name].state = CreationFailedChild(failure=stored_failure)
                await self._commit(state)
        return error_output(stored_failure)

    async def _wait(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        request: WaitInput,
        receipt: SubagentOperationReceipt | None,
    ) -> dict[str, JsonValue]:
        if receipt is None:
            async with self._name_locks[request.agent_name], self._graph_lock:
                child = self._require_child(request.agent_name)
                generation = _current_generation(child)
                if generation is None:
                    return error_output(child_unavailable_failure(child))
                target = WaitTarget(
                    generation=generation,
                    deadline_unix_ms=_now_milliseconds() + request.timeout_ms,
                )
                state = self._subagents()
                _add_receipt(state, action, request.agent_name, target)
                await self._commit(state)
            receipt_state: ActiveSubagentReceiptState = PreparedReceipt(target=target)
        else:
            receipt_state = _active_receipt(receipt)
        replay = _terminal_receipt_output(receipt_state)
        if replay is not None:
            return replay
        if not isinstance(receipt_state, PreparedReceipt) or not isinstance(
            receipt_state.target, WaitTarget
        ):
            raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Invalid wait receipt phase")
        target = receipt_state.target
        child = self._require_child(request.agent_name)
        immediate = _stored_outcome(child, target.generation)
        try:
            outcome = immediate or await self._child_host.wait_for_child_generation(
                await self._open_child(child, self._binding(child)),
                target.generation,
                max(0, target.deadline_unix_ms - _now_milliseconds()),
            )
        except TimeoutError:
            stored_failure = failure(
                "subagent_wait_timeout",
                f"Timed out waiting for {request.agent_name}",
                retryable=True,
            )
            await self._finish_receipt(
                action.action_id,
                FailedReceipt(target=target, failure=stored_failure),
            )
            return error_output(stored_failure)
        await self._record_outcome(request.agent_name, outcome)
        await self.deliver_pending_notifications()
        if isinstance(outcome, CompletedChildTurnOutcome):
            result: dict[str, JsonValue] = {
                "type": "success",
                "value": outcome.final_answer,
            }
            await self._finish_receipt(
                action.action_id, SucceededReceipt(target=target, result=result)
            )
            return result
        stored_failure = (
            outcome.failure
            if isinstance(outcome, FailedChildTurnOutcome)
            else failure("subagent_interrupted", outcome.reason, retryable=True)
        )
        await self._finish_receipt(
            action.action_id, FailedReceipt(target=target, failure=stored_failure)
        )
        return error_output(stored_failure)

    async def _send(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        request: MessageInput,
        receipt: SubagentOperationReceipt | None,
    ) -> dict[str, JsonValue]:
        async with self._name_locks[request.agent_name], self._turn_admission_lock:
            if receipt is None:
                async with self._graph_lock:
                    child = self._require_child(request.agent_name)
                    if not child_is_sendable(child):
                        return error_output(child_unavailable_failure(child))
                    if not isinstance(child.state, RunningChild) and (
                        running_child_count(self._subagents()) >= self._limits.max_concurrent_turns
                    ):
                        return error_output(
                            failure(
                                "subagent_concurrency_limit",
                                "Subagent concurrent Turn limit reached",
                                retryable=True,
                            )
                        )
                    target = SendIntentTarget(child_session_id=child.child_session_id)
                    state = self._subagents()
                    _add_receipt(state, action, request.agent_name, target)
                    await self._commit(state)
                receipt_state: ActiveSubagentReceiptState = PreparedReceipt(target=target)
            else:
                receipt_state = _active_receipt(receipt)
            return await self._resume_send(action, request, receipt_state)

    async def _resume_send(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        request: MessageInput,
        receipt_state: ActiveSubagentReceiptState,
    ) -> dict[str, JsonValue]:
        replay = _terminal_receipt_output(receipt_state)
        if replay is not None:
            return replay
        if not isinstance(receipt_state, PreparedReceipt | ChildCommandAcceptedReceipt):
            raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Invalid send receipt phase")
        target = receipt_state.target
        if not isinstance(target, SendIntentTarget | SendStartTarget | SendSteerTarget):
            raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Send target changed")
        child = self._require_child(request.agent_name)
        handle = await self._open_child(child, self._binding(child))
        if isinstance(receipt_state, PreparedReceipt):
            if not isinstance(target, SendIntentTarget):
                raise UnsafeSubagentRecovery(
                    "subagent_recovery_unsafe", "Prepared send already resolved its target"
                )
            admission = await self._child_host.send_child_message(
                handle,
                message=request.message,
                known_generation=_require_current_generation(child),
                operation_key=action.action_id,
            )
            resolved = admission.target
            if not isinstance(resolved, SendStartTarget | SendSteerTarget):
                raise UnsafeSubagentRecovery(
                    "subagent_recovery_unsafe", "Child returned an invalid send target"
                )
            async with self._graph_lock:
                state = self._subagents()
                state.children[request.agent_name].state = RunningChild(active=resolved.command)
                _set_receipt_state(
                    state,
                    action.action_id,
                    ChildCommandAcceptedReceipt(target=resolved),
                )
                await self._commit(state)
            target = resolved
        if not isinstance(target, SendStartTarget | SendSteerTarget):
            raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Accepted send has no target")
        await self._child_host.acknowledge_child_command(handle, operation_key=action.action_id)
        result = success_output()
        await self._finish_receipt(action.action_id, SucceededReceipt(target=target, result=result))
        self._ensure_watcher(request.agent_name, target.command)
        return result

    async def _interrupt(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        request: AgentInput,
        receipt: SubagentOperationReceipt | None,
    ) -> dict[str, JsonValue]:
        async with self._name_locks[request.agent_name]:
            if receipt is None:
                async with self._graph_lock:
                    child = self._require_child(request.agent_name)
                    if not isinstance(child.state, RunningChild):
                        return error_output(
                            failure(
                                "subagent_not_running",
                                "Subagent has no active Turn",
                                retryable=True,
                            )
                            if isinstance(child.state, IdleChild | TurnFailedChild)
                            else child_unavailable_failure(child)
                        )
                    target = InterruptTarget(
                        command=child.state.active,
                        child_command_id=f"{action.action_id}:interrupt",
                    )
                    state = self._subagents()
                    _add_receipt(state, action, request.agent_name, target)
                    await self._commit(state)
                receipt_state: ActiveSubagentReceiptState = PreparedReceipt(target=target)
            else:
                receipt_state = _active_receipt(receipt)
            replay = _terminal_receipt_output(receipt_state)
            if replay is not None:
                return replay
            if not isinstance(receipt_state, PreparedReceipt | ChildCommandAcceptedReceipt):
                raise UnsafeSubagentRecovery(
                    "subagent_recovery_unsafe", "Invalid interrupt receipt phase"
                )
            target = receipt_state.target
            if not isinstance(target, InterruptTarget):
                raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Interrupt target changed")
            child = self._require_child(request.agent_name)
            handle = await self._open_child(child, self._binding(child))
            if isinstance(receipt_state, PreparedReceipt):
                admission = await self._child_host.interrupt_child(
                    handle,
                    target=target,
                    operation_key=action.action_id,
                )
                if admission.target != target:
                    raise UnsafeSubagentRecovery(
                        "subagent_recovery_unsafe", "Child interrupt target changed"
                    )
                async with self._graph_lock:
                    state = self._subagents()
                    _set_receipt_state(
                        state,
                        action.action_id,
                        ChildCommandAcceptedReceipt(target=target),
                    )
                    await self._commit(state)
            await self._child_host.acknowledge_child_command(handle, operation_key=action.action_id)
            result = success_output()
            await self._finish_receipt(
                action.action_id, SucceededReceipt(target=target, result=result)
            )
            return result

    async def _stop(
        self,
        action: RustRuntimeBuiltinToolCallAction | str,
        request: AgentInput,
        receipt: SubagentOperationReceipt | None,
    ) -> dict[str, JsonValue]:
        action_id = (
            action.action_id if isinstance(action, RustRuntimeBuiltinToolCallAction) else action
        )
        async with self._name_locks[request.agent_name]:
            if receipt is None:
                if not isinstance(action, RustRuntimeBuiltinToolCallAction):
                    raise RuntimeError("new close operation requires its Core Action")
                async with self._graph_lock:
                    child = self._require_child(request.agent_name)
                    if isinstance(child.state, ChildTombstone):
                        target: CloseIdleTarget | CloseRunningTarget = CloseIdleTarget(
                            child_session_id=child.child_session_id
                        )
                        state = self._subagents()
                        _add_receipt(state, action, request.agent_name, target)
                        _set_receipt_state(
                            state,
                            action_id,
                            SucceededReceipt(target=target, result=success_output()),
                        )
                        await self._commit(state)
                        return success_output()
                    if isinstance(child.state, RunningChild):
                        target = CloseRunningTarget(
                            child_session_id=child.child_session_id,
                            command=child.state.active,
                            child_command_id=f"{action_id}:interrupt",
                        )
                        next_state = DeletingRunningChild(active=child.state.active)
                    elif isinstance(
                        child.state,
                        IdleChild
                        | TurnFailedChild
                        | CreationFailedChild
                        | CreationCleanupPendingChild
                        | ReservedChild,
                    ):
                        target = CloseIdleTarget(child_session_id=child.child_session_id)
                        next_state = DeletingIdleChild()
                    else:
                        return error_output(child_unavailable_failure(child))
                    state = self._subagents()
                    state.children[request.agent_name].state = next_state
                    _add_receipt(state, action, request.agent_name, target)
                    await self._commit(state)
                receipt_state: ActiveSubagentReceiptState = PreparedReceipt(target=target)
            else:
                receipt_state = _active_receipt(receipt)
            return await self._resume_stop(action_id, request, receipt_state)

    async def _resume_stop(
        self,
        action_id: str,
        request: AgentInput,
        receipt_state: ActiveSubagentReceiptState,
    ) -> dict[str, JsonValue]:
        replay = _terminal_receipt_output(receipt_state)
        if replay is not None:
            return replay
        target = receipt_state.target
        if not isinstance(target, CloseIdleTarget | CloseRunningTarget):
            raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Close target changed")
        child = self._require_child(request.agent_name)
        await self._cancel_watcher(request.agent_name, _target_generation(target))
        async with self._graph_lock:
            state = self._subagents()
            receipt_state = _active_receipt(state.operation_receipts[action_id])
            child = state.children[request.agent_name]
        handle: ChildSessionHandle | None = None
        # Only a running close must reach the live child to interrupt its turn,
        # which requires re-opening it under its Host binding. An idle close is
        # pure teardown: it unloads and deletes durable state by session id and
        # never re-runs the child, so it must not re-resolve the Host policy.
        # Gating idle teardown on a still-matching binding lets policy drift
        # strand a quiescent child that can no longer be stopped, failing the
        # parent turn with subagent_policy_revoked instead of cleaning up.
        if isinstance(target, CloseRunningTarget) and not isinstance(
            receipt_state, CloseCleanupStartedReceipt
        ):
            handle = await self._open_child(
                child,
                self._binding(child),
                require_existing=not isinstance(child.state, DeletingIdleChild),
            )
            receipt_state = await self._finish_running_close(
                action_id, request.agent_name, handle, target, receipt_state
            )
        if isinstance(receipt_state, PreparedReceipt | CloseNotificationCommittedReceipt):
            async with self._graph_lock:
                state = self._subagents()
                state.children[request.agent_name].state = DeletingIdleChild()
                _set_receipt_state(
                    state,
                    action_id,
                    CloseCleanupStartedReceipt(target=target),
                )
                await self._commit(state)
            receipt_state = CloseCleanupStartedReceipt(target=target)
        if not isinstance(receipt_state, CloseCleanupStartedReceipt):
            raise UnsafeSubagentRecovery("subagent_recovery_unsafe", "Close did not reach cleanup")
        if handle is not None:
            await self._child_host.unload_child(handle)
        await self._child_host.delete_child(child.child_session_id)
        result = success_output()
        async with self._graph_lock:
            state = self._subagents()
            state.children[request.agent_name].state = ChildTombstone()
            _set_receipt_state(
                state,
                action_id,
                SucceededReceipt(target=target, result=result),
            )
            await self._commit(state)
        return result

    async def _finish_running_close(
        self,
        action_id: str,
        agent_name: str,
        handle: ChildSessionHandle,
        target: CloseRunningTarget,
        receipt_state: ActiveSubagentReceiptState,
    ) -> ActiveSubagentReceiptState:
        if isinstance(receipt_state, PreparedReceipt):
            admission = await self._child_host.interrupt_child(
                handle,
                target=target,
                operation_key=action_id,
            )
            if admission.target != target:
                raise UnsafeSubagentRecovery(
                    "subagent_recovery_unsafe", "Child close interrupt target changed"
                )
            async with self._graph_lock:
                state = self._subagents()
                current = _active_receipt(state.operation_receipts[action_id])
                if isinstance(current, PreparedReceipt):
                    current = ChildCommandAcceptedReceipt(target=target)
                    _set_receipt_state(state, action_id, current)
                    await self._commit(state)
                receipt_state = current
        if isinstance(receipt_state, ChildCommandAcceptedReceipt):
            outcome = await self._child_host.wait_for_child_generation(
                handle, target.command.generation, 2_147_483_647
            )
            await self._record_close_outcome(action_id, agent_name, target, outcome)
            async with self._graph_lock:
                receipt_state = _active_receipt(self._subagents().operation_receipts[action_id])
        if isinstance(receipt_state, CloseOutcomeRecordedReceipt):
            await self._child_host.acknowledge_child_command(handle, operation_key=action_id)
            await self.deliver_pending_notifications()
            async with self._graph_lock:
                state = self._subagents()
                receipt_state = _active_receipt(state.operation_receipts[action_id])
        if isinstance(receipt_state, CloseNotificationCommittedReceipt):
            return receipt_state
        if isinstance(receipt_state, CloseCleanupStartedReceipt):
            return receipt_state
        raise UnsafeSubagentRecovery(
            "subagent_recovery_unsafe",
            "Close outcome did not reach a committed notification",
        )

    async def _record_close_outcome(
        self,
        action_id: str,
        agent_name: str,
        target: CloseRunningTarget,
        outcome: ChildTurnOutcome,
    ) -> None:
        async with self._graph_lock:
            state = self._subagents()
            child = state.children[agent_name]
            _queue_outcome_notification(state, child, outcome)
            current = _active_receipt(state.operation_receipts[action_id])
            if isinstance(current, CloseOutcomeRecordedReceipt):
                if current.outcome != outcome:
                    raise RuntimeError("close outcome changed after commitment")
            elif isinstance(
                current,
                CloseNotificationCommittedReceipt | CloseCleanupStartedReceipt,
            ):
                return
            elif isinstance(current, PreparedReceipt | ChildCommandAcceptedReceipt):
                _set_receipt_state(
                    state,
                    action_id,
                    CloseOutcomeRecordedReceipt(target=target, outcome=outcome),
                )
            else:
                raise RuntimeError("close receipt reached an invalid outcome phase")
            await self._commit(state)

    async def _record_outcome(self, agent_name: str, outcome: ChildTurnOutcome) -> None:
        async with self._graph_lock:
            state = self._subagents()
            child = state.children[agent_name]
            existing = _stored_outcome(child, outcome.generation)
            if existing is not None:
                if existing != outcome:
                    raise RuntimeError("child generation outcome changed after commitment")
                return
            if isinstance(child.state, DeletingRunningChild):
                close_receipt = _close_receipt_for_generation(state, agent_name, outcome.generation)
                if close_receipt is None:
                    raise RuntimeError("deleting-running child has no matching close receipt")
                current = _active_receipt(close_receipt)
                if isinstance(current, CloseOutcomeRecordedReceipt):
                    if current.outcome != outcome:
                        raise RuntimeError("close outcome changed after commitment")
                elif isinstance(current, PreparedReceipt | ChildCommandAcceptedReceipt):
                    if not isinstance(current.target, CloseRunningTarget):
                        raise RuntimeError("deleting-running child has an invalid close target")
                    close_receipt.state = CloseOutcomeRecordedReceipt(
                        target=current.target,
                        outcome=outcome,
                    )
                elif isinstance(
                    current,
                    CloseNotificationCommittedReceipt | CloseCleanupStartedReceipt,
                ):
                    return
                else:
                    raise RuntimeError("deleting-running child has an invalid close receipt")
            elif isinstance(outcome, FailedChildTurnOutcome):
                child.state = TurnFailedChild(outcome=outcome, reusable=outcome.failure.retryable)
            else:
                child.state = IdleChild(last_outcome=outcome)
            _queue_outcome_notification(state, child, outcome)
            await self._commit(state)

    def _ensure_watcher(self, agent_name: str, generation: ChildGenerationRef) -> None:
        if self._closing:
            return
        key = (agent_name, generation.generation)
        existing = self._watchers.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._watch_generation(agent_name, generation),
            name=f"harness-subagent-watch:{agent_name}:{generation.generation}",
        )
        self._watchers[key] = task
        add_subagent_active_turns(1)
        task.add_done_callback(lambda done, watcher_key=key: self._watcher_done(watcher_key, done))

    async def _watch_generation(self, agent_name: str, generation: ChildGenerationRef) -> None:
        try:
            child = self._require_child(agent_name)
            handle = await self._open_child(child, self._binding(child))
            outcome = await self._child_host.wait_for_child_generation(
                handle, generation.generation, 2_147_483_647
            )
            await self._record_outcome(agent_name, outcome)
            await self.deliver_pending_notifications()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            add_subagent_recovery_failure(
                failure_code="subagent_child_watch_failed",
                phase="child_watch",
            )
            outcome = FailedChildTurnOutcome(
                generation=generation.generation,
                turn_id=generation.turn_id,
                completed_at_unix_ms=_now_milliseconds(),
                failure=failure("subagent_child_watch_failed", str(exc), retryable=False),
            )
            await self._record_outcome(agent_name, outcome)
            await self.deliver_pending_notifications()

    async def _cancel_watcher(self, agent_name: str, generation: int | None) -> None:
        if generation is None:
            return
        watcher = self._watchers.pop((agent_name, generation), None)
        if watcher is None or watcher is asyncio.current_task():
            return
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)

    def _forget_watcher(self, key: tuple[str, int], watcher: asyncio.Task[None]) -> None:
        if self._watchers.get(key) is watcher:
            self._watchers.pop(key, None)

    def _watcher_done(self, key: tuple[str, int], watcher: asyncio.Task[None]) -> None:
        add_subagent_active_turns(-1)
        self._forget_watcher(key, watcher)
        if watcher.cancelled():
            return
        error = watcher.exception()
        if error is not None:
            logger.error(
                "Unified subagent watcher terminated unexpectedly",
                extra={
                    "harness_backend": "unified",
                    "failure_code": type(error).__name__,
                },
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _preflight_children(self) -> None:
        opened: list[ChildSessionHandle] = []
        try:
            for child in sorted(
                self._subagents().children.values(),
                key=lambda item: item.child_session_id,
            ):
                if isinstance(
                    child.state,
                    ReservedChild
                    | CreationFailedChild
                    | CreationCleanupPendingChild
                    | DeletingIdleChild
                    | ChildTombstone,
                ):
                    continue
                try:
                    binding = self._binding(child)
                except UnsafeSubagentRecovery:
                    if isinstance(child.state, IdleChild | TurnFailedChild):
                        # A quiescent child has no effect to recover. Keep its pinned
                        # binding so a later command fails closed, but do not make that
                        # historical child prevent the root Session from resuming.
                        continue
                    raise
                opened.append(
                    await self._open_child(
                        child,
                        binding,
                        start_new_actions=False,
                        require_existing=True,
                    )
                )
        except BaseException as exc:
            if not isinstance(exc, asyncio.CancelledError):
                add_subagent_recovery_failure(
                    failure_code=getattr(exc, "code", type(exc).__name__),
                    phase="child_preflight",
                )
            for handle in reversed(opened):
                await self._child_host.unload_child(handle)
            raise

    async def _open_child(
        self,
        child: ChildSessionRecord,
        binding: ResolvedChildSessionBinding,
        *,
        start_new_actions: bool = True,
        require_existing: bool | None = None,
    ) -> ChildSessionHandle:
        return await self._child_host.create_or_restore_child(
            identity=SubagentSessionIdentity(
                session_id=child.child_session_id,
                root_session_id=self._runtime.identity.root_session_id,
                parent_session_id=self._runtime.session_id,
            ),
            binding=binding,
            spawn_key=child.spawn_action_id,
            start_new_actions=start_new_actions,
            require_existing=(
                not isinstance(child.state, ReservedChild)
                if require_existing is None
                else require_existing
            ),
        )

    def _binding(self, child: ChildSessionRecord) -> ResolvedChildSessionBinding:
        binding = self._bindings.get(child.agent_type)
        if binding is None:
            raise UnsafeSubagentRecovery(
                "subagent_policy_revoked", "Stored subagent type is no longer available"
            )
        if (
            binding.template_digest != child.template_digest
            or binding.policy_ceiling_digest != child.policy_ceiling_digest
        ):
            raise UnsafeSubagentRecovery(
                "subagent_policy_revoked",
                "Stored subagent binding no longer matches Host policy",
            )
        return binding

    def _require_child(self, agent_name: str) -> ChildSessionRecord:
        child = self._subagents().children.get(agent_name)
        if child is None:
            raise ExpectedSubagentError(
                failure(
                    "subagent_not_found",
                    f"Unknown subagent: {agent_name}",
                    retryable=False,
                )
            )
        return child

    def _subagents(self) -> SubagentRuntimeState:
        state = self._runtime.runtime_state.subagents
        if state is None:
            raise RuntimeError("subagent controller state is not initialized")
        return state.model_copy(deep=True)

    async def _commit(self, subagents: SubagentRuntimeState) -> None:
        updated_subagents = _sorted_state(subagents)
        await self._runtime.update_runtime_state(
            lambda state: state.model_copy(update={"subagents": updated_subagents}, deep=True)
        )

    async def _finish_receipt(
        self,
        action_id: str,
        receipt_state: SucceededReceipt | FailedReceipt,
    ) -> None:
        async with self._graph_lock:
            state = self._subagents()
            _set_receipt_state(state, action_id, receipt_state)
            await self._commit(state)

    def _spawn_annotations(self, agent_name: str) -> dict[str, JsonValue]:
        child = self._subagents().children[agent_name]
        return {
            "mistral.vibe.subagent": {
                "agentName": child.agent_name,
                "agentType": child.agent_type,
                "childSessionId": child.child_session_id,
                "generation": 1,
            }
        }


class ExpectedSubagentError(Exception):
    def __init__(self, stored_failure: SubagentFailure) -> None:
        self.failure = stored_failure
        super().__init__(stored_failure.message)


class UnsafeSubagentRecovery(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _same_enforced_policy(
    stored: ResolvedSubagentPolicyCeiling, current: ResolvedSubagentPolicyCeiling
) -> bool:
    # Compare enforced policy only: ambient env names are launcher noise, not drift.
    excluded = {"allowed_environment_names"}
    return stored.model_dump(exclude=excluded) == current.model_dump(exclude=excluded)


def _policy_can_be_rebound(state: SubagentRuntimeState) -> bool:
    """Whether policy drift can be adopted without recovering child-side work."""
    if state.operation_receipts:
        return False
    return all(
        isinstance(
            child.state,
            IdleChild | TurnFailedChild | CreationFailedChild | ChildTombstone,
        )
        for child in state.children.values()
    )


def _unbindable_recoverable_children(
    state: SubagentRuntimeState,
    bindings: Mapping[str | None, ResolvedChildSessionBinding],
) -> frozenset[str]:
    """Running/mid-close children whose pinned digests no longer match a binding.

    ``open`` settles exactly these up front (a real revocation, or the one-time
    digest-formula change) so preflight neither wedges nor leaves their receipts
    for a later ``_binding`` to reject; bindable siblings keep recovering.
    """
    unbindable: set[str] = set()
    for name, child in state.children.items():
        if not isinstance(child.state, RunningChild | DeletingRunningChild):
            continue
        binding = bindings.get(child.agent_type)
        if (
            binding is None
            or binding.template_digest != child.template_digest
            or binding.policy_ceiling_digest != child.policy_ceiling_digest
        ):
            unbindable.add(name)
    return frozenset(unbindable)


def _settle_children_for_restore(
    state: SubagentRuntimeState, agent_names: frozenset[str], *, observed_at: int
) -> SubagentRuntimeState:
    """Settle the named children and their receipts on a cold restore.

    The process that owned any in-flight child turn or parent operation has
    exited, so none of it is live: transition the named running children to an
    interrupted idle outcome and terminate their open operation receipts, so
    recovery replays those to a terminal result instead of re-driving child work.
    Siblings not named are left untouched and recover normally -- pass every
    child to settle the whole graph (policy drift), or just the unbindable ones.
    """
    children = {
        name: (
            child.model_copy(
                update={"state": _settle_child_for_restore(child.state, observed_at=observed_at)},
                deep=True,
            )
            if name in agent_names
            else child
        )
        for name, child in state.children.items()
    }
    receipts = {
        action_id: (
            _settle_receipt_for_restore(receipt) if receipt.agent_name in agent_names else receipt
        )
        for action_id, receipt in state.operation_receipts.items()
    }
    return state.model_copy(
        update={"children": children, "operation_receipts": receipts}, deep=True
    )


def _settle_child_for_restore(
    state: ChildLifecycleState, *, observed_at: int
) -> ChildLifecycleState:
    if isinstance(state, RunningChild):
        return IdleChild(
            last_outcome=InterruptedChildTurnOutcome(
                generation=state.active.generation,
                turn_id=state.active.turn_id,
                completed_at_unix_ms=observed_at,
                reason="Subagent turn interrupted by host restart",
            )
        )
    if isinstance(state, ReservedChild):
        return CreationFailedChild(
            failure=SubagentFailure(
                code="subagent_interrupted",
                message="Subagent creation interrupted by host restart",
                retryable=False,
            )
        )
    if isinstance(state, DeletingRunningChild):
        return DeletingIdleChild()
    return state


def _settle_receipt_for_restore(
    receipt: SubagentOperationReceipt,
) -> SubagentOperationReceipt:
    active = _active_receipt(receipt)
    if isinstance(active, SucceededReceipt | FailedReceipt):
        # Already terminal: drop any abandonment wrapper so recovery replays it.
        return receipt.model_copy(update={"state": active}, deep=True)
    return receipt.model_copy(
        update={
            "state": FailedReceipt(
                target=active.target,
                failure=SubagentFailure(
                    code="subagent_interrupted",
                    message="Subagent operation interrupted by host restart",
                    retryable=True,
                ),
            )
        },
        deep=True,
    )


def _add_receipt(
    state: SubagentRuntimeState,
    action: RustRuntimeBuiltinToolCallAction,
    agent_name: str,
    target: SubagentOperationTarget,
) -> None:
    sequence = state.next_operation_sequence
    state.next_operation_sequence += 1
    state.operation_receipts[action.action_id] = SubagentOperationReceipt(
        action_id=action.action_id,
        admission_sequence=sequence,
        agent_name=agent_name,
        request_digest=request_digest(action),
        state=PreparedReceipt(target=target),
    )


def _set_receipt_state(
    state: SubagentRuntimeState,
    action_id: str,
    receipt_state: ActiveSubagentReceiptState,
) -> None:
    state.operation_receipts[action_id].state = receipt_state


def _active_receipt(receipt: SubagentOperationReceipt) -> ActiveSubagentReceiptState:
    if isinstance(receipt.state, AbandoningReceipt):
        return receipt.state.previous
    return receipt.state


def _terminal_receipt_output(
    receipt: ActiveSubagentReceiptState,
) -> dict[str, JsonValue] | None:
    if isinstance(receipt, SucceededReceipt):
        if not isinstance(receipt.result, dict):
            raise UnsafeSubagentRecovery(
                "subagent_recovery_unsafe", "Stored subagent result is not an object"
            )
        return cast(dict[str, JsonValue], receipt.result)
    if isinstance(receipt, FailedReceipt):
        return error_output(receipt.failure)
    return None


def _result_event(
    action: RustRuntimeBuiltinToolCallAction,
    output: dict[str, JsonValue],
    annotations: dict[str, JsonValue] | None = None,
) -> RustToolSucceededEvent:
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult(
            structured_content=cast(JsonValue, output),
            _meta=annotations or None,
        ),
    )


def _unsafe_recovery_event(
    action: RustRuntimeBuiltinToolCallAction, code: str, message: str
) -> RustFailTurnEvent:
    return RustFailTurnEvent(
        expected_turn_id=action.turn_id,
        action_id=action.action_id,
        error=RustProtocolError(
            code=code,
            message=message,
            retryable=False,
            details={"action_kind": action.call.name},
        ),
    )


def _current_generation(child: ChildSessionRecord) -> int | None:
    if isinstance(child.state, RunningChild | DeletingRunningChild):
        return child.state.active.generation
    if isinstance(child.state, IdleChild):
        return child.state.last_outcome.generation
    if isinstance(child.state, TurnFailedChild):
        return child.state.outcome.generation
    return None


def _require_current_generation(child: ChildSessionRecord) -> ChildGenerationRef:
    if isinstance(child.state, RunningChild | DeletingRunningChild):
        return child.state.active
    if isinstance(child.state, IdleChild):
        outcome = child.state.last_outcome
    elif isinstance(child.state, TurnFailedChild):
        outcome = child.state.outcome
    else:
        raise UnsafeSubagentRecovery(
            "subagent_recovery_unsafe", "Sendable child has no current generation"
        )
    return ChildGenerationRef(generation=outcome.generation, turn_id=outcome.turn_id)


def _stored_outcome(child: ChildSessionRecord, generation: int) -> ChildTurnOutcome | None:
    if isinstance(child.state, IdleChild) and child.state.last_outcome.generation == generation:
        return child.state.last_outcome
    if isinstance(child.state, TurnFailedChild) and child.state.outcome.generation == generation:
        return child.state.outcome
    return None


def _target_generation(target: CloseIdleTarget | CloseRunningTarget) -> int | None:
    return target.command.generation if isinstance(target, CloseRunningTarget) else None


def _queue_outcome_notification(
    state: SubagentRuntimeState,
    child: ChildSessionRecord,
    outcome: ChildTurnOutcome,
) -> None:
    if child.last_notified_generation >= outcome.generation:
        return
    if any(
        item.agent_name == child.agent_name and item.generation == outcome.generation
        for item in state.notifications.pending
    ):
        return
    state.notifications.pending.append(pending_notification(state, child, outcome))
    state.notifications.next_sequence += 1


def _sorted_state(state: SubagentRuntimeState) -> SubagentRuntimeState:
    state.children = dict(sorted(state.children.items()))
    state.operation_receipts = dict(sorted(state.operation_receipts.items()))
    state.notifications.pending.sort(key=lambda item: item.sequence)
    return SubagentRuntimeState.model_validate(state.model_dump(mode="python"))


def _now_milliseconds() -> int:
    return time.time_ns() // 1_000_000


def _operation_name(
    action_name: str,
) -> Literal["list", "spawn", "wait", "send_message", "interrupt", "close"]:
    match action_name:
        case "subagent.list":
            return "list"
        case "subagent.spawn":
            return "spawn"
        case "subagent.wait":
            return "wait"
        case "subagent.send_message":
            return "send_message"
        case "subagent.interrupt":
            return "interrupt"
        case "subagent.stop":
            return "close"
        case _:
            raise ValueError(f"unsupported subagent operation: {action_name}")


def _event_outcome(
    event: RustEvent,
    *,
    operation: Literal["list", "spawn", "wait", "send_message", "interrupt", "close"],
) -> tuple[Literal["failure", "success", "timeout"], str | None]:
    if isinstance(event, RustFailTurnEvent):
        return "failure", event.error.code
    if not isinstance(event, RustToolSucceededEvent):
        return "failure", type(event).__name__
    content = event.result.structured_content
    if operation == "list":
        return "success", None
    if isinstance(content, dict) and content.get("type") == "success":
        return "success", None
    error = content.get("error") if isinstance(content, dict) else None
    failure_code = error.partition(":")[0] if isinstance(error, str) else "invalid_result"
    if operation == "wait" and failure_code == "subagent_wait_timeout":
        return "timeout", failure_code
    return "failure", failure_code


def _pending_notification_outcome(
    state: SubagentRuntimeState,
    agent_name: str,
    generation: int,
) -> ChildTurnOutcome:
    child = state.children[agent_name]
    outcome = _stored_outcome(child, generation)
    if outcome is not None:
        return outcome
    for receipt in state.operation_receipts.values():
        receipt_state = _active_receipt(receipt)
        if (
            receipt.agent_name == agent_name
            and isinstance(receipt_state, CloseOutcomeRecordedReceipt)
            and receipt_state.outcome.generation == generation
        ):
            return receipt_state.outcome
    raise RuntimeError("pending child notification has no durable terminal outcome")


def _close_receipt_for_generation(
    state: SubagentRuntimeState,
    agent_name: str,
    generation: int,
) -> SubagentOperationReceipt | None:
    for receipt in state.operation_receipts.values():
        receipt_state = _active_receipt(receipt)
        if receipt.agent_name != agent_name:
            continue
        target = receipt_state.target
        if isinstance(target, CloseRunningTarget) and target.command.generation == generation:
            return receipt
    return None


__all__ = ["SubagentController"]
