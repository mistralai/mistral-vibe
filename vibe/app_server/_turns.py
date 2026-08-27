from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from vibe.app_server._execution import (
    ActiveSessionExecution,
    SessionExecution,
    SessionExecutionKind,
    cancel_tasks,
)
from vibe.app_server._model import ProtocolModel
from vibe.app_server._projection import project_agents, project_stats
from vibe.app_server._projector import EventProjector, ProjectedUpdate
from vibe.app_server._root_session import SessionCoordinator, rebind_history
from vibe.app_server._state import session_preview
from vibe.app_server._utils import decode_input, now_ms, public_error
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    ApprovalCallbackOutput,
    ApprovalDecisionType,
    CallbackOutput,
    EffectDetail,
    EffectState,
    MentionStats,
    PublicCallbackEntry,
    PublicError,
    PublicHistoryEntry,
    PublicMessageSource,
    PublicRetryCategory,
    PublicRetryState,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    PublicTurnStopReason,
    ScheduledLoopFiredNoticeDetail,
    UserInputCallbackDetail,
    UserInputCallbackOutput,
    UserQuestionRequest,
)
from vibe.app_server.protocol import (
    CallbackResultError,
    ContextInjectParams,
    ContextInjectResponse,
    JsonPatchOperation,
    SessionCompactedParams,
    SessionContextClearedParams,
    SessionSnapshotParams,
    SessionUpdatedParams,
    StatsUpdatedParams,
    TurnCompletedParams,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnRetryingParams,
    TurnStartedParams,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
)
from vibe.core.agent_loop import AgentLoop, AgentTurnOptions
from vibe.core.subagents import SubagentRunnerPort
from vibe.core.tools.io_port import ToolIOPort
from vibe.core.types import (
    AgentProfileChangedEvent,
    ApprovalRequestEvent,
    ApprovalResponse,
    AssistantEvent,
    BaseEvent,
    CompactEndEvent,
    ContextClearedEvent,
    ImageAttachment,
    ManualShellContext,
    UserInputRequestEvent,
    UserMessageEvent,
)
from vibe.core.utils.retry import RetryCategory, RetryReason
from vibe.core.utils.tags import CancellationReason, get_user_cancellation_message
from vibe.user_content import UserResource

type Notify = Callable[[str, ProtocolModel], Awaitable[None]]
type DeliverCallback = Callable[[PublicCallbackEntry], Awaitable[None]]
type CoreEventSink = Callable[[BaseEvent], Awaitable[None]]
type SnapshotState = Callable[[], PublicSessionState]

# Retry hooks may cross a worker thread. Context keeps each notice tied to the
# turn whose model request produced it, even if delivery happens later.
_retry_turn_id: ContextVar[str | None] = ContextVar(
    "app_server_retry_turn_id", default=None
)


class TurnConflictError(RuntimeError):
    pass


class StaleTurnError(RuntimeError):
    def __init__(self, active_turn_id: str) -> None:
        self.active_turn_id = active_turn_id
        super().__init__("Active turn does not match expectedTurnId")


class CallbackNotFoundError(RuntimeError):
    pass


class CallbackConflictError(RuntimeError):
    pass


class CallbackClosedError(RuntimeError):
    pass


class CallbackRejectedError(RuntimeError):
    pass


@dataclass(slots=True)
class CallbackRecord:
    event: ApprovalRequestEvent | UserInputRequestEvent
    future: asyncio.Future[CallbackOutput]
    resolution: CallbackOutput | CallbackResultError | None = None
    core_resolved: bool = False
    resolution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _public_retry_category(category: RetryCategory) -> PublicRetryCategory:
    match category:
        case RetryCategory.RATE_LIMITED:
            return PublicRetryCategory.RATE_LIMITED
        case RetryCategory.SERVER_ERROR:
            return PublicRetryCategory.SERVER_ERROR
        case RetryCategory.TIMED_OUT:
            return PublicRetryCategory.TIMED_OUT
        case RetryCategory.CONNECTION:
            return PublicRetryCategory.CONNECTION
        case RetryCategory.UNKNOWN:
            return PublicRetryCategory.UNKNOWN


class TurnController:
    def __init__(
        self,
        agent_loop: AgentLoop,
        notify: Notify,
        deliver_callback: DeliverCallback,
        execution: SessionExecution,
        subagent_runner: SubagentRunnerPort,
        *,
        snapshot_state: SnapshotState,
        tool_io: ToolIOPort | None = None,
        event_sink: CoreEventSink | None = None,
        session_coordinator: SessionCoordinator | None = None,
    ) -> None:
        self._agent_loop = agent_loop
        self._notify = notify
        self._deliver_callback = deliver_callback
        self._execution = execution
        self._subagent_runner = subagent_runner
        self._snapshot_state = snapshot_state
        self._tool_io = tool_io
        self._event_sink = event_sink
        self._session_coordinator = session_coordinator
        self._session_execution: ActiveSessionExecution | None = None
        self._active_turn: PublicTurn | None = None
        self._retrying: PublicRetryState | None = None
        self._completed_turns: list[PublicTurn] = []
        self._active_task: asyncio.Task[None] | None = None
        self._projector: EventProjector | None = None
        self._harness_effects: dict[str, EventProjector] = {}
        self._history: list[PublicHistoryEntry] = []
        self._callbacks: dict[str, CallbackRecord] = {}
        self._scheduled_loop_id: str | None = None

    @property
    def active_turn(self) -> PublicTurn | None:
        return self._active_turn

    @property
    def retrying(self) -> PublicRetryState | None:
        return self._retrying

    @property
    def completed_turns(self) -> list[PublicTurn]:
        return self._completed_turns.copy()

    @property
    def turns(self) -> list[PublicTurn]:
        return [
            *self._completed_turns,
            *([self._active_turn] if self._active_turn is not None else []),
        ]

    @property
    def history(self) -> list[PublicHistoryEntry]:
        current = self._projector.history if self._projector is not None else []
        harness_effects = [
            entry
            for projector in self._harness_effects.values()
            for entry in projector.history
        ]
        return [*self._history, *current, *harness_effects]

    @property
    def callbacks(self) -> list[PublicCallbackEntry]:
        if self._projector is None:
            return []
        return [
            entry
            for entry in self._projector.history
            if isinstance(entry, PublicCallbackEntry)
        ]

    def start(
        self, params: TurnStartParams, *, scheduled_loop_id: str | None = None
    ) -> tuple[TurnStartResponse, Callable[[], None]]:
        if self._active_task is not None and not self._active_task.done():
            raise TurnConflictError("A turn is already running")
        self._retrying = None
        decoded = decode_input(
            params, session_dir=self._agent_loop.session_logger.session_dir
        )
        turn = PublicTurn(
            id=str(uuid4()),
            session_id=params.session_id,
            status=PublicTurnStatus.IN_PROGRESS,
            started_at=now_ms(),
        )
        session_execution = self._execution.begin(SessionExecutionKind.TURN, turn.id)
        self._session_execution = session_execution
        self._active_turn = turn
        self._scheduled_loop_id = scheduled_loop_id
        self._projector = EventProjector(
            params.session_id,
            turn.id,
            session_preview=session_preview(self._agent_loop),
        )

        def start_turn() -> None:
            retry_turn_token = _retry_turn_id.set(turn.id)
            try:
                self._active_task = asyncio.create_task(
                    self._run_turn(
                        turn,
                        decoded.prompt,
                        session_execution=session_execution,
                        params=params,
                        images=decoded.images,
                        input_text=(decoded.input_text if decoded.resources else None),
                        resources=decoded.resources,
                    )
                )
            finally:
                _retry_turn_id.reset(retry_turn_token)

        return TurnStartResponse(turn=turn), start_turn

    async def wait_for_turn(self, turn_id: str) -> PublicTurn:
        task = self._active_task
        if task is None or self._active_turn is None:
            raise StaleTurnError(turn_id)
        if self._active_turn.id != turn_id:
            raise StaleTurnError(self._active_turn.id)
        await task
        completed = next(
            (turn for turn in self._completed_turns if turn.id == turn_id), None
        )
        if completed is None:
            raise RuntimeError(f"Turn did not complete: {turn_id}")
        return completed

    async def link_subagent(self, tool_call_id: str, child_session_id: str) -> None:
        projector = self._projector
        if projector is None:
            raise RuntimeError("Cannot link a child session without an active turn")
        for update in projector.link_subagent(tool_call_id, child_session_id):
            await self._emit_projected(update)

    async def replace_subagent(
        self, tool_call_id: str, old_session_id: str, new_session_id: str
    ) -> None:
        projector = self._projector
        if projector is None:
            raise RuntimeError("Cannot replace a child session without an active turn")
        for update in projector.replace_subagent(
            tool_call_id, old_session_id, new_session_id
        ):
            await self._emit_projected(update)

    async def unlink_subagent(self, tool_call_id: str, child_session_id: str) -> None:
        projector = self._projector
        if projector is None:
            return
        for update in projector.unlink_subagent(tool_call_id, child_session_id):
            await self._emit_projected(update)

    async def start_effect(
        self, *, session_id: str, entry_id: str, title: str, detail: EffectDetail
    ) -> None:
        if entry_id in self._harness_effects or any(
            entry.id == entry_id for entry in self._history
        ):
            raise ValueError(f"Duplicate harness effect: {entry_id}")
        projector = EventProjector(session_id, None)
        self._harness_effects[entry_id] = projector
        try:
            await self._emit_projected(
                projector.start_effect(entry_id, title=title, detail=detail)
            )
        except BaseException:
            self._harness_effects.pop(entry_id, None)
            raise

    async def append_effect_output(self, entry_id: str, text: str) -> None:
        projector = self._require_harness_effect(entry_id)
        await self._emit_projected(projector.append_effect_output(entry_id, text))

    async def complete_effect(self, entry_id: str, state: EffectState) -> None:
        projector = self._require_harness_effect(entry_id)
        events = projector.complete_effect(entry_id, state)
        # Retired before the emit, which can fail. The effect is already finished
        # in the projector's own history, so a registration left behind would
        # make `history` report it as still running for the rest of the session
        # - a worse account of a finished effect than a dropped notification.
        self._history.extend(projector.history)
        self._harness_effects.pop(entry_id, None)
        await self._emit_projected(events)

    async def steer(self, params: TurnSteerParams) -> TurnSteerResponse:
        self._require_active_turn(params.expected_turn_id)
        decoded = decode_input(
            params, session_dir=self._agent_loop.session_logger.session_dir
        )
        self._record_mentions(params.mention_stats, params.client_user_message_id)
        events = await self._agent_loop.inject_user_context(
            decoded.prompt,
            as_message=True,
            inject_implicit=params.inject_invoked_skill,
            images=decoded.images or None,
            input_text=decoded.input_text if decoded.resources else None,
            resources=decoded.resources or None,
            client_message_id=params.client_user_message_id,
        )
        await self._project_events(events, user_message_source="turn_steer")
        return TurnSteerResponse()

    def interrupt(self, params: TurnInterruptParams) -> TurnInterruptResponse:
        self._require_active_turn(params.expected_turn_id)
        if self._active_task is not None:
            self._active_task.cancel()
        return TurnInterruptResponse()

    async def inject(
        self,
        params: ContextInjectParams,
        *,
        manual_shell: ManualShellContext | None = None,
    ) -> ContextInjectResponse:
        self._execution.require_idle()
        if self._active_turn is not None:
            raise TurnConflictError("Use turn/steer while a turn is active")
        decoded = decode_input(
            params, session_dir=self._agent_loop.session_logger.session_dir
        )
        self._record_mentions(params.mention_stats, params.client_user_message_id)
        projector = EventProjector(
            params.session_id,
            f"injection:{uuid4()}",
            session_preview=session_preview(self._agent_loop),
        )
        events = await self._agent_loop.inject_user_context(
            decoded.prompt,
            as_message=params.as_message,
            inject_implicit=params.inject_invoked_skill,
            images=decoded.images or None,
            input_text=decoded.input_text if decoded.resources else None,
            resources=decoded.resources or None,
            client_message_id=params.client_user_message_id,
            manual_shell=manual_shell,
        )
        for event in events:
            for update in projector.project(event, user_message_source="harness"):
                await self._emit_projected(update)
        self._history.extend(projector.history)
        return ContextInjectResponse(entries=projector.history)

    async def answer_callback(self, callback_id: str, output: CallbackOutput) -> str:
        record = self._callbacks.get(callback_id)
        if record is None:
            raise CallbackNotFoundError(f"Callback not found: {callback_id}")
        async with record.resolution_lock:
            if record.core_resolved and record.resolution is None:
                raise CallbackClosedError(f"Callback is closed: {callback_id}")
            if record.resolution is not None:
                if record.resolution.model_dump(mode="json") == output.model_dump(
                    mode="json"
                ):
                    return "duplicate"
                raise CallbackConflictError("Callback already has a different answer")
            match record.event, output:
                case ApprovalRequestEvent(), ApprovalCallbackOutput():
                    pass
                case UserInputRequestEvent(), UserInputCallbackOutput():
                    pass
                case _:
                    raise CallbackConflictError("Callback answer has the wrong type")
            if self._projector is not None:
                for update in self._projector.resolve_callback(callback_id, output):
                    await self._emit_projected(update)
            await self._emit_status("running")
            record.resolution = output
            if not record.future.done():
                record.future.set_result(output)
            return "accepted"

    async def reject_callback(
        self, callback_id: str, error: CallbackResultError
    ) -> str:
        record = self._callbacks.get(callback_id)
        if record is None:
            raise CallbackNotFoundError(f"Callback not found: {callback_id}")
        async with record.resolution_lock:
            if record.core_resolved and record.resolution is None:
                raise CallbackClosedError(f"Callback is closed: {callback_id}")
            if record.resolution is not None:
                if record.resolution.model_dump(mode="json") == error.model_dump(
                    mode="json"
                ):
                    return "duplicate"
                raise CallbackConflictError("Callback already has a different answer")
            record.resolution = error
            self._reject_callback_record(record, CallbackRejectedError(error.message))
            return "accepted"

    async def close(self) -> None:
        errors: list[BaseException] = []
        if self._active_task is not None:
            errors.extend(await cancel_tasks([self._active_task], label="active turn"))
        try:
            await self._clear_retrying()
        except BaseException as exc:
            errors.append(exc)
        if self._session_execution is not None:
            self._execution.finish(self._session_execution)
            self._session_execution = None
        self._scheduled_loop_id = None
        self._cancel_callbacks("App server closed")
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close turn controller", errors)

    async def reset(self) -> None:
        await self.close()
        self._active_turn = None
        self._completed_turns.clear()
        self._active_task = None
        self._session_execution = None
        self._projector = None
        self._harness_effects.clear()
        self._history.clear()
        self._callbacks.clear()

    async def _run_turn(
        self,
        turn: PublicTurn,
        prompt: str,
        *,
        session_execution: ActiveSessionExecution,
        params: TurnStartParams,
        images: list[ImageAttachment],
        input_text: str | None,
        resources: list[UserResource],
    ) -> None:
        await self._notify(
            "turn/started",
            TurnStartedParams(
                event_id=0, session_id=turn.session_id, turn=turn, emitted_at=now_ms()
            ),
        )
        await self._emit_status("running")
        await self._emit_stats()
        last_context_tokens = self._agent_loop.stats.context_tokens
        status = PublicTurnStatus.COMPLETED
        error: PublicError | None = None
        stop_reason: PublicTurnStopReason | None = None
        try:
            self._record_mentions(params.mention_stats, params.client_user_message_id)
            async with aclosing(
                self._agent_loop.act(
                    prompt,
                    client_message_id=params.client_user_message_id,
                    auto_title=params.auto_title,
                    images=images or None,
                    input_text=input_text,
                    resources=resources or None,
                    user_display_content=params.user_display_content,
                    subagent_runner=self._subagent_runner,
                    tool_io=self._tool_io,
                    turn_options=AgentTurnOptions(
                        retry_sink=self._emit_retrying, injected=params.injected
                    ),
                )
            ) as events:
                async for event in events:
                    await self._clear_retrying()
                    if self._event_sink is not None:
                        await self._event_sink(event)
                    if isinstance(event, ApprovalRequestEvent | UserInputRequestEvent):
                        await self._handle_core_request(event)
                        continue
                    if (
                        isinstance(event, AssistantEvent)
                        and event.stopped_by_middleware
                    ):
                        stop_reason = PublicTurnStopReason.LIMIT
                    await self._project_events([event])
                    context_tokens = self._agent_loop.stats.context_tokens
                    if context_tokens != last_context_tokens:
                        last_context_tokens = context_tokens
                        await self._emit_stats()
                    if isinstance(event, UserMessageEvent) and (
                        loop_id := self._scheduled_loop_id
                    ):
                        self._scheduled_loop_id = None
                        await self._emit_scheduled_loop_notice(loop_id)
        except asyncio.CancelledError:
            status = PublicTurnStatus.INTERRUPTED
        except Exception as exc:
            status = PublicTurnStatus.FAILED
            error = public_error(exc)
        finally:
            try:
                await self._finalize_turn(
                    turn, status, error, stop_reason, session_execution
                )
            finally:
                self._scheduled_loop_id = None
                self._session_execution = None
                self._execution.finish(session_execution)

    async def _emit_scheduled_loop_notice(self, loop_id: str) -> None:
        projector = self._projector
        turn = self._active_turn
        if projector is None or turn is None:
            raise RuntimeError("Cannot emit a loop notice without an active turn")
        await self._emit_projected(
            projector.add_notice(
                f"scheduled-loop:{turn.id}",
                message=f"Loop `{loop_id}` fired",
                detail=ScheduledLoopFiredNoticeDetail(loop_id=loop_id),
            )
        )

    async def _finalize_turn(
        self,
        turn: PublicTurn,
        status: PublicTurnStatus,
        error: PublicError | None,
        stop_reason: PublicTurnStopReason | None,
        session_execution: ActiveSessionExecution,
    ) -> None:
        await self._clear_retrying()
        projector = self._projector
        if projector is not None:
            for update in projector.finalize(
                cancelled=status is PublicTurnStatus.INTERRUPTED
            ):
                await self._emit_projected(update)
            self._history.extend(projector.history)
        self._cancel_callbacks("Turn ended")
        completed = turn.model_copy(
            update={
                "status": status,
                "completed_at": now_ms(),
                "error": error,
                "stop_reason": stop_reason,
            }
        )
        self._completed_turns.append(completed)
        await self._emit_stats()
        self._active_turn = None
        self._projector = None
        self._session_execution = None
        await self._emit_status("idle")
        self._execution.finish(session_execution)
        await self._notify(
            "turn/completed",
            TurnCompletedParams(
                event_id=0,
                session_id=turn.session_id,
                turn=completed,
                emitted_at=now_ms(),
            ),
        )

    async def _handle_core_request(
        self, event: ApprovalRequestEvent | UserInputRequestEvent
    ) -> None:
        try:
            match event:
                case ApprovalRequestEvent():
                    await self._request_approval(event)
                case UserInputRequestEvent():
                    await self._request_user_input(event)
        except CallbackRejectedError:
            raise
        except Exception as exc:
            record = self._callbacks.get(event.request_id)
            if record is None:
                self._agent_loop.reject_request(event.request_id, exc)
            else:
                self._reject_callback_record(record, exc)
            raise

    async def _request_approval(self, event: ApprovalRequestEvent) -> None:
        projector = self._projector
        if projector is None:
            raise RuntimeError("Cannot request approval without an active turn")
        detail = ApprovalCallbackDetail(
            effect=projector.effect_detail(event.tool_call_id),
            required_permissions=list(event.required_permissions or []),
            related_entry_id=event.tool_call_id,
        )
        output = await self._open_callback(
            event, detail, title=f"Allow {event.tool_name}?"
        )
        if not isinstance(output, ApprovalCallbackOutput):
            raise RuntimeError("Client returned the wrong approval result")
        decision = output.decision.type
        if decision in {
            ApprovalDecisionType.APPROVE_FOR_SESSION,
            ApprovalDecisionType.APPROVE_PERMANENTLY,
        }:
            await self._agent_loop.approve_always(
                event.tool_name,
                detail.required_permissions or None,
                save_permanently=decision is ApprovalDecisionType.APPROVE_PERMANENTLY,
            )
        approved = decision in {
            ApprovalDecisionType.APPROVE,
            ApprovalDecisionType.APPROVE_FOR_SESSION,
            ApprovalDecisionType.APPROVE_PERMANENTLY,
        }
        feedback = output.feedback
        if not approved and feedback is None:
            feedback = str(
                get_user_cancellation_message(CancellationReason.OPERATION_CANCELLED)
            )
        self._agent_loop.resolve_approval_request(
            event.request_id,
            ApprovalResponse.YES if approved else ApprovalResponse.NO,
            feedback,
        )
        self._callbacks[event.request_id].core_resolved = True
        if decision is ApprovalDecisionType.CANCEL_TURN and self._active_task:
            self._active_task.cancel()

    async def _request_user_input(self, event: UserInputRequestEvent) -> None:
        if not isinstance(event.args, UserQuestionRequest):
            raise TypeError(
                f"Unsupported user-input request: {type(event.args).__name__}"
            )
        output = await self._open_callback(
            event,
            UserInputCallbackDetail(
                request=event.args, related_entry_id=event.tool_call_id
            ),
            title="User input required",
        )
        if not isinstance(output, UserInputCallbackOutput):
            raise RuntimeError("Client returned the wrong user-input result")
        self._agent_loop.resolve_user_input_request(event.request_id, output.result)
        self._callbacks[event.request_id].core_resolved = True

    async def _open_callback(
        self,
        event: ApprovalRequestEvent | UserInputRequestEvent,
        detail: ApprovalCallbackDetail | UserInputCallbackDetail,
        *,
        title: str,
    ) -> CallbackOutput:
        projector = self._projector
        if projector is None or self._active_turn is None:
            raise RuntimeError("Cannot open callback without an active turn")
        callback_id = event.request_id
        record = CallbackRecord(
            event=event, future=asyncio.get_running_loop().create_future()
        )
        if callback_id in self._callbacks:
            raise RuntimeError(f"Duplicate callback request: {callback_id}")
        self._callbacks[callback_id] = record
        for update in projector.open_callback(callback_id, detail, title):
            await self._emit_projected(update)
        entry = next(
            cast(PublicCallbackEntry, entry)
            for entry in projector.history
            if isinstance(entry, PublicCallbackEntry)
            and entry.callback_id == callback_id
        )
        await self._emit_status("blocked", callback=entry)
        await self._deliver_callback(entry)
        return await record.future

    async def _project_events(
        self,
        events: list[BaseEvent],
        *,
        user_message_source: PublicMessageSource = "turn_start",
    ) -> None:
        projector = self._projector
        if projector is None:
            return
        for event in events:
            await self._handoff_session_if_needed(projector, event)
            updates = projector.project(event, user_message_source=user_message_source)
            if isinstance(event, AgentProfileChangedEvent):
                active, _ = project_agents(self._agent_loop)
                updates.append(projector.project_agent(active))
            for update in updates:
                await self._emit_projected(update)

    async def _handoff_session_if_needed(
        self, projector: EventProjector, event: BaseEvent
    ) -> None:
        old_session_id = projector.session_id
        new_session_id = self._agent_loop.session_id
        if old_session_id == new_session_id:
            return
        coordinator = self._session_coordinator
        turn = self._active_turn
        if coordinator is None or turn is None:
            raise RuntimeError("Core changed session identity without a coordinator")
        if not isinstance(event, CompactEndEvent | ContextClearedEvent):
            raise RuntimeError(
                f"Core changed session identity before {type(event).__name__}"
            )
        self._history = rebind_history(self._history, new_session_id)
        projector.rebind_session(new_session_id)
        turn.session_id = new_session_id
        handoff = await coordinator.handoff_active_turn(
            old_session_id,
            current_history=self.history,
            callbacks=self.callbacks,
            active_turn=turn,
            completed_turns=self.completed_turns,
        )
        common = {
            "event_id": 0,
            "session_id": handoff.new_session_id,
            "old_session_id": handoff.old_session_id,
            "state": handoff.state,
            "session_log": handoff.session_log,
            "emitted_at": now_ms(),
        }
        match event:
            case CompactEndEvent():
                await self._notify(
                    "session/compacted",
                    SessionCompactedParams(
                        **common, summary_length=event.summary_length
                    ),
                )
            case ContextClearedEvent():
                await self._notify(
                    "session/contextCleared",
                    SessionContextClearedParams(
                        **common,
                        plan_file_path=(
                            str(event.plan_file_path)
                            if event.plan_file_path is not None
                            else None
                        ),
                    ),
                )
        await self._emit_stats()

    async def _emit_projected(self, update: ProjectedUpdate) -> None:
        await self._notify(update.method, update.params)

    async def _emit_retrying(self, reason: RetryReason) -> None:
        turn = self._active_turn
        if turn is None or _retry_turn_id.get() != turn.id:
            return
        retrying = PublicRetryState(
            turn_id=turn.id,
            category=_public_retry_category(reason.category),
            detail=reason.detail,
        )
        self._retrying = retrying
        await self._emit_retry_snapshot()
        await self._notify(
            "turn/retrying",
            TurnRetryingParams(
                session_id=self._agent_loop.session_id,
                category=retrying.category,
                detail=retrying.detail,
            ),
        )

    async def _clear_retrying(self) -> None:
        if self._retrying is None:
            return
        self._retrying = None
        await self._emit_retry_snapshot()

    async def _emit_retry_snapshot(self) -> None:
        state = self._snapshot_state()
        await self._notify(
            "session/snapshot",
            SessionSnapshotParams(
                event_id=0,
                session_id=state.session.id,
                state=state,
                emitted_at=now_ms(),
            ),
        )

    async def _emit_stats(self) -> None:
        try:
            context_window = (
                self._agent_loop.config.get_active_model().auto_compact_threshold
            )
        except ValueError:
            context_window = 0
        await self._notify(
            "session/statsUpdated",
            StatsUpdatedParams(
                event_id=0,
                session_id=self._agent_loop.session_id,
                stats=project_stats(self._agent_loop),
                context_window=context_window,
                emitted_at=now_ms(),
            ),
        )

    async def _emit_status(
        self, status: str, *, callback: PublicCallbackEntry | None = None
    ) -> None:
        if self._active_turn is None and status != "idle":
            return
        if status == "running":
            value: JsonValue = {
                "type": "running",
                "activeTurnId": cast(PublicTurn, self._active_turn).id,
            }
        elif status == "blocked" and callback is not None:
            value = {
                "type": "blocked",
                "activeTurnId": cast(PublicTurn, self._active_turn).id,
                "callbackId": callback.callback_id,
                "reason": callback.detail.kind,
            }
        else:
            value = {"type": "idle"}
        await self._notify(
            "session/updated",
            SessionUpdatedParams(
                event_id=0,
                session_id=self._agent_loop.session_id,
                patch=[
                    JsonPatchOperation(op="replace", path="/status", value=value),
                    JsonPatchOperation(op="replace", path="/updatedAt", value=now_ms()),
                ],
                emitted_at=now_ms(),
            ),
        )

    def _require_active_turn(self, turn_id: str) -> PublicTurn:
        if self._active_turn is None:
            raise TurnConflictError("No active turn")
        if self._active_turn.id != turn_id:
            raise StaleTurnError(self._active_turn.id)
        return self._active_turn

    def _require_harness_effect(self, entry_id: str) -> EventProjector:
        projector = self._harness_effects.get(entry_id)
        if projector is None:
            raise ValueError(f"Harness effect not found: {entry_id}")
        return projector

    def _record_mentions(
        self, stats: MentionStats | None, message_id: str | None
    ) -> None:
        if stats is None or stats.count == 0:
            return
        self._agent_loop.telemetry_client.send_at_mention_inserted(
            nb_mentions=stats.count,
            context_types=stats.context_types,
            file_extensions=stats.file_extensions or None,
            message_id=message_id,
        )

    def _cancel_callbacks(self, reason: str) -> None:
        for record in self._callbacks.values():
            self._reject_callback_record(record, CallbackRejectedError(reason))

    def _reject_callback_record(
        self, record: CallbackRecord, error: BaseException
    ) -> None:
        if record.core_resolved:
            return
        record.core_resolved = True
        self._agent_loop.reject_request(record.event.request_id, error)
        if not record.future.done():
            record.future.set_exception(error)
