from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from vibe.app_server._dispatch import DispatchResult, RequestFailure
from vibe.app_server._execution import SessionExecutionConflict, SessionExecutionKind
from vibe.app_server._handler import CoreRequestHandler
from vibe.app_server._host import HostRequestHandler, project_session_list
from vibe.app_server._model import ProtocolModel
from vibe.app_server._resources import ResourceRequestHandler
from vibe.app_server._root_session import RootSessionCoordinator
from vibe.app_server._session_backend_port import (
    SessionBackendError,
    SessionBackendEvent,
    SessionBackendKind,
    SessionBackendResult,
    SessionEventSubscription,
    SessionForkResult,
    SessionLifecycleResult,
)
from vibe.app_server._sessions import SessionRuntime, SessionRuntimeRegistry
from vibe.app_server._streaming import finish_event_queue
from vibe.app_server.events import (
    CallbackRequested,
    ClientProjection,
    EventSequenceError,
    UnknownNotificationError,
    parse_server_event,
)
from vibe.app_server.models import PublicCallbackEntry, PublicSessionState
from vibe.app_server.protocol import (
    AgentSwitchParams,
    CallbackResultError,
    CallbackResultParams,
    CallbackResultResponse,
    ConfigMutationResponse,
    ConfigReloadParams,
    ConfigWriteParams,
    ConfigWriteResponse,
    ContextInjectParams,
    ContextInjectResponse,
    EmptyResponse,
    EventNotificationParams,
    Notification,
    ProtocolErrorCode,
    RuntimeMutationResponse,
    RuntimeUpdatedParams,
    SessionCompactParams,
    SessionCompactResponse,
    SessionContinueParams,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryClearResponse,
    SessionListParams,
    SessionListResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    SessionRewindResponse,
    SessionSettingsUpdateParams,
    SessionStartParams,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
)
from vibe.core.git.worktree import ManagedWorktree, PreparedWorktree
from vibe.core.session.session_lease import SessionBusyError
from vibe.observability.logging import logger


@dataclass(frozen=True, slots=True)
class _EventStreamClosed:
    pass


type _QueuedEvent = SessionBackendEvent | _EventStreamClosed
type _StartSession = Callable[[SessionStartParams], Awaitable[LegacySessionBackend]]
type _ResumeSession = Callable[
    [SessionResumeParams],
    Awaitable[tuple[LegacySessionBackend, Callable[[], None] | None]],
]
type _ContinueSession = Callable[
    [SessionContinueParams], Awaitable[LegacySessionBackend]
]
type _CurrentSession = Callable[[], LegacySessionBackend | None]
type _StopBackgroundTasks = Callable[[Any], Awaitable[list[BaseException]]]
type _AfterLifecycleResponse = Callable[[], None]


@dataclass(slots=True)
class LegacySessionBackend:
    session: SessionRuntime
    resources: ResourceRequestHandler
    coordinator: RootSessionCoordinator
    handler: CoreRequestHandler
    children: SessionRuntimeRegistry
    record_last_session: Callable[..., None]
    created_worktree: PreparedWorktree | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _projection: ClientProjection | None = field(default=None, init=False, repr=False)
    _events: asyncio.Queue[_QueuedEvent] = field(
        default_factory=lambda: asyncio.Queue(maxsize=256), init=False, repr=False
    )
    _events_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _events_idle: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _events_subscribed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._events_idle.set()

    @property
    def session_id(self) -> str:
        return self.session.agent_loop.session_id

    def adopt_state(self, state: PublicSessionState) -> None:
        self._projection = ClientProjection(state)

    async def publish_notification(self, method: str, params: ProtocolModel) -> bool:
        async with self._events_lock:
            if not self._events_subscribed:
                return False
            if self._projection is None:
                return False
            notification = Notification(
                method=method, params=params.model_dump(mode="json", by_alias=True)
            )
            event = parse_server_event(notification)
            if event is None:
                try:
                    event = self._projection.consume(notification)
                except UnknownNotificationError:
                    return False
                except EventSequenceError:
                    self._projection = None
                    self._events_idle.clear()
                    finish_event_queue(self._events, _EventStreamClosed())
                    return False
            if event is None:
                return False
            self._events_idle.clear()
            await self._events.put(
                SessionBackendEvent(
                    event=event,
                    method=method,
                    params=params,
                    session_id=(
                        params.session_id
                        if isinstance(params, EventNotificationParams)
                        else None
                    ),
                    event_id=(
                        params.event_id
                        if isinstance(params, EventNotificationParams)
                        else None
                    ),
                )
            )
            return True

    async def publish_callback(self, callback: PublicCallbackEntry) -> bool:
        async with self._events_lock:
            if not self._events_subscribed or self._projection is None:
                return False
            self._events_idle.clear()
            await self._events.put(
                SessionBackendEvent(event=CallbackRequested(callback))
            )
            return True

    async def flush_events(self) -> None:
        await self._events_idle.wait()

    def runtime_updated_params(self) -> RuntimeUpdatedParams:
        return RuntimeUpdatedParams(
            session_id=self.session_id, runtime=self.resources.runtime_snapshot()
        )

    def references_child(self, session_id: str) -> bool:
        return self.children.references_child(session_id)

    def open_callbacks(self) -> list[PublicCallbackEntry]:
        return [*self.session.turns.callbacks, *self.children.active_callbacks()]

    async def reject_callback_delivery(
        self, session_id: str, callback_id: str, error: CallbackResultError
    ) -> None:
        if await self.children.ensure_child(session_id):
            await self.children.reject_callback(session_id, callback_id, error)
            return
        await self.session.turns.reject_callback(callback_id, error)

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        result = await self._request("session/read", params, SessionReadResponse)
        if result.after_response is not None:
            raise RuntimeError("session/read returned a deferred action")
        return result.response

    async def subscribe(self, params: SessionReadParams) -> SessionEventSubscription:
        if params.session_id != self.session_id:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {params.session_id}"
            )
        async with self._events_lock:
            if self._events_subscribed:
                raise SessionBackendError(
                    ProtocolErrorCode.CONFLICT,
                    "The legacy session backend already has an event subscriber",
                )
            response = await self.read(params)
            self.adopt_state(response.state)
            while not self._events.empty():
                self._events.get_nowait()
            self._events_idle.set()
            self._events_subscribed = True
        return SessionEventSubscription(
            snapshot=response,
            events=self._event_stream(
                session_id=response.state.session.id,
                after_event_id=response.last_event_id,
            ),
        )

    def guard_request(self) -> None:
        active = self.session.execution.active
        if active is None or active.kind is not SessionExecutionKind.LIFECYCLE:
            return
        raise SessionBackendError(
            ProtocolErrorCode.CONFLICT,
            f"Session lifecycle transition is active: {active.id}",
        )

    async def switch_agent(
        self, params: AgentSwitchParams
    ) -> SessionBackendResult[RuntimeMutationResponse]:
        return await self._request(
            "session/agent/update", params, RuntimeMutationResponse
        )

    async def update_settings(
        self, params: SessionSettingsUpdateParams
    ) -> SessionBackendResult[EmptyResponse]:
        return await self._request("session/settings/update", params, EmptyResponse)

    async def write_config(
        self, params: ConfigWriteParams
    ) -> SessionBackendResult[ConfigWriteResponse]:
        return await self._resource_request("config/write", params, ConfigWriteResponse)

    async def reload_config(
        self, params: ConfigReloadParams
    ) -> SessionBackendResult[ConfigMutationResponse]:
        return await self._resource_request(
            "config/reload", params, ConfigMutationResponse
        )

    async def start_turn(
        self, params: TurnStartParams
    ) -> SessionBackendResult[TurnStartResponse]:
        return await self._request("turn/start", params, TurnStartResponse)

    async def steer_turn(
        self, params: TurnSteerParams
    ) -> SessionBackendResult[TurnSteerResponse]:
        return await self._request("turn/steer", params, TurnSteerResponse)

    async def interrupt_turn(
        self, params: TurnInterruptParams
    ) -> SessionBackendResult[TurnInterruptResponse]:
        return await self._request("turn/interrupt", params, TurnInterruptResponse)

    async def inject_context(
        self, params: ContextInjectParams
    ) -> SessionBackendResult[ContextInjectResponse]:
        return await self._request(
            "session/context/inject", params, ContextInjectResponse
        )

    async def respond_to_callback(
        self, params: CallbackResultParams
    ) -> SessionBackendResult[CallbackResultResponse]:
        return await self._request("callback/result", params, CallbackResultResponse)

    async def compact(
        self, params: SessionCompactParams
    ) -> SessionBackendResult[SessionCompactResponse]:
        return await self._request("session/compact", params, SessionCompactResponse)

    async def _event_stream(
        self, *, session_id: str, after_event_id: int
    ) -> AsyncIterator[SessionBackendEvent]:
        try:
            while True:
                queued = await self._events.get()
                try:
                    if isinstance(queued, _EventStreamClosed):
                        return
                    event_id = queued.event_id
                    if event_id is None:
                        yield queued
                        continue
                    if queued.session_id is None:
                        raise RuntimeError("Numbered backend event has no session ID")
                    if queued.session_id != session_id:
                        session_id = queued.session_id
                        after_event_id = 0
                    if event_id <= after_event_id:
                        continue
                    expected_event_id = after_event_id + 1
                    if event_id != expected_event_id:
                        raise SessionBackendError(
                            ProtocolErrorCode.STALE_CURSOR,
                            "The session event stream has a gap",
                            data={
                                "expectedEventId": expected_event_id,
                                "receivedEventId": event_id,
                            },
                        )
                    after_event_id = event_id
                    yield queued
                finally:
                    if self._events.empty():
                        self._events_idle.set()
        finally:
            async with self._events_lock:
                self._events_subscribed = False

    async def dispatch_extension(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        try:
            dispatched = await self.handler.dispatch(method, raw_params)
        except RequestFailure as exc:
            raise SessionBackendError(exc.code, str(exc), exc.data) from exc
        if isinstance(
            dispatched.response, SessionHistoryClearResponse | SessionRewindResponse
        ):
            self.adopt_state(dispatched.response.state)
        return dispatched

    async def shutdown(self) -> None:
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
                self.record_last_session(
                    self.session.agent_loop.config.session_logging,
                    self.session.agent_loop.session_id,
                )
            except BaseException as exc:
                errors.append(exc)
        # Release the liveness marker before the potentially slow runtime close
        # so a concurrent session deletion does not strand the worktree.
        try:
            self._release_worktree_holder()
        except BaseException as exc:
            errors.append(exc)
        try:
            await self.session.close()
        except BaseException as exc:
            errors.append(exc)
        # Runtime shutdown releases MCP and terminal handles before an unstarted
        # worktree is rolled back, which is required for removal on Windows.
        try:
            await self._roll_back_unstarted_worktree()
        except BaseException as exc:
            errors.append(exc)
        if self._events_subscribed:
            self._events_idle.clear()
            finish_event_queue(self._events, _EventStreamClosed())
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close root runtime", errors)

    def _release_worktree_holder(self) -> None:
        agent_loop = self.session.agent_loop
        if managed := ManagedWorktree.at(agent_loop.cwd):
            managed.release_holder(agent_loop.session_id)

    async def _roll_back_unstarted_worktree(self) -> None:
        agent_loop = self.session.agent_loop
        managed = ManagedWorktree.at(agent_loop.cwd)
        if managed is None:
            return
        if agent_loop.session_logger.persisted or self.created_worktree is None:
            return
        try:
            await asyncio.to_thread(managed.release, agent_loop.session_id)
        except Exception as exc:
            logger.warning(
                "Failed to roll back the worktree of an unstarted session", exc_info=exc
            )

    async def _request[ResponseT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResponseT]
    ) -> SessionBackendResult[ResponseT]:
        try:
            dispatched: DispatchResult = await self.handler.dispatch(
                method, params.model_dump(mode="json", by_alias=True)
            )
        except RequestFailure as exc:
            raise SessionBackendError(exc.code, str(exc), exc.data) from exc
        response = dispatched.response
        if not isinstance(response, response_type):
            raise TypeError(
                f"Legacy backend returned {type(response).__name__} for {method}"
            )
        if isinstance(response, SessionCompactResponse):
            self.adopt_state(response.state)
        return SessionBackendResult(
            response=cast(ResponseT, response), after_response=dispatched.after_response
        )

    async def _resource_request[ResponseT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResponseT]
    ) -> SessionBackendResult[ResponseT]:
        try:
            dispatched: DispatchResult = await self.resources.dispatch(
                method, params.model_dump(mode="json", by_alias=True)
            )
        except SessionExecutionConflict as exc:
            raise SessionBackendError(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except RequestFailure as exc:
            raise SessionBackendError(exc.code, str(exc), exc.data) from exc
        response = dispatched.response
        if not isinstance(response, response_type):
            raise TypeError(
                f"Legacy backend returned {type(response).__name__} for {method}"
            )
        return SessionBackendResult(
            response=cast(ResponseT, response), after_response=dispatched.after_response
        )


class LegacySessionBackendHost:
    def __init__(
        self,
        *,
        start: _StartSession,
        resume: _ResumeSession,
        continue_latest: _ContinueSession,
        current_session: _CurrentSession,
        host_handler: HostRequestHandler,
        stop_background_tasks: _StopBackgroundTasks | None = None,
        after_lifecycle_response: _AfterLifecycleResponse | None = None,
    ) -> None:
        self._start = start
        self._resume = resume
        self._continue_latest = continue_latest
        self._current_session = current_session
        self._host_handler = host_handler
        self._stop_background_tasks = stop_background_tasks
        self._after_lifecycle_response = after_lifecycle_response
        self._sessions: dict[str, LegacySessionBackend] = {}
        self._closed = False

    @property
    def harness_kind(self) -> SessionBackendKind:
        return "python"

    async def start(self, params: SessionStartParams) -> SessionLifecycleResult:
        backend = self._register(await self._invoke(self._start(params)))
        return SessionLifecycleResult(
            backend=backend, after_response=self._after_response(None)
        )

    async def resume(self, params: SessionResumeParams) -> SessionLifecycleResult:
        backend, after_response = await self._invoke(self._resume(params))
        return SessionLifecycleResult(
            backend=self._register(backend),
            after_response=self._after_response(after_response),
        )

    async def continue_latest(
        self, params: SessionContinueParams
    ) -> SessionLifecycleResult:
        backend = self._register(await self._invoke(self._continue_latest(params)))
        return SessionLifecycleResult(
            backend=backend, after_response=self._after_response(None)
        )

    async def fork(self, params: SessionForkParams) -> SessionForkResult:
        source = self._live_backend(params.source_session_id)
        if source is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND,
                f"Session not found: {params.source_session_id}",
            )
        response = await self._session_request(
            source, "session/fork", params, SessionForkResponse
        )
        backend: LegacySessionBackend | None = None
        if params.attach:
            backend = self._current_session()
            if backend is None or backend.session_id != response.state.session.id:
                raise RuntimeError("The forked session was not attached")
            self._register(backend)
        return SessionForkResult(response=response, backend=backend)

    async def list(self, params: SessionListParams) -> SessionListResponse:
        if backend := self._current_session():
            return await asyncio.to_thread(
                project_session_list, backend.session.agent_loop.config, params
            )
        return await self._host_request("session/list", params, SessionListResponse)

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        if backend := self._live_backend(params.session_id):
            return await backend.read(params)
        if backend := self._current_session():
            try:
                return await backend.read(params)
            except SessionBackendError as exc:
                if exc.code is not ProtocolErrorCode.NOT_FOUND:
                    raise
        return await self._host_request("session/read", params, SessionReadResponse)

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        current = self._current_session()
        sessions = list(self._sessions.values())
        if current is not None and all(current is not session for session in sessions):
            sessions.append(current)
        self._sessions.clear()
        errors: list[BaseException] = []
        for session in sessions:
            try:
                await session.shutdown()
            except BaseException as exc:
                errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close legacy session backends", errors)

    async def stop_background_tasks(self, current: Any) -> list[BaseException]:
        if self._stop_background_tasks is None:
            return []
        return await self._stop_background_tasks(current)

    def _after_response(
        self, action: Callable[[], None] | None
    ) -> Callable[[], None] | None:
        if action is None and self._after_lifecycle_response is None:
            return None

        def run() -> None:
            if action is not None:
                action()
            if self._after_lifecycle_response is not None:
                self._after_lifecycle_response()

        return run

    @staticmethod
    async def _invoke[ResultT](operation: Awaitable[ResultT]) -> ResultT:
        try:
            return await operation
        except SessionBusyError as exc:
            raise SessionBackendError(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except RequestFailure as exc:
            raise SessionBackendError(exc.code, str(exc), exc.data) from exc

    async def _host_request[ResponseT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResponseT]
    ) -> ResponseT:
        try:
            dispatched = await self._host_handler.dispatch(
                method, params.model_dump(mode="json", by_alias=True)
            )
        except RequestFailure as exc:
            raise SessionBackendError(exc.code, str(exc), exc.data) from exc
        response = dispatched.response
        if not isinstance(response, response_type):
            raise TypeError(
                f"Legacy backend host returned {type(response).__name__} for {method}"
            )
        return cast(ResponseT, response)

    @staticmethod
    async def _session_request[ResponseT: ProtocolModel](
        backend: LegacySessionBackend,
        method: str,
        params: ProtocolModel,
        response_type: type[ResponseT],
    ) -> ResponseT:
        try:
            dispatched = await backend.handler.dispatch(
                method, params.model_dump(mode="json", by_alias=True)
            )
        except RequestFailure as exc:
            raise SessionBackendError(exc.code, str(exc), exc.data) from exc
        response = dispatched.response
        if not isinstance(response, response_type):
            raise TypeError(
                f"Legacy backend returned {type(response).__name__} for {method}"
            )
        if dispatched.after_response is not None:
            dispatched.after_response()
        return cast(ResponseT, response)

    def _live_backend(self, session_id: str) -> LegacySessionBackend | None:
        backend = self._sessions.get(session_id)
        if backend is None:
            current = self._current_session()
            if current is not None and current.session_id == session_id:
                return current
            return None
        if backend._closed:
            self._sessions.pop(session_id, None)
            return None
        return backend

    def _register(self, backend: LegacySessionBackend) -> LegacySessionBackend:
        if self._closed:
            raise RuntimeError("The legacy session backend host is closed")
        previous = self._sessions.get(backend.session_id)
        if previous is not None and previous is not backend and not previous._closed:
            raise RuntimeError(
                f"Legacy session backend is already registered: {backend.session_id}"
            )
        self._sessions[backend.session_id] = backend
        return backend
