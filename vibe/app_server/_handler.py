from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._execution import (
    SessionExecution,
    SessionExecutionConflict,
    SessionExecutionKind,
)
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._projection import (
    history_user_message_index,
    project_history,
    project_session_log,
)
from vibe.app_server._resources import ResourceRequestHandler
from vibe.app_server._review import ReviewRequestHandler
from vibe.app_server._root_session import RootSessionCoordinator
from vibe.app_server._runtime import (
    AgentRuntimeFactory,
    RuntimeSessionNotFoundError,
    close_agent_loop,
)
from vibe.app_server._sessions import SessionRuntimeRegistry
from vibe.app_server._shell import ShellConflictError
from vibe.app_server._shell_requests import ShellRequestHandler
from vibe.app_server._state import build_public_state, history_page
from vibe.app_server._turns import (
    CallbackClosedError,
    CallbackConflictError,
    CallbackNotFoundError,
    StaleTurnError,
    TurnConflictError,
    TurnController,
)
from vibe.app_server._vibe_code import (
    VibeCodeAccessError,
    VibeCodeConflictError,
    VibeCodeController,
    VibeCodeError,
)
from vibe.app_server._workspace import (
    PromptPreparationError,
    WorkspaceTrustError,
    decide_workspace_trust,
    prepare_prompt,
)
from vibe.app_server.models import (
    CallbackOutput,
    PublicCallbackEntry,
    PublicHistoryEntry,
    PublicSessionState,
)
from vibe.app_server.protocol import (
    AgentSwitchParams,
    CallbackRespondParams,
    CallbackRespondResponse,
    CallbackResultError,
    CallbackResultParams,
    CallbackResultResponse,
    ContextInjectParams,
    EmptyResponse,
    ProtocolErrorCode,
    RuntimeMutationResponse,
    RuntimeUpdatedParams,
    SessionCompactParams,
    SessionCompactResponse,
    SessionContinueParams,
    SessionContinueResponse,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryClearParams,
    SessionHistoryClearResponse,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionKind,
    SessionLogReadParams,
    SessionLogReadResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionReadyReadParams,
    SessionReadyReadResponse,
    SessionReadyWaitParams,
    SessionReadyWaitResponse,
    SessionResumeParams,
    SessionResumeResponse,
    SessionRewindParams,
    SessionRewindReadParams,
    SessionRewindReadResponse,
    SessionRewindResponse,
    SessionSettingsUpdateParams,
    SessionStartParams,
    SessionStartResponse,
    SessionStopParams,
    SessionStopResponse,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    TeleportCancelParams,
    TeleportCancelResponse,
    TeleportPushRespondParams,
    TeleportStartParams,
    TeleportStartResponse,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
    VibeCodeProjectCancelParams,
    VibeCodeProjectCreateParams,
    VibeCodeProjectCreateResponse,
    VibeCodeProjectRecoverParams,
    VibeCodeProjectRecoverResponse,
    VibeCodeProjectSelectParams,
    VibeCodeProjectSelectResponse,
    VibeCodeProjectsLoadMoreParams,
    VibeCodeProjectsLoadMoreResponse,
    VibeCodeProjectsOpenParams,
    VibeCodeProjectsOpenResponse,
    VibeCodeProjectUnlinkParams,
    VibeCodeProjectUnlinkResponse,
    WorkspacePromptPrepareParams,
    WorkspacePromptPrepareResponse,
    WorkspaceTrustDecisionParams,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.compaction import CompactionFailedError
from vibe.core.session.saved_sessions import update_saved_session_title_at_path
from vibe.observability.logging import logger

DEFAULT_HISTORY_LIMIT = 200

type ReplaceRoot = Callable[[str, int], Awaitable[PublicSessionState]]
type AdoptRoot = Callable[[AgentLoop, int], Awaitable[PublicSessionState]]
type StageRoot = Callable[[AgentLoop], Awaitable[None]]
type SpawnResumeTask = Callable[[asyncio.Task[None]], None]


@dataclass(frozen=True, slots=True)
class RootLifecycle:
    replace: ReplaceRoot
    adopt: AdoptRoot
    stage: StageRoot | None = None


@dataclass(frozen=True, slots=True)
class ResumeOrchestration:
    """Server-owned callbacks that manage the fast-resume background lifecycle.

    Grouped separately from ``RootLifecycle`` because they are server concerns
    (task tracking, post-response notification) rather than session-state concerns.
    """

    runtime_factory: AgentRuntimeFactory
    current_event_id: Callable[[str], int]
    spawn_resume_task: SpawnResumeTask


class CoreRequestHandler:
    def __init__(
        self,
        agent_loop: AgentLoop,
        turns: TurnController,
        execution: SessionExecution,
        notify: Callable[[str, ProtocolModel], Awaitable[None]],
        sessions: SessionRuntimeRegistry,
        resources: ResourceRequestHandler,
        root_session: RootSessionCoordinator,
        root_lifecycle: RootLifecycle,
        resume_orchestration: ResumeOrchestration,
    ) -> None:
        self._agent_loop = agent_loop
        self._turns = turns
        self._execution = execution
        self._notify = notify
        self._sessions = sessions
        self._root_session = root_session
        self._current_event_id = resume_orchestration.current_event_id
        self._shell = ShellRequestHandler(
            agent_loop, turns, execution, self._require_attached, self._current_event_id
        )
        self._vibe_code = VibeCodeController(
            agent_loop, notify, execution, resources.read_account
        )
        self._resources = resources
        self._review = ReviewRequestHandler(
            agent_loop.review_manager,
            self._require_session,
            self._execution.require_idle,
        )
        self._root_lifecycle = root_lifecycle
        self._runtime_factory = resume_orchestration.runtime_factory
        self._closed = False
        self._spawn_resume_task = resume_orchestration.spawn_resume_task

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        try:
            active = self._execution.active
            if active is not None and active.kind is SessionExecutionKind.LIFECYCLE:
                raise SessionExecutionConflict(
                    f"Session lifecycle transition is active: {active.id}"
                )
            return await self._dispatch(method, raw_params)
        except TurnConflictError as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except StaleTurnError as exc:
            raise RequestFailure(
                ProtocolErrorCode.STALE_TURN,
                str(exc),
                data={"activeTurnId": exc.active_turn_id},
            ) from exc
        except CallbackNotFoundError as exc:
            raise RequestFailure(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
        except CallbackClosedError as exc:
            raise RequestFailure(ProtocolErrorCode.CALLBACK_CLOSED, str(exc)) from exc
        except CallbackConflictError as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except (PromptPreparationError, WorkspaceTrustError) as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        except ShellConflictError as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except VibeCodeConflictError as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except VibeCodeAccessError as exc:
            raise RequestFailure(ProtocolErrorCode.FORBIDDEN, str(exc)) from exc
        except VibeCodeError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        except SessionExecutionConflict as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._shell.close()
        await self._vibe_code.close()

    async def _dispatch(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        namespace = method.partition("/")[0]
        match namespace:
            case "session":
                result = await self._dispatch_session(method, raw_params)
            case "turn":
                result = await self._dispatch_turn(method, raw_params)
            case "workspace" | "vibeCode":
                result = await self._dispatch_product(method, raw_params)
            case "callback":
                result = await self._dispatch_callback(method, raw_params)
            case "plugin":
                # Reserved for future clients; Vibe has no plugin backing yet.
                raise RequestFailure(
                    ProtocolErrorCode.NOT_IMPLEMENTED,
                    f"Plugins are not supported: {method}",
                )
            case "review":
                result = self._review.dispatch(method, raw_params)
            case (
                "account"
                | "identity"
                | "runtime"
                | "config"
                | "agents"
                | "skills"
                | "tools"
                | "stats"
                | "diagnostics"
                | "connectors"
                | "mcp"
                | "loops"
                | "telemetry"
                | "narration"
                | "feedback"
            ):
                result = await self._resources.dispatch(method, raw_params)
            case _:
                raise method_not_found(method)
        return result

    async def _dispatch_product(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method.startswith("workspace/"):
            return await self._dispatch_workspace(method, raw_params)
        return await self._dispatch_vibe_code(method, raw_params)

    async def _dispatch_vibe_code(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method.startswith("vibeCode/projects/"):
            return await self._dispatch_vibe_code_projects(method, raw_params)
        return await self._dispatch_teleport(method, raw_params)

    async def _dispatch_vibe_code_projects(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "vibeCode/projects/open":
                params = validate_wire(VibeCodeProjectsOpenParams, raw_params)
                self._require_session(params.session_id)
                picker_id, view, project_id = await self._vibe_code.open(
                    purpose=params.purpose, prompt=params.prompt
                )
                response: ProtocolModel = VibeCodeProjectsOpenResponse(
                    picker_id=picker_id, view=view, resolved_project_id=project_id
                )
            case "vibeCode/projects/loadMore":
                params = validate_wire(VibeCodeProjectsLoadMoreParams, raw_params)
                self._require_session(params.session_id)
                view, focus = await self._vibe_code.load_more(params.picker_id)
                response = VibeCodeProjectsLoadMoreResponse(
                    view=view, focus_option_id=focus
                )
            case "vibeCode/projects/create":
                params = validate_wire(VibeCodeProjectCreateParams, raw_params)
                self._require_session(params.session_id)
                view, project = await self._vibe_code.create(
                    picker_id=params.picker_id,
                    name=params.name,
                    default_branch=params.default_branch,
                )
                response = VibeCodeProjectCreateResponse(view=view, project=project)
            case "vibeCode/projects/select":
                params = validate_wire(VibeCodeProjectSelectParams, raw_params)
                self._require_session(params.session_id)
                view, project = await self._vibe_code.select(
                    picker_id=params.picker_id, project_id=params.project_id
                )
                response = VibeCodeProjectSelectResponse(view=view, project=project)
            case "vibeCode/projects/unlink":
                params = validate_wire(VibeCodeProjectUnlinkParams, raw_params)
                self._require_session(params.session_id)
                view = await self._vibe_code.unlink(params.picker_id)
                response = VibeCodeProjectUnlinkResponse(view=view)
            case "vibeCode/projects/cancel":
                params = validate_wire(VibeCodeProjectCancelParams, raw_params)
                self._require_session(params.session_id)
                await self._vibe_code.cancel_picker(params.picker_id)
                response = EmptyResponse()
            case "vibeCode/projects/recover":
                params = validate_wire(VibeCodeProjectRecoverParams, raw_params)
                self._require_session(params.session_id)
                view, recovered = await self._vibe_code.recover_stale_link(
                    params.picker_id
                )
                response = VibeCodeProjectRecoverResponse(
                    recovered=recovered, view=view
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _dispatch_teleport(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        after_response: Callable[[], None] | None = None
        match method:
            case "vibeCode/teleport/start":
                params = validate_wire(TeleportStartParams, raw_params)
                self._require_attached(params.session_id)
                await self._vibe_code.reserve_teleport(params)
                response = TeleportStartResponse(operation_id=params.operation_id)
                after_response = lambda: self._vibe_code.start_teleport(params)
            case "vibeCode/teleport/cancel":
                params = validate_wire(TeleportCancelParams, raw_params)
                self._require_attached(params.session_id)
                response = TeleportCancelResponse(
                    cancelled=await self._vibe_code.cancel_teleport(params.operation_id)
                )
            case "vibeCode/teleport/push/respond":
                params = validate_wire(TeleportPushRespondParams, raw_params)
                self._require_attached(params.session_id)
                self._vibe_code.respond_to_push(params.operation_id, params.approved)
                response = EmptyResponse()
            case _:
                raise method_not_found(method)
        return DispatchResult(response, after_response)

    async def _dispatch_session(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method == "session/shellCommand":
            return await self._shell.dispatch(method, raw_params)
        if method in {
            "session/start",
            "session/resume",
            "session/continue",
            "session/stop",
        }:
            return await self._dispatch_session_lifecycle(method, raw_params)
        if (
            method == "session/read"
            or method.startswith("session/rewind")
            or method
            in {
                "session/history/list",
                "session/turns/list",
                "session/rename",
                "session/compact",
            }
        ):
            return await self._dispatch_session_delegated(method, raw_params)
        runtime_updated = False
        session_attached = False
        match method:
            case "session/ready/wait":
                response: ProtocolModel = await self._wait_ready(
                    validate_wire(SessionReadyWaitParams, raw_params)
                )
            case "session/ready/read":
                params = validate_wire(SessionReadyReadParams, raw_params)
                self._require_session(params.session_id)
                response = SessionReadyReadResponse(
                    ready=self._agent_loop.is_initialized
                )
            case "session/fork":
                params = validate_wire(SessionForkParams, raw_params)
                response = await self._session_fork(params)
                session_attached = params.attach
            case "session/agent/update":
                response = await self._agent_switch(
                    validate_wire(AgentSwitchParams, raw_params)
                )
                runtime_updated = True
            case "session/settings/update":
                response = self._session_settings_update(
                    validate_wire(SessionSettingsUpdateParams, raw_params)
                )
            case "session/log/read":
                response = self._session_log_read(
                    validate_wire(SessionLogReadParams, raw_params)
                )
            case "session/context/inject":
                params = validate_wire(ContextInjectParams, raw_params)
                self._require_attached(params.session_id)
                response = await self._turns.inject(params)
            case "session/history/clear":
                response = await self._history_clear(
                    validate_wire(SessionHistoryClearParams, raw_params)
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(
            response, runtime_updated=runtime_updated, session_attached=session_attached
        )

    async def _dispatch_session_delegated(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method == "session/read":
            return await self._dispatch_session_records(method, raw_params)
        if method.startswith("session/rewind"):
            return await self._dispatch_rewind(method, raw_params)
        if method == "session/rename":
            return DispatchResult(
                await self._session_rename(
                    validate_wire(SessionTitleUpdateParams, raw_params)
                )
            )
        if method == "session/compact":
            return DispatchResult(
                await self._compact(validate_wire(SessionCompactParams, raw_params))
            )
        return await self._dispatch_session_catalog(method, raw_params)

    async def _dispatch_session_catalog(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "session/history/list":
                return DispatchResult(
                    await self._history_list(
                        validate_wire(SessionHistoryListParams, raw_params)
                    )
                )
            case "session/turns/list":
                return DispatchResult(
                    await self._session_turns_list(
                        validate_wire(SessionTurnsListParams, raw_params)
                    )
                )
        raise method_not_found(method)

    async def _dispatch_session_records(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "session/read":
                params = validate_wire(SessionReadParams, raw_params)
                if await self._sessions.ensure_child(params.session_id):
                    public = self._sessions.public_state(
                        params.session_id,
                        params.history_limit,
                        turns_limit=params.turns_limit,
                        include_history=params.include_history,
                        include_turns=params.include_turns,
                    )
                else:
                    self._require_session(params.session_id)
                    public = self._public_state(
                        params.history_limit,
                        turns_limit=params.turns_limit,
                        include_history=params.include_history,
                        include_turns=params.include_turns,
                    )
                response: ProtocolModel = SessionReadResponse(
                    state=public, last_event_id=public.event_id
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _dispatch_session_lifecycle(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method == "session/start":
            params = validate_wire(SessionStartParams, raw_params)
            if self._root_session.attached_session_id is not None:
                raise RequestFailure(
                    ProtocolErrorCode.CONFLICT, "A session is already attached"
                )
            if (
                params.cwd is not None
                and Path(params.cwd).resolve() != self._agent_loop.cwd
            ):
                raise RequestFailure(
                    ProtocolErrorCode.INVALID_PARAMS,
                    "The app server was started in a different working directory",
                )
            self._root_session.attach(self._agent_loop.session_id)
            self._agent_loop.start_initialize_experiments(
                defer_new_session_telemetry=params.kind is SessionKind.EPHEMERAL
            )
            return DispatchResult(
                SessionStartResponse(
                    state=(state := self._public_state(params.history_limit)),
                    last_event_id=state.event_id,
                ),
                session_attached=True,
            )
        if method == "session/resume":
            return await self._session_resume(
                validate_wire(SessionResumeParams, raw_params)
            )
        if method == "session/continue":
            params = validate_wire(SessionContinueParams, raw_params)
            if self._root_session.attached_session_id is not None:
                raise RequestFailure(
                    ProtocolErrorCode.CONFLICT, "A session is already attached"
                )
            cwd = (
                Path(params.cwd).resolve()
                if params.cwd is not None
                else self._agent_loop.cwd
            )
            try:
                session_id = self._runtime_factory.resolve_latest(self._agent_loop, cwd)
            except RuntimeSessionNotFoundError as exc:
                raise RequestFailure(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
            state = await self._root_lifecycle.replace(session_id, params.history_limit)
            return DispatchResult(
                SessionContinueResponse(state=state, last_event_id=state.event_id),
                session_attached=True,
                runtime_updated=True,
            )
        if method == "session/stop":
            stop_params = validate_wire(SessionStopParams, raw_params)
            self._require_attached(stop_params.session_id)
            await self.close()
            return DispatchResult(SessionStopResponse())
        raise method_not_found(method)

    async def _dispatch_rewind(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "session/rewind/read":
                response: ProtocolModel = self._rewind_read(
                    validate_wire(SessionRewindReadParams, raw_params)
                )
            case "session/rewind":
                response = await self._rewind(
                    validate_wire(SessionRewindParams, raw_params)
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _dispatch_turn(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        response: ProtocolModel
        match method:
            case "turn/start":
                start_params = validate_wire(TurnStartParams, raw_params)
                self._require_attached(start_params.session_id)
                vibe_turn, after_response = self._turns.start(start_params)
                response = TurnStartResponse(
                    turn=vibe_turn.turn,
                    last_event_id=self._current_event_id(start_params.session_id),
                )
            case "turn/steer":
                steer_params = validate_wire(TurnSteerParams, raw_params)
                self._require_turn_route(
                    steer_params.session_id, steer_params.expected_turn_id
                )
                await self._turns.steer(steer_params)
                response = TurnSteerResponse(
                    last_event_id=self._current_event_id(steer_params.session_id)
                )
                after_response = None
            case "turn/interrupt":
                params = validate_wire(TurnInterruptParams, raw_params)
                self._require_turn_route(params.session_id, params.expected_turn_id)
                self._turns.interrupt(params)
                response = TurnInterruptResponse(
                    last_event_id=self._current_event_id(params.session_id)
                )
                after_response = None
            case _:
                raise method_not_found(method)
        return DispatchResult(response, after_response)

    async def _dispatch_workspace(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "workspace/prompt/prepare":
                params = validate_wire(WorkspacePromptPrepareParams, raw_params)
                self._require_session(params.session_id)
                prompt = await asyncio.to_thread(
                    prepare_prompt,
                    self._agent_loop,
                    params.message,
                    params.title_content,
                )
                response: ProtocolModel = WorkspacePromptPrepareResponse(prompt=prompt)
                runtime_updated = False
            case "workspace/trust/decision":
                params = validate_wire(WorkspaceTrustDecisionParams, raw_params)
                response = await self._workspace_trust_decision(params)
                runtime_updated = params.decision in {"trust_repo", "trust_cwd"}
            case _:
                raise method_not_found(method)
        return DispatchResult(response, runtime_updated=runtime_updated)

    async def _workspace_trust_decision(
        self, params: WorkspaceTrustDecisionParams
    ) -> ProtocolModel:
        if params.session_id is None:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                "Active workspace trust decisions require a session ID",
            )
        grant = params.decision in {"trust_repo", "trust_cwd"}
        self._require_session(params.session_id)
        if grant:
            self._execution.require_idle()

        cwd = Path(params.cwd) if params.cwd is not None else self._agent_loop.cwd
        response = await asyncio.to_thread(
            decide_workspace_trust,
            cwd,
            params.decision,
            self._agent_loop.harness_files.trust_store,
        )
        if not grant:
            return response

        await self._agent_loop.config_orchestrator.reload()
        await self._agent_loop.reload_with_initial_messages(reload_hooks=True)
        return response

    async def _dispatch_callback(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method == "callback/result":
            return DispatchResult(
                await self._callback_result(
                    validate_wire(CallbackResultParams, raw_params)
                )
            )
        raise method_not_found(method)

    async def _callback_result(
        self, params: CallbackResultParams
    ) -> CallbackResultResponse:
        if params.result.error is not None:
            await self._reject_callback(
                params.session_id, params.callback_id, params.result.error
            )
        elif params.result.output is None:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                "Callback result must include output or error",
            )
        else:
            await self._callback_respond(
                CallbackRespondParams(
                    session_id=params.session_id,
                    callback_id=params.callback_id,
                    output=TypeAdapter(CallbackOutput).validate_python(
                        params.result.output
                    ),
                )
            )
        return CallbackResultResponse(
            last_event_id=self._current_event_id(params.session_id)
        )

    async def _session_resume(self, params: SessionResumeParams) -> DispatchResult:
        if params.session_id != self._agent_loop.session_id:
            state = await self._root_lifecycle.replace(
                params.session_id, params.history_limit
            )
            agent_loop = self._agent_loop
            session_id = params.session_id

            def _spawn() -> None:
                task = asyncio.create_task(self._finish_resume(agent_loop, session_id))
                self._spawn_resume_task(task)

            return DispatchResult(
                SessionResumeResponse(state=state, last_event_id=state.event_id),
                after_response=_spawn,
                session_attached=True,
                runtime_updated=True,
            )
        self._root_session.attach(params.session_id)
        return DispatchResult(
            SessionResumeResponse(
                state=(state := self._public_state(params.history_limit)),
                last_event_id=state.event_id,
            ),
            session_attached=True,
            runtime_updated=True,
        )

    async def _finish_resume(self, agent_loop: AgentLoop, session_id: str) -> None:
        await self._runtime_factory.finish_resume_root(agent_loop, session_id)
        try:
            await self._notify(
                "runtime/updated",
                RuntimeUpdatedParams(
                    session_id=session_id, runtime=self._resources.runtime_snapshot()
                ),
            )
        except Exception:
            logger.exception(
                "Failed to emit runtime/updated after resuming session_id=%s",
                session_id,
            )

    async def _session_title_update(
        self, params: SessionTitleUpdateParams
    ) -> SessionTitleUpdateResponse:
        self._require_session(params.session_id)
        session_logger = self._agent_loop.session_logger
        updated_at: str | None = None
        if (
            session_logger.session_dir is not None
            and session_logger.metadata_filepath.exists()
        ):
            try:
                metadata = await update_saved_session_title_at_path(
                    session_logger.session_dir, params.title
                )
            except ValueError as exc:
                raise RequestFailure(
                    ProtocolErrorCode.INVALID_PARAMS, str(exc)
                ) from exc
            value = metadata.get("end_time")
            updated_at = value if isinstance(value, str) else None
        try:
            session_logger.set_title(params.title)
        except ValueError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        title = session_logger.title
        if title is None:
            raise RuntimeError("The session title was not updated")
        if updated_at is None and session_logger.session_metadata is not None:
            updated_at = session_logger.session_metadata.end_time
        return SessionTitleUpdateResponse(title=title, updated_at=updated_at)

    async def _session_rename(
        self, params: SessionTitleUpdateParams
    ) -> SessionTitleUpdateResponse:
        return await self._session_title_update(params)

    async def _session_fork(self, params: SessionForkParams) -> SessionForkResponse:
        self._require_attached(params.source_session_id)
        if (
            not params.attach
            and self._root_lifecycle.stage is None
            and not self._agent_loop.session_logger.enabled
        ):
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT,
                "Detached forks require session logging to be enabled",
            )

        message_id: str | None = None
        if params.entry_id is not None:
            index = history_user_message_index(self._agent_loop, params.entry_id)
            if index is None:
                raise RequestFailure(
                    ProtocolErrorCode.NOT_FOUND,
                    f"Forkable history entry not found: {params.entry_id}",
                )
            message_id = self._agent_loop.messages[index].message_id
            if message_id is None:
                raise RequestFailure(
                    ProtocolErrorCode.CONFLICT,
                    "The selected history entry has no stable source message ID",
                )

        with self._execution.reserve(
            SessionExecutionKind.LIFECYCLE, f"fork:{params.source_session_id}"
        ):
            try:
                forked: AgentLoop | None = await self._runtime_factory.fork(
                    self._agent_loop, message_id
                )
            except ValueError as exc:
                raise RequestFailure(
                    ProtocolErrorCode.INVALID_PARAMS, str(exc)
                ) from exc

            if params.attach:
                state = await self._root_lifecycle.adopt(forked, params.history_limit)
                return SessionForkResponse(
                    source_session_id=params.source_session_id,
                    state=state,
                    last_event_id=state.event_id,
                )

            try:
                history = project_history(forked)
                state = build_public_state(
                    forked,
                    history=history,
                    current_history=[],
                    callbacks=[],
                    active_turn=None,
                    completed_turns=[],
                    history_limit=params.history_limit,
                )
                if (
                    self._root_lifecycle.stage is not None
                    and not forked.session_logger.enabled
                ):
                    await self._root_lifecycle.stage(forked)
                    forked = None
            finally:
                if forked is not None:
                    await close_agent_loop(forked)

        return SessionForkResponse(
            source_session_id=params.source_session_id,
            state=state,
            last_event_id=state.event_id,
        )

    def _rewind_read(
        self, params: SessionRewindReadParams
    ) -> SessionRewindReadResponse:
        self._require_session(params.session_id)
        index = self._rewind_index(params.entry_id)
        paths = self._agent_loop.rewind_manager.restorable_paths_at(index)
        return SessionRewindReadResponse(has_file_changes=bool(paths), paths=paths)

    async def _rewind(self, params: SessionRewindParams) -> SessionRewindResponse:
        self._require_attached(params.session_id)
        index = self._rewind_index(params.entry_id)
        history = self._all_history()
        history_index = next(
            (
                position
                for position, entry in enumerate(history)
                if entry.id == params.entry_id
            ),
            None,
        )
        if history_index is None:
            raise RuntimeError(
                f"Rewindable core message is missing from public history: {params.entry_id}"
            )
        with self._execution.reserve(
            SessionExecutionKind.LIFECYCLE, f"rewind:{params.entry_id}"
        ):
            (
                message,
                restore_errors,
                restored_paths,
            ) = await self._agent_loop.rewind_manager.rewind_to_message(
                index, restore_files=params.restore_files, inplace=params.inplace
            )
            await self._turns.reset()
            handoff = self._root_session.replace_idle_with_history(
                params.session_id,
                history=history[:history_index],
                checkpoint_kind="rewind",
                checkpoint_message="Conversation rewound",
                checkpoint_details={
                    "entryId": params.entry_id,
                    "restoreFiles": params.restore_files,
                    "inplace": params.inplace,
                },
            )
        return SessionRewindResponse(
            message=message,
            restore_errors=restore_errors,
            restored_paths=restored_paths,
            state=handoff.state,
            session_log=handoff.session_log,
        )

    def _rewind_index(self, entry_id: str) -> int:
        index = history_user_message_index(self._agent_loop, entry_id)
        if index is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND,
                f"Rewindable history entry not found: {entry_id}",
            )
        return index

    async def _history_list(
        self, params: SessionHistoryListParams
    ) -> SessionHistoryListResponse:
        if await self._sessions.ensure_child(params.session_id):
            history = self._sessions.history(params.session_id)
        else:
            self._require_session(params.session_id)
            history = self._all_history()
        page = history_page(
            history,
            turn_id=params.turn_id,
            before=params.cursor if params.sort_direction == "backward" else None,
            after=params.cursor if params.sort_direction == "forward" else None,
            limit=params.limit,
        )
        return SessionHistoryListResponse(
            items=page.entries,
            next_cursor=(
                page.cursor.before
                if params.sort_direction == "backward"
                else page.cursor.after
            ),
            previous_cursor=(
                page.cursor.after
                if params.sort_direction == "backward"
                else page.cursor.before
            ),
        )

    async def _session_turns_list(
        self, params: SessionTurnsListParams
    ) -> SessionTurnsListResponse:
        if await self._sessions.ensure_child(params.session_id):
            turns = self._sessions.turns(params.session_id)
        else:
            self._require_session(params.session_id)
            turns = self._turns.turns
        if params.sort_direction == "backward":
            if params.cursor is None:
                page = turns[-params.limit :]
                first_index = max(0, len(turns) - len(page))
            else:
                end = next(
                    (
                        index
                        for index, turn in enumerate(turns)
                        if turn.id == params.cursor
                    ),
                    0,
                )
                first_index = max(0, end - params.limit)
                page = turns[first_index:end]
            last_index = first_index + len(page) - 1
        else:
            first_index = (
                0
                if params.cursor is None
                else next(
                    (
                        index + 1
                        for index, turn in enumerate(turns)
                        if turn.id == params.cursor
                    ),
                    len(turns),
                )
            )
            page = turns[first_index : first_index + params.limit]
            last_index = first_index + len(page) - 1
        next_cursor = page[0].id if page and first_index > 0 else None
        backwards_cursor = page[-1].id if page and last_index < len(turns) - 1 else None
        if params.sort_direction == "forward":
            next_cursor, backwards_cursor = backwards_cursor, next_cursor
        return SessionTurnsListResponse(
            items=page, next_cursor=next_cursor, previous_cursor=backwards_cursor
        )

    async def _wait_ready(
        self, params: SessionReadyWaitParams
    ) -> SessionReadyWaitResponse:
        self._require_session(params.session_id)
        await self._agent_loop.wait_until_ready()
        return SessionReadyWaitResponse(
            init_duration_ms=self._agent_loop.init_duration_ms
        )

    async def _agent_switch(self, params: AgentSwitchParams) -> RuntimeMutationResponse:
        active = self._execution.active
        if active is not None and active.kind is not SessionExecutionKind.TURN:
            self._execution.require_idle()
        self._require_session(params.session_id)
        await self._agent_loop.switch_agent(params.agent_name)
        return RuntimeMutationResponse(runtime=self._resources.runtime_snapshot())

    def _session_settings_update(
        self, params: SessionSettingsUpdateParams
    ) -> EmptyResponse:
        self._require_session(params.session_id)
        if params.max_turns is not None:
            self._agent_loop.set_max_turns(params.max_turns)
        if params.max_tokens is not None:
            self._agent_loop.set_max_tokens(params.max_tokens)
        return EmptyResponse()

    def _session_log_read(self, params: SessionLogReadParams) -> SessionLogReadResponse:
        self._require_session(params.session_id)
        return SessionLogReadResponse(log=project_session_log(self._agent_loop))

    async def _callback_respond(
        self, params: CallbackRespondParams
    ) -> CallbackRespondResponse:
        if await self._sessions.ensure_child(params.session_id):
            attached = self._root_session.attached_session_id
            if attached is None or not self._sessions.child_belongs_to(
                params.session_id, attached
            ):
                raise RequestFailure(
                    ProtocolErrorCode.CONFLICT,
                    "Child session is not linked to the attached session",
                )
            status = await self._sessions.answer_callback(
                params.session_id, params.callback_id, params.output
            )
            return CallbackRespondResponse.model_validate({"status": status})
        self._require_attached(params.session_id)
        status = await self._turns.answer_callback(params.callback_id, params.output)
        return CallbackRespondResponse.model_validate({"status": status})

    async def _reject_callback(
        self, session_id: str, callback_id: str, error: CallbackResultError
    ) -> str:
        if await self._sessions.ensure_child(session_id):
            attached = self._root_session.attached_session_id
            if attached is None or not self._sessions.child_belongs_to(
                session_id, attached
            ):
                raise RequestFailure(
                    ProtocolErrorCode.CONFLICT,
                    "Child session is not linked to the attached session",
                )
            return await self._sessions.reject_callback(session_id, callback_id, error)
        self._require_attached(session_id)
        return await self._turns.reject_callback(callback_id, error)

    async def _history_clear(
        self, params: SessionHistoryClearParams
    ) -> SessionHistoryClearResponse:
        self._require_session(params.session_id)
        if self._turns.active_turn is not None:
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT,
                "Cannot clear history while a turn is active",
            )
        with self._execution.reserve(
            SessionExecutionKind.LIFECYCLE, f"clear:{params.session_id}"
        ):
            previous_history = self._turns.history
            await self._agent_loop.clear_history()
            await self._turns.reset()
            handoff = self._root_session.replace_idle(
                params.session_id,
                current_history=previous_history,
                checkpoint_kind="clear",
                checkpoint_message="New conversation started",
            )
        return SessionHistoryClearResponse(
            state=handoff.state, session_log=handoff.session_log
        )

    async def _compact(self, params: SessionCompactParams) -> SessionCompactResponse:
        self._require_session(params.session_id)
        if self._turns.active_turn is not None:
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT, "Cannot compact while a turn is active"
            )
        with self._execution.reserve(
            SessionExecutionKind.LIFECYCLE, f"compact:{params.session_id}"
        ):
            previous_history = self._turns.history
            try:
                summary = await self._agent_loop.compact(params.extra_instructions)
            except CompactionFailedError as exc:
                raise RequestFailure(
                    ProtocolErrorCode.COMPACTION_FAILED,
                    str(exc),
                    {"reason": exc.reason},
                ) from exc
            await self._turns.reset()
            handoff = self._root_session.replace_idle(
                params.session_id,
                current_history=previous_history,
                checkpoint_kind="compaction",
                checkpoint_message="Context compacted",
                checkpoint_details={"summaryLength": len(summary)},
            )
        return SessionCompactResponse(
            summary=summary, state=handoff.state, session_log=handoff.session_log
        )

    def _public_state(
        self,
        history_limit: int,
        *,
        turns_limit: int | None = None,
        include_history: bool = True,
        include_turns: bool = True,
    ) -> PublicSessionState:
        callbacks = [
            entry
            for entry in self._all_history()
            if isinstance(entry, PublicCallbackEntry)
        ]
        return self._root_session.public_state(
            current_history=self._turns.history,
            callbacks=callbacks,
            active_turn=self._turns.active_turn,
            completed_turns=self._turns.completed_turns,
            history_limit=history_limit,
            turns_limit=turns_limit,
            include_history=include_history,
            include_turns=include_turns,
        )

    def _all_history(self) -> list[PublicHistoryEntry]:
        return self._root_session.all_history(self._turns.history)

    def _require_session(self, session_id: str) -> None:
        if not self._root_session.is_current(session_id):
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )

    def _require_attached(self, session_id: str) -> None:
        self._require_session(session_id)
        if not self._root_session.is_attached(session_id):
            raise RequestFailure(ProtocolErrorCode.CONFLICT, "Session is not attached")

    def _require_turn_route(self, session_id: str, turn_id: str) -> None:
        active_turn = self._turns.active_turn
        if active_turn is None:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, "No active turn")
        if active_turn.id != turn_id:
            raise StaleTurnError(active_turn.id)
        if not self._root_session.routes_active_turn(session_id, active_turn):
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )
