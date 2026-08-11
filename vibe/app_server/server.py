from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum, auto
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue, ValidationError

from vibe import __version__
from vibe.app_server._account import AccountGateway
from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._execution import (
    SessionExecution,
    SessionExecutionConflict,
    SessionExecutionKind,
    cancel_tasks,
)
from vibe.app_server._handler import CoreRequestHandler, RootLifecycle
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._projection import project_history
from vibe.app_server._resources import ResourceRequestHandler
from vibe.app_server._root_session import RootSessionCoordinator
from vibe.app_server._runtime import (
    AgentRuntimeFactory,
    RootOpenRequest,
    RuntimeAuthenticationError,
    RuntimeConfigurationError,
    RuntimeSessionNotFoundError,
    close_agent_loop,
)
from vibe.app_server._session_history import SessionHistory
from vibe.app_server._sessions import SessionRuntime, SessionRuntimeRegistry
from vibe.app_server._tool_io import ClientToolIO
from vibe.app_server._turns import TurnConflictError, TurnController
from vibe.app_server._utils import public_error
from vibe.app_server.models import (
    OpenCallbackState,
    PublicCallbackEntry,
    PublicSessionState,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    SERVER_METHODS,
    AgentConfig,
    AppServerResponseError,
    CallbackCallParams,
    CallbackCallResponse,
    CallbackResultError,
    ClientCapabilities,
    ClientInfo,
    EventBatch,
    EventNotificationParams,
    EventsReadParams,
    InitializeParams,
    InitializeResponse,
    InvalidParamsData,
    InvalidParamsIssue,
    JsonRpcErrorResponse,
    JsonRpcProtocolError,
    JsonRpcSuccessResponse,
    Notification,
    ProtocolError,
    ProtocolErrorCode,
    RuntimeUpdatedParams,
    ServerErrorParams,
    ServerInfo,
    ServerRequest,
    SessionContinueParams,
    SessionContinueResponse,
    SessionDeleteParams,
    SessionHandoffParams,
    SessionOpenParams,
    SessionOptions,
    SessionResumeParams,
    SessionResumeResponse,
    SessionSnapshotParams,
    SessionStartParams,
    SessionStartResponse,
    TransportKind,
    TurnStartParams,
    validate_callback_acknowledgement,
    validate_json_rpc_envelope,
)
from vibe.app_server.transport import JsonRpcTransport
from vibe.core.agent_loop import AgentLoop
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.session import last_session_pointer
from vibe.core.worktree import (
    PreparedWorktree,
    WorktreeError,
    list_linked_worktrees,
    prepare_auto_worktree_session,
    prepare_worktree_session,
    remove_worktree,
)
from vibe.core.worktree_naming_model import suggest_worktree_name
from vibe.observability.logging import logger


class InitializationState(StrEnum):
    UNINITIALIZED = auto()
    INITIALIZE_RECEIVED = auto()
    INITIALIZED = auto()


type OpenRoot = Callable[[RootOpenRequest], Awaitable[AgentLoop]]
type StageRoot = Callable[[AgentLoop], Awaitable[None]]


_SESSION_ATTACHMENT_CANDIDATES = frozenset({
    "session/fork",
    "session/start",
    "session/resume",
    "session/continue",
})

_SESSION_OPTIONAL_METHODS = frozenset({"config/read", "workspace/trust/decision"})


@dataclass(slots=True)
class CallbackDelivery:
    session_id: str
    callback_id: str
    answered: bool = False


@dataclass(slots=True)
class PendingClientRequest:
    response_type: type[ProtocolModel]
    future: asyncio.Future[ProtocolModel]


@dataclass(frozen=True, slots=True)
class WorktreeResolution:
    options: SessionOptions
    prepared_worktree: PreparedWorktree | None = None


@dataclass(frozen=True, slots=True)
class OpenedRuntime:
    agent_loop: AgentLoop
    worktree_resolution: WorktreeResolution


@dataclass(frozen=True, slots=True)
class PendingNotification:
    method: str
    params: ProtocolModel


@dataclass(slots=True)
class _RootRuntime:
    session: SessionRuntime
    resources: ResourceRequestHandler
    coordinator: RootSessionCoordinator
    handler: CoreRequestHandler
    children: SessionRuntimeRegistry
    _closed: bool = field(default=False, init=False, repr=False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        self.children.release_root(self.session)
        for cleanup in (self.handler.close, self.children.close):
            try:
                await cleanup()
            except BaseException as exc:
                errors.append(exc)
        try:
            self.session.agent_loop.emit_session_closed_telemetry()
        except BaseException as exc:
            errors.append(exc)
        if (
            self.coordinator.attached_session_id is not None
            and self.session.agent_loop.session_logger.persisted
        ):
            try:
                last_session_pointer.record(
                    self.session.agent_loop.config.session_logging,
                    self.session.agent_loop.session_id,
                )
            except BaseException as exc:
                errors.append(exc)
        try:
            await self.session.close()
        except BaseException as exc:
            errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close root runtime", errors)


class AppServer:
    def __init__(
        self,
        transport: JsonRpcTransport,
        *,
        open_root: OpenRoot,
        transport_kind: TransportKind = "in_process",
        account_gateway: AccountGateway | None = None,
        identity_gateway: IdentityGateway | None = None,
        runtime_factory: AgentRuntimeFactory | None = None,
        host_handler: HostRequestHandler | None = None,
        stage_root: StageRoot | None = None,
    ) -> None:
        self._root: _RootRuntime | None = None
        self._open_root = open_root
        self._host_handler = host_handler or HostRequestHandler(
            HarnessFilesManager(sources=("user", "project"))
        )
        self._transport: JsonRpcTransport | None = transport
        self._transport_kind: TransportKind = transport_kind
        self._initialization = InitializationState.UNINITIALIZED
        self._client_info: ClientInfo | None = None
        self._client_capabilities = ClientCapabilities()
        self._next_request_id = 1
        self._event_watermarks: dict[str, int] = {}
        self._callback_requests: dict[int, CallbackDelivery] = {}
        self._client_requests: dict[int, PendingClientRequest] = {}
        self._abandoned_client_request_ids: set[int] = set()
        self._request_tasks: set[asyncio.Task[None]] = set()
        self._scheduler_task: asyncio.Task[None] | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._connection_attached = False
        self._attaching = False
        self._pending_notifications: list[PendingNotification] = []
        self._request_error: BaseException | None = None
        self._close_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()
        self._attachment_lock = asyncio.Lock()
        self._closed = False
        self._shutdown_complete = False
        self._account_gateway = account_gateway
        self._identity_gateway = identity_gateway
        self._runtime_factory = runtime_factory or AgentRuntimeFactory()
        self._stage_root = stage_root
        self._scheduler_enabled = False
        self._tool_io = ClientToolIO(
            self._request_client_result,
            lambda: self._client_capabilities,
            lambda: self._agent_loop.session_id,
        )
        self._sessions = SessionRuntimeRegistry(
            self._record_child_notification,
            self._deliver_callback,
            self._event_watermark,
            self._tool_io,
            self._runtime_factory,
        )

    @property
    def _agent_loop(self) -> AgentLoop:
        return self._require_root().session.agent_loop

    @property
    def _resources(self) -> ResourceRequestHandler:
        return self._require_root().resources

    @property
    def _root_session(self) -> RootSessionCoordinator:
        return self._require_root().coordinator

    @property
    def _turns(self) -> TurnController:
        return self._require_root().session.turns

    @property
    def _handler(self) -> CoreRequestHandler:
        return self._require_root().handler

    def _require_root(self) -> _RootRuntime:
        if self._root is None:
            raise RuntimeError("No app-server session runtime is active")
        return self._root

    def _bind_root(self, agent_loop: AgentLoop) -> None:
        execution = SessionExecution()
        history = SessionHistory(project_history(agent_loop))
        resources = ResourceRequestHandler(
            agent_loop,
            execution,
            self._notify,
            self._account_gateway,
            current_event_id=self._event_watermark,
            identity_gateway=self._identity_gateway,
        )
        coordinator = RootSessionCoordinator(
            agent_loop, resources, self._sessions, self._event_watermark, history
        )
        turns = TurnController(
            agent_loop,
            self._notify,
            self._deliver_callback,
            execution,
            self._sessions,
            tool_io=self._tool_io,
            session_coordinator=coordinator,
        )
        session = SessionRuntime(agent_loop, turns, execution, history)
        handler = CoreRequestHandler(
            agent_loop,
            turns,
            execution,
            self._notify,
            self._sessions,
            resources,
            coordinator,
            RootLifecycle(
                replace=self._replace_root,
                adopt=self._adopt_root,
                stage=self._stage_root,
            ),
            self._runtime_factory,
            self._event_watermark,
        )
        self._sessions.bind_root(session)
        self._root = _RootRuntime(
            session, resources, coordinator, handler, self._sessions
        )

    async def _replace_root(
        self, session_id: str, history_limit: int
    ) -> PublicSessionState:
        previous = self._require_root()
        with previous.session.execution.reserve(
            SessionExecutionKind.LIFECYCLE, f"resume:{session_id}"
        ):
            async with self._lifecycle_lock:
                try:
                    replacement = await self._runtime_factory.resume_root(
                        previous.session.agent_loop, session_id
                    )
                except RuntimeSessionNotFoundError as exc:
                    raise RequestFailure(
                        ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
                    ) from exc
                return await self._install_root(
                    previous, replacement, history_limit, resume_checkpoint=True
                )

    async def _adopt_root(
        self, replacement: AgentLoop, history_limit: int
    ) -> PublicSessionState:
        async with self._lifecycle_lock:
            previous = self._require_root()
            return await self._install_root(
                previous, replacement, history_limit, resume_checkpoint=False
            )

    async def _install_root(
        self,
        previous: _RootRuntime,
        replacement: AgentLoop,
        history_limit: int,
        *,
        resume_checkpoint: bool,
    ) -> PublicSessionState:
        self._root = None
        try:
            await previous.close()
        except BaseException:
            await close_agent_loop(replacement)
            raise
        try:
            self._bind_root(replacement)
        except BaseException:
            await close_agent_loop(replacement)
            raise
        staged_root = self._require_root()
        try:
            await self._sessions.close_children()
            self._root_session.attach(replacement.session_id)
            replacement.start_initialize_experiments()
        except BaseException:
            self._root = None
            await staged_root.close()
            raise
        if resume_checkpoint:
            return self._root_session.append_checkpoint(
                current_history=[],
                kind="resume",
                message="Session resumed",
                history_limit=history_limit,
            )
        return self._root_session.public_state(
            current_history=[],
            callbacks=[],
            active_turn=None,
            completed_turns=[],
            history_limit=history_limit,
        )

    async def serve(self) -> None:
        transport = self._transport
        if transport is None:
            raise RuntimeError("App server has no transport to serve")
        await self.serve_connection(transport, close_on_disconnect=True)

    async def serve_connection(
        self, transport: JsonRpcTransport, *, close_on_disconnect: bool
    ) -> None:
        async with self._connection_lock:
            if self._closed:
                raise RuntimeError("App server is closed")
            self._transport = transport
            self._initialization = InitializationState.UNINITIALIZED
            self._client_info = None
            self._client_capabilities = ClientCapabilities()
            self._connection_attached = False
            self._attaching = False
            self._pending_notifications.clear()
            self._request_error = None
            self._serve_task = asyncio.current_task()
            try:
                async for raw_message in transport.messages():
                    message = validate_json_rpc_envelope(raw_message)
                    if isinstance(message, ServerRequest):
                        if message.method == "initialize":
                            await self._handle_request(message)
                            continue
                        task = asyncio.create_task(self._handle_request(message))
                        self._request_tasks.add(task)
                        task.add_done_callback(self._request_finished)
                        continue
                    if isinstance(message, Notification):
                        self._handle_notification(message)
                        continue
                    task = asyncio.create_task(self._handle_response(message))
                    self._request_tasks.add(task)
                    task.add_done_callback(self._request_finished)
            except asyncio.CancelledError:
                if self._request_error is not None:
                    raise self._request_error
                raise
            finally:
                if close_on_disconnect:
                    await self.close()
                else:
                    await self._detach_connection(transport)

    async def close(self) -> None:
        async with self._close_lock:
            if self._shutdown_complete:
                return
            self._closed = True
            current = asyncio.current_task()
            serve_task = self._serve_task
            if serve_task is not None and serve_task is not current:
                serve_task.cancel()
            errors = await self._stop_background_tasks(current)
            try:
                await self._close_root()
            except BaseException as exc:
                errors.append(exc)
            self._callback_requests.clear()
            self._pending_notifications.clear()
            self._connection_attached = False
            self._attaching = False
            self._reject_pending_client_requests(RuntimeError("App server closed"))
            transport = self._transport
            if transport is not None:
                try:
                    await transport.close()
                except BaseException as exc:
                    errors.append(exc)
            self._shutdown_complete = True
            if len(errors) == 1:
                raise errors[0]
            if errors:
                raise BaseExceptionGroup("App server shutdown failed", errors)

    async def _close_root(self) -> None:
        async with self._lifecycle_lock:
            root = self._root
            self._root = None
            if root is not None:
                await root.close()
                return
            await self._sessions.close()

    async def _detach_connection(self, transport: JsonRpcTransport) -> None:
        if self._transport is not transport:
            return
        if self._closed:
            return
        errors = await self._stop_request_tasks(asyncio.current_task())
        self._reject_pending_client_requests(RuntimeError("Client disconnected"))
        self._callback_requests.clear()
        self._pending_notifications.clear()
        self._connection_attached = False
        self._attaching = False
        self._transport = None
        self._serve_task = None
        self._initialization = InitializationState.UNINITIALIZED
        self._client_info = None
        self._client_capabilities = ClientCapabilities()
        try:
            await transport.close()
        except BaseException as exc:
            errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("App-server connection cleanup failed", errors)

    async def _stop_background_tasks(
        self, current: asyncio.Task[object] | None
    ) -> list[BaseException]:
        tasks = [task for task in self._request_tasks if task is not current]
        scheduler = self._scheduler_task
        self._scheduler_task = None
        if scheduler is not None:
            tasks.append(scheduler)
        return await cancel_tasks(tasks, label="app-server background")

    async def _stop_request_tasks(
        self, current: asyncio.Task[object] | None
    ) -> list[BaseException]:
        tasks = [task for task in self._request_tasks if task is not current]
        return await cancel_tasks(tasks, label="app-server request")

    def _reject_pending_client_requests(self, error: Exception) -> None:
        for pending in self._client_requests.values():
            if not pending.future.done():
                pending.future.set_exception(error)
        self._client_requests.clear()
        self._abandoned_client_request_ids.clear()

    def _request_finished(self, task: asyncio.Task[None]) -> None:
        self._request_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None or self._closed:
            return
        self._request_error = error
        if self._serve_task is not None:
            self._serve_task.cancel()

    async def _handle_request(self, request: ServerRequest) -> None:
        if request.method in _SESSION_ATTACHMENT_CANDIDATES:
            async with self._attachment_lock:
                await self._handle_request_once(request)
            return
        await self._handle_request_once(request)

    async def _handle_request_once(self, request: ServerRequest) -> None:
        was_attached = self._connection_attached
        attachment_candidate = request.method in _SESSION_ATTACHMENT_CANDIDATES
        if attachment_candidate:
            self._begin_attachment()
        outcome = await self._dispatch_or_error(request)
        if isinstance(outcome, ProtocolError):
            await self._send_error(request.id, outcome)
            if attachment_candidate:
                await self._finish_attachment(was_attached)
            if request.method == "session/stop":
                await self.close()
            return
        try:
            await self._send({
                "jsonrpc": "2.0",
                "id": request.id,
                "result": outcome.response.model_dump(mode="json", by_alias=True),
            })
        except BaseException:
            if attachment_candidate:
                await self._finish_attachment(was_attached)
            raise
        if attachment_candidate:
            await self._finish_attachment(outcome.session_attached or was_attached)
        await self._after_response(request, outcome)

    def _begin_attachment(self) -> None:
        self._attaching = True
        self._connection_attached = False
        self._pending_notifications.clear()

    async def _finish_attachment(self, attached: bool) -> None:
        if not attached:
            self._pending_notifications.clear()
            self._attaching = False
            self._connection_attached = False
            return
        while self._pending_notifications:
            pending = self._pending_notifications
            self._pending_notifications = []
            for notification in pending:
                await self._send_notification(notification.method, notification.params)
        self._attaching = False
        self._connection_attached = True

    async def _dispatch_or_error(
        self, request: ServerRequest
    ) -> DispatchResult | ProtocolError:
        try:
            if request.method == "initialize":
                return DispatchResult(self._initialize(request.params))
            if self._initialization is not InitializationState.INITIALIZED:
                raise RequestFailure(
                    ProtocolErrorCode.NOT_INITIALIZED,
                    "initialize must be the first request",
                )
            dispatched = await self._dispatch_request(request.method, request.params)
            if dispatched.session_attached:
                self._validate_open_callback_capabilities()
            return dispatched
        except RequestFailure as exc:
            return ProtocolError(code=exc.code, message=str(exc), data=exc.data)
        except ValidationError as exc:
            return ProtocolError(
                code=ProtocolErrorCode.INVALID_PARAMS,
                message="Invalid request parameters",
                data=InvalidParamsData(
                    error_count=exc.error_count(),
                    issues=[
                        InvalidParamsIssue(
                            path=list(issue["loc"]), message=issue["msg"]
                        )
                        for issue in exc.errors()
                    ],
                ).model_dump(mode="json", by_alias=True),
            )
        except Exception as exc:
            return ProtocolError(
                code=ProtocolErrorCode.INTERNAL_ERROR, message=str(exc)
            )

    async def _after_response(
        self, request: ServerRequest, dispatched: DispatchResult
    ) -> None:
        if request.method == "callback/result":
            result = request.params.get("result")
            callback_id = result.get("callbackId") if isinstance(result, dict) else None
            if isinstance(callback_id, str):
                self._mark_callback_answered(callback_id)
        if dispatched.after_response is not None:
            dispatched.after_response()
        if dispatched.session_attached:
            await self._redeliver_open_callbacks()
        if dispatched.runtime_updated and self._root is not None:
            await self._notify(
                "runtime/updated",
                RuntimeUpdatedParams(
                    session_id=self._agent_loop.session_id,
                    runtime=self._resources.runtime_snapshot(),
                ),
            )
        if self._scheduler_enabled and dispatched.session_attached:
            self._ensure_scheduler()
        if request.method == "session/stop":
            await self.close()

    async def _dispatch_request(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method not in SERVER_METHODS:
            raise method_not_found(method)
        if method == "events/read":
            return self._events_read(raw_params)
        if method in {
            "config/schema",
            "workspace/trust/untrustedConfig",
            "workspace/trust/status",
            "workspace/worktrees/list",
        } or method.startswith("projectLinks/"):
            return await self._host_handler.dispatch(method, raw_params)
        if method == "session/delete":
            return await self._delete_session(raw_params)
        if _omits_session_id(method, raw_params):
            return await self._host_handler.dispatch(method, raw_params)
        if self._root is None:
            return await self._dispatch_without_root(method, raw_params)
        result = await self._dispatch_to_root(method, raw_params)
        if method == "session/stop":
            await self._close_attached_runtime()
        return result

    def _events_read(self, raw_params: dict[str, Any]) -> DispatchResult:
        """Reserve pull-based event replay for future clients.

        Vibe receives live updates through notifications. Without a replay
        store, this procedure intentionally returns an empty batch.
        """
        EventsReadParams.model_validate(raw_params)
        return DispatchResult(response=EventBatch())

    async def _close_attached_runtime(self) -> None:
        self._closed = True
        errors = await self._stop_background_tasks(asyncio.current_task())
        try:
            await self._close_root()
        except BaseException as exc:
            errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("App-server session shutdown failed", errors)

    async def _delete_session(self, raw_params: dict[str, Any]) -> DispatchResult:
        params = validate_wire(SessionDeleteParams, raw_params)
        root = self._root
        is_root = (
            root is not None and params.session_id == root.session.agent_loop.session_id
        )
        if is_root or self._sessions.references_child(params.session_id):
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT, "Deleting a live session is not supported"
            )
        return await self._host_handler.dispatch("session/delete", raw_params)

    async def _dispatch_without_root(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if self._host_handler.handles(method):
            return await self._host_handler.dispatch(method, raw_params)
        return await self._open_initial_session(method, raw_params)

    async def _dispatch_to_root(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        root = self._root
        if root is None:
            raise RuntimeError("Root session closed while dispatching a request")
        try:
            return await root.handler.dispatch(method, raw_params)
        except RequestFailure as exc:
            if exc.code is ProtocolErrorCode.NOT_FOUND and method in {
                "session/read",
                "session/rename",
                "session/history/list",
            }:
                return await self._host_handler.dispatch(method, raw_params)
            raise

    async def _open_initial_session(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        async with self._lifecycle_lock:
            root = self._root
            if root is None:
                return await self._open_initial_session_locked(method, raw_params)
        return await root.handler.dispatch(method, raw_params)

    async def _open_initial_session_locked(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        open_root = self._open_root
        match method:
            case "session/start":
                params = validate_wire(SessionStartParams, raw_params)
                self._scheduler_enabled = not params.agent_config.headless
                opened = await self._open_runtime(open_root, params, None)
                state = await self._attach_opened_runtime(opened, params.history_limit)
                return DispatchResult(
                    SessionStartResponse(state=state, last_event_id=state.event_id),
                    session_attached=True,
                )
            case "session/resume":
                params = validate_wire(SessionResumeParams, raw_params)
                self._scheduler_enabled = not params.agent_config.headless
                _reject_worktree_input(params.agent_config)
                try:
                    opened = await self._open_runtime(
                        open_root, params, params.session_id
                    )
                except RuntimeSessionNotFoundError as exc:
                    raise RequestFailure(
                        ProtocolErrorCode.NOT_FOUND,
                        f"Session not found: {params.session_id}",
                    ) from exc
                state = await self._attach_opened_runtime(
                    opened, params.history_limit, resumed=True
                )
                return DispatchResult(
                    SessionResumeResponse(state=state, last_event_id=state.event_id),
                    session_attached=True,
                )
            case "session/continue":
                params = validate_wire(SessionContinueParams, raw_params)
                self._scheduler_enabled = not params.agent_config.headless
                _reject_worktree_input(params.agent_config)
                try:
                    opened = await self._open_runtime(
                        open_root, params, None, continue_latest=True
                    )
                except RuntimeSessionNotFoundError as exc:
                    raise RequestFailure(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
                state = await self._attach_opened_runtime(
                    opened, params.history_limit, resumed=True
                )
                return DispatchResult(
                    SessionContinueResponse(state=state, last_event_id=state.event_id),
                    session_attached=True,
                )
            case _:
                raise RequestFailure(
                    ProtocolErrorCode.CONFLICT,
                    "Start, resume, or continue a session before using this method",
                )

    async def _attach_opened_session(
        self, agent_loop: AgentLoop, history_limit: int, *, resumed: bool = False
    ) -> PublicSessionState:
        try:
            self._bind_root(agent_loop)
            started = await self._handler.dispatch(
                "session/start",
                SessionStartParams(
                    agent_config=AgentConfig(cwd=str(agent_loop.cwd)),
                    history_limit=history_limit,
                ).model_dump(mode="json", by_alias=True),
            )
            assert isinstance(started.response, SessionStartResponse)
            state = started.response.state
            self._schedule_admin_config_fetch()
            if not resumed:
                return state
            return self._root_session.append_checkpoint(
                current_history=[],
                kind="resume",
                message="Session resumed",
                history_limit=history_limit,
            )
        except BaseException:
            root = self._root
            self._root = None
            if root is not None:
                await root.close()
            else:
                await close_agent_loop(agent_loop)
            raise

    def _schedule_admin_config_fetch(self) -> None:
        task = asyncio.create_task(
            self._fetch_admin_config(), name="vibe-admin-config-fetch"
        )
        self._request_tasks.add(task)
        task.add_done_callback(self._request_finished)

    async def _fetch_admin_config(self) -> None:
        if self._root is None:
            return
        try:
            changed = await self._resources.apply_admin_config()
        except Exception as exc:
            logger.debug("Admin config fetch failed", exc_info=exc)
            return
        if changed and self._root is not None:
            await self._notify(
                "runtime/updated",
                RuntimeUpdatedParams(
                    session_id=self._agent_loop.session_id,
                    runtime=self._resources.runtime_snapshot(),
                ),
            )

    async def _open_runtime(
        self,
        open_root: OpenRoot,
        params: SessionOpenParams,
        session_id: str | None,
        *,
        continue_latest: bool = False,
    ) -> OpenedRuntime:
        options = params.agent_config.model_copy(update={"cwd": params.cwd})
        try:
            worktree_resolution = await self._resolve_worktree(options)
            try:
                agent_loop = await open_root(
                    RootOpenRequest(
                        options=worktree_resolution.options,
                        client_info=self._initialized_client_info(),
                        client_capabilities=self._client_capabilities,
                        session_id=session_id,
                        continue_latest=continue_latest,
                    )
                )
            except BaseException:
                await self._cleanup_worktree(worktree_resolution)
                raise
            return OpenedRuntime(
                agent_loop=agent_loop, worktree_resolution=worktree_resolution
            )
        except RuntimeAuthenticationError as exc:
            raise RequestFailure(
                ProtocolErrorCode.UNAUTHORIZED,
                str(exc),
                data={"provider": exc.provider},
            ) from exc
        except RuntimeConfigurationError as exc:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                str(exc),
                data={"kind": "configuration"},
            ) from exc
        except WorktreeError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc

    async def _resolve_worktree(self, options: SessionOptions) -> WorktreeResolution:
        # Stay synchronous without a worktree: hopping to a thread here would add
        # an await point to every session start, letting a pipelined follow-up
        # request overtake the session attachment.
        if options.worktree is None:
            return WorktreeResolution(options=options)

        # Named before the thread hop rather than inside it: the suggestion is
        # async and nothing has been created yet, so a cancellation here costs
        # nothing and needs no cleanup.
        suggested_name = await self._suggest_worktree_name(options)

        # Shielded because the worker thread cannot be cancelled: it still creates
        # the worktree after a cancelled await, so the resolution has to stay
        # reachable for cleanup.
        resolve = asyncio.create_task(
            asyncio.to_thread(resolve_worktree, options, suggested_name)
        )
        try:
            return await asyncio.shield(resolve)
        except asyncio.CancelledError:
            with suppress(BaseException):
                await self._cleanup_worktree(await resolve)
            raise

    @staticmethod
    async def _suggest_worktree_name(options: SessionOptions) -> str | None:
        # Only an auto worktree is named here; the other kinds already carry the
        # name the caller chose, so they must not pay the latency.
        worktree = options.worktree
        if worktree is None or worktree.kind != "auto":
            return None
        return await suggest_worktree_name(
            worktree.prompt, cwd=Path(options.cwd or Path.cwd())
        )

    async def _attach_opened_runtime(
        self, opened: OpenedRuntime, history_limit: int, *, resumed: bool = False
    ) -> PublicSessionState:
        try:
            return await self._attach_opened_session(
                opened.agent_loop, history_limit, resumed=resumed
            )
        except BaseException:
            await self._cleanup_worktree(opened.worktree_resolution)
            raise

    async def _cleanup_worktree(self, worktree_resolution: WorktreeResolution) -> None:
        worktree = worktree_resolution.prepared_worktree
        if worktree is None or not worktree.created:
            return
        try:
            await asyncio.to_thread(
                remove_worktree, worktree, delete_branch=worktree.branch_created
            )
        except Exception as exc:
            logger.warning(
                "Failed to clean up worktree after session startup failure",
                exc_info=exc,
            )

    def _ensure_scheduler(self) -> None:
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._scheduler_task = asyncio.create_task(
            self._run_scheduler(), name="vibe-scheduled-loops"
        )

    async def _run_scheduler(self) -> None:
        while True:
            try:
                delay = self._resources.next_loop_due_in()
                await asyncio.sleep(max(0.05, min(delay, 1.0)))
                if self._require_root().session.execution.active is not None:
                    continue
                loop = self._resources.due_loop()
                if loop is None:
                    continue
                try:
                    _, start = self._turns.start(
                        TurnStartParams(
                            session_id=self._agent_loop.session_id,
                            message=[TextContentBlock(text=loop.prompt)],
                        ),
                        scheduled_loop_id=loop.id,
                    )
                except (SessionExecutionConflict, TurnConflictError):
                    continue
                start()
                await self._resources.mark_loop_fired(loop.id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._notify("error", ServerErrorParams(error=public_error(exc)))

    def _initialize(self, raw_params: dict[str, Any]) -> InitializeResponse:
        if self._initialization is not InitializationState.UNINITIALIZED:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_REQUEST, "initialize may only be called once"
            )
        params = validate_wire(InitializeParams, raw_params)
        self._client_info = params.client_info
        self._client_capabilities = params.capabilities
        self._initialization = InitializationState.INITIALIZE_RECEIVED
        return InitializeResponse(
            server_info=ServerInfo(name="vibe-app-server", version=__version__)
        )

    def _initialized_client_info(self) -> ClientInfo:
        if self._client_info is None:
            raise RuntimeError("App-server client metadata is unavailable")
        return self._client_info

    def _handle_notification(self, notification: Notification) -> None:
        if (
            notification.method == "initialized"
            and self._initialization is InitializationState.INITIALIZE_RECEIVED
        ):
            self._initialization = InitializationState.INITIALIZED
            return
        raise JsonRpcProtocolError(
            f"Unknown or unexpected client notification: {notification.method}"
        )

    async def _handle_response(
        self, response: JsonRpcErrorResponse | JsonRpcSuccessResponse
    ) -> None:
        request_id = response.id
        if not isinstance(request_id, int):
            raise JsonRpcProtocolError("Server request responses require integer IDs")
        pending = self._client_requests.pop(request_id, None)
        if pending is not None:
            self._resolve_client_request(request_id, pending, response)
            return
        if request_id in self._abandoned_client_request_ids:
            self._abandoned_client_request_ids.remove(request_id)
            return
        callback_route = self._callback_requests.pop(request_id, None)
        if callback_route is None:
            raise JsonRpcProtocolError(
                f"Response does not match a pending server request: {request_id}"
            )
        session_id = callback_route.session_id
        callback_id = callback_route.callback_id
        if isinstance(response, JsonRpcErrorResponse):
            if callback_route.answered:
                return
            await self._reject_callback(session_id, callback_id, response.error.message)
            return
        acknowledgement = validate_wire(CallbackCallResponse, response.result)
        validate_callback_acknowledgement(callback_id, acknowledgement)
        if not acknowledgement.accepted:
            if callback_route.answered:
                return
            await self._reject_callback(
                session_id, callback_id, "Client did not accept callback delivery"
            )

    def _resolve_client_request(
        self,
        request_id: int,
        pending: PendingClientRequest,
        response: JsonRpcErrorResponse | JsonRpcSuccessResponse,
    ) -> None:
        if pending.future.cancelled():
            self._abandoned_client_request_ids.discard(request_id)
            return
        if isinstance(response, JsonRpcErrorResponse):
            pending.future.set_exception(AppServerResponseError(response.error))
            return
        try:
            result = validate_wire(pending.response_type, response.result)
        except Exception as exc:
            pending.future.set_exception(exc)
        else:
            pending.future.set_result(result)

    async def _notify(self, method: str, params: ProtocolModel) -> None:
        if (
            method in self._client_capabilities.disabled_notifications
            and not isinstance(params, EventNotificationParams)
        ):
            return
        params = self._sequence_notification(params)
        if self._attaching:
            self._pending_notifications.append(PendingNotification(method, params))
            return
        if not self._connection_attached:
            return
        await self._send_notification(method, params)

    async def _send_notification(self, method: str, params: ProtocolModel) -> None:
        transport = self._transport
        if transport is None:
            return
        try:
            await transport.send({
                "jsonrpc": "2.0",
                "method": method,
                "params": params.model_dump(mode="json", by_alias=True),
            })
        except Exception:
            self._disconnect_current_client()

    async def _deliver_callback(self, callback: PublicCallbackEntry) -> None:
        transport = self._transport
        if transport is None or not self._connection_attached or self._attaching:
            return
        callback_kind = callback.detail.kind
        if callback_kind not in self._client_capabilities.callback_kinds:
            raise RuntimeError(f"Client does not support {callback_kind} callbacks")
        request_id = self._next_request_id
        self._next_request_id += 1
        self._callback_requests[request_id] = CallbackDelivery(
            session_id=callback.session_id, callback_id=callback.callback_id
        )
        params = CallbackCallParams(callback=callback)
        try:
            await transport.send({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "callback/call",
                "params": params.model_dump(mode="json", by_alias=True),
            })
        except Exception:
            self._callback_requests.pop(request_id, None)
            self._disconnect_current_client()

    async def _request_client_result[ResultT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResultT]
    ) -> ResultT:
        request_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[ProtocolModel] = (
            asyncio.get_running_loop().create_future()
        )
        self._client_requests[request_id] = PendingClientRequest(
            response_type=response_type, future=future
        )
        transport = self._transport
        if transport is None or not self._connection_attached or self._attaching:
            self._client_requests.pop(request_id, None)
            raise RuntimeError("No app-server client is attached")
        sent = False
        try:
            await transport.send({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params.model_dump(mode="json", by_alias=True),
            })
            sent = True
            return cast(ResultT, await future)
        except asyncio.CancelledError:
            if sent and self._client_requests.pop(request_id, None) is not None:
                self._abandoned_client_request_ids.add(request_id)
            raise
        except Exception:
            self._disconnect_current_client()
            raise
        finally:
            self._client_requests.pop(request_id, None)

    def _mark_callback_answered(self, callback_id: str) -> None:
        for delivery in self._callback_requests.values():
            if delivery.callback_id == callback_id:
                delivery.answered = True

    async def _redeliver_open_callbacks(self) -> None:
        if self._root is None:
            return
        callbacks: list[PublicCallbackEntry] = [
            *self._turns.callbacks,
            *self._sessions.active_callbacks(),
        ]
        for callback in callbacks:
            if not isinstance(callback.state, OpenCallbackState):
                continue
            await self._deliver_callback(callback)

    def _validate_open_callback_capabilities(self) -> None:
        if self._root is None:
            return
        required = {
            callback.detail.kind
            for callback in [*self._turns.callbacks, *self._sessions.active_callbacks()]
            if isinstance(callback.state, OpenCallbackState)
        }
        missing = sorted(required - set(self._client_capabilities.callback_kinds))
        if not missing:
            return
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            f"Client does not support open callback kinds: {', '.join(missing)}",
            data=cast(JsonValue, {"missingCallbackKinds": missing}),
        )

    async def _record_child_notification(
        self, method: str, params: ProtocolModel
    ) -> None:
        del method
        self._sequence_notification(params)

    def _sequence_notification(self, params: ProtocolModel) -> ProtocolModel:
        if not isinstance(params, EventNotificationParams):
            return params
        session_id = _notification_session_id(params)
        event_id = self._event_watermark(session_id) + 1
        self._event_watermarks[session_id] = event_id
        update: dict[str, Any] = {"event_id": event_id}
        if isinstance(params, SessionSnapshotParams | SessionHandoffParams):
            update["state"] = params.state.model_copy(update={"event_id": event_id})
        return params.model_copy(update=update)

    async def _reject_callback(
        self, session_id: str, callback_id: str, message: str
    ) -> None:
        error = CallbackResultError(message=message)
        if await self._sessions.ensure_child(session_id):
            await self._sessions.reject_callback(session_id, callback_id, error)
            return
        await self._turns.reject_callback(callback_id, error)

    async def _send_error(self, request_id: Any, error: ProtocolError) -> None:
        await self._send({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": error.model_dump(mode="json", by_alias=True),
        })

    async def _send(self, message: dict[str, Any]) -> None:
        transport = self._transport
        if transport is None:
            raise RuntimeError("No app-server client is attached")
        await transport.send(message)

    def _disconnect_current_client(self) -> None:
        task = self._serve_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _event_watermark(self, session_id: str) -> int:
        return self._event_watermarks.get(session_id, 0)


def _omits_session_id(method: str, raw_params: dict[str, Any]) -> bool:
    """Whether a method that can answer from either scope named no session.

    `_SESSION_OPTIONAL_METHODS` take an optional session id. When it is absent
    the caller is asking the host, so they must not be routed to an attached
    root: the answer would otherwise depend on whether a session happens to be
    attached, and a `cwd` the host would have honoured would be silently
    dropped. The handler still validates the params it was given.
    """
    return method in _SESSION_OPTIONAL_METHODS and raw_params.get("sessionId") is None


def _reject_worktree_input(options: SessionOptions) -> None:
    # The field rides on shared SessionOptions, so resume/continue accept it on
    # the wire. Resolving it there would mint a worktree and reopen the saved
    # session under it instead of the workspace it was recorded against.
    if options.worktree is None:
        return
    raise RequestFailure(
        ProtocolErrorCode.INVALID_PARAMS,
        "worktree is only supported when starting a session",
    )


def resolve_worktree(
    options: SessionOptions, suggested_name: str | None = None
) -> WorktreeResolution:
    requested_worktree = options.worktree
    if requested_worktree is None:
        return WorktreeResolution(options=options)

    # Match the runtime default: non-desktop callers may omit cwd, in which case
    # the app-server process cwd is the local project root.
    base_cwd = Path(options.cwd or Path.cwd()).expanduser().resolve()
    if not base_cwd.is_dir():
        raise WorktreeError(f"Local project path is not a directory: {base_cwd}")

    prepared_worktree: PreparedWorktree | None = None
    match requested_worktree.kind:
        case "existing":
            requested = Path(requested_worktree.cwd).expanduser().resolve()
            linked = list_linked_worktrees(base_cwd)
            if not any(worktree.path == requested for worktree in linked):
                raise WorktreeError(
                    f"Worktree is not linked to the local project: {requested}"
                )
            cwd = requested
        case "create":
            prepared_worktree = prepare_worktree_session(
                requested_worktree.name, base_cwd, branch=requested_worktree.branch
            )
            cwd = prepared_worktree.path
        case "auto":
            prepared_worktree = prepare_auto_worktree_session(
                base_cwd,
                prompt=requested_worktree.prompt,
                suggested_name=suggested_name,
            )
            cwd = prepared_worktree.path
        case _:  # Safety net for future worktree input variants.
            raise TypeError(f"Unsupported worktree input: {requested_worktree!r}")

    return WorktreeResolution(
        options=options.model_copy(
            update={"cwd": str(cwd), "workspace_roots": [str(cwd)], "worktree": None}
        ),
        prepared_worktree=prepared_worktree,
    )


def _notification_session_id(params: EventNotificationParams) -> str:
    return params.session_id
