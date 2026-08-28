from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, cast

from pydantic import JsonValue, ValidationError

from vibe import __version__
from vibe.app_server._account import AccountGateway
from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._execution import cancel_tasks
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._session_backend_port import (
    SessionBackend,
    SessionBackendCallbackSink,
    SessionBackendChildSessionIndex,
    SessionBackendError,
    SessionBackendEvent,
    SessionBackendEventDrain,
    SessionBackendExtension,
    SessionBackendHost,
    SessionBackendHostBackgroundTasks,
    SessionBackendNotificationSink,
    SessionBackendOpenCallbacks,
    SessionBackendResult,
    SessionBackendRuntimeView,
    SessionEventSubscription,
)
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server.connector_catalog import ConnectorCatalogService
from vibe.app_server.events import (
    CallbackRequested,
    ConnectorAuthorizationRequiredEvent,
    MCPAuthorizationRequiredEvent,
)
from vibe.app_server.models import OpenCallbackState, PublicCallbackEntry
from vibe.app_server.protocol import (
    SERVER_METHODS,
    AgentSwitchParams,
    AppServerResponseError,
    CallbackCallParams,
    CallbackCallResponse,
    CallbackResultError,
    CallbackResultParams,
    ClientCapabilities,
    ClientInfo,
    ConfigReloadParams,
    ConfigWriteParams,
    ContextInjectParams,
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
    PageRequest,
    ProtocolError,
    ProtocolErrorCode,
    ServerInfo,
    ServerRequest,
    SessionCompactParams,
    SessionContinueParams,
    SessionContinueResponse,
    SessionDeleteParams,
    SessionForkParams,
    SessionHandoffParams,
    SessionListParams,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    SessionResumeResponse,
    SessionSettingsUpdateParams,
    SessionSnapshotParams,
    SessionStartParams,
    SessionStartResponse,
    TransportKind,
    TurnInterruptParams,
    TurnStartParams,
    TurnSteerParams,
    validate_callback_acknowledgement,
    validate_json_rpc_envelope,
)
from vibe.app_server.transport import JsonRpcTransport
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.app_server.mcp_catalog import MCPCatalogService
    from vibe.app_server.plugin_catalog import PluginCatalogService


class InitializationState(StrEnum):
    UNINITIALIZED = auto()
    INITIALIZE_RECEIVED = auto()
    INITIALIZED = auto()


type SessionBackendHostFactory = Callable[[SessionBackendServices], SessionBackendHost]


_SESSION_ATTACHMENT_CANDIDATES = frozenset({
    "session/fork",
    "session/start",
    "session/resume",
    "session/continue",
})

_SESSION_BACKEND_HOST_LIFECYCLE_METHODS = frozenset({
    "session/start",
    "session/resume",
    "session/continue",
})

_SESSION_OPTIONAL_METHODS = frozenset({"config/read", "workspace/trust/decision"})

_SESSION_BACKEND_METHODS = frozenset({
    "callback/result",
    "config/reload",
    "config/write",
    "session/agent/update",
    "session/compact",
    "session/context/inject",
    "session/settings/update",
    "turn/interrupt",
    "turn/start",
    "turn/steer",
})


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
class PendingNotification:
    method: str
    params: ProtocolModel


class AppServer:
    def __init__(
        self,
        transport: JsonRpcTransport,
        *,
        session_backend_host_factory: SessionBackendHostFactory,
        transport_kind: TransportKind = "in_process",
        account_gateway: AccountGateway | None = None,
        identity_gateway: IdentityGateway | None = None,
        host_handler: HostRequestHandler | None = None,
        mcp_catalog_service: MCPCatalogService | None = None,
        connector_catalog_service: ConnectorCatalogService | None = None,
        plugin_catalog_service: PluginCatalogService | None = None,
    ) -> None:
        self._root: SessionBackend | None = None
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
        self._backend_event_task: asyncio.Task[None] | None = None
        self._backend_event_backend: SessionBackend | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._connection_attached = False
        self._attaching = False
        self._pending_notifications: list[PendingNotification] = []
        self._request_error: BaseException | None = None
        self._close_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._lifecycle_lock_owner: asyncio.Task[Any] | None = None
        self._connection_lock = asyncio.Lock()
        self._attachment_lock = asyncio.Lock()
        self._closed = False
        self._shutdown_complete = False
        self._account_gateway = account_gateway
        self._identity_gateway = identity_gateway
        self._mcp_catalog_service = mcp_catalog_service
        self._connector_catalog_service = connector_catalog_service
        self._plugin_catalog_service = plugin_catalog_service
        self._session_backend_host = session_backend_host_factory(self)
        if self._session_backend_host is None:
            raise TypeError(
                "session_backend_host_factory must return a SessionBackendHost"
            )

    def _require_root(self) -> SessionBackend:
        if self._root is None:
            raise RuntimeError("No app-server session runtime is active")
        return self._root

    async def _activate_backend(
        self, backend: SessionBackend, history_limit: int
    ) -> SessionReadResponse:
        params = SessionReadParams(
            session_id=backend.session_id, history=PageRequest(limit=history_limit)
        )
        if backend is self._backend_event_backend:
            return await backend.read(params)
        subscription = await backend.subscribe(params)
        previous = self._root
        self._root = backend
        await self._replace_backend_event_task(backend, subscription)
        if previous is not None and previous is not backend:
            try:
                await previous.shutdown()
            except Exception as exc:
                logger.warning(
                    "Failed to shut down previous session backend", exc_info=exc
                )
            finally:
                if self._connector_catalog_service is not None:
                    self._connector_catalog_service.discard_session(previous.session_id)
        await self._replay_pending_backend_events(
            backend, subscription.snapshot.last_event_id
        )
        await self._flush_backend_events(backend)
        return subscription.snapshot

    async def _replace_backend_event_task(
        self, backend: SessionBackend, subscription: SessionEventSubscription
    ) -> None:
        previous = self._backend_event_task
        if previous is not None:
            previous.cancel()
            with suppress(asyncio.CancelledError):
                await previous
        task = asyncio.create_task(
            self._forward_backend_events(subscription), name="vibe-backend-events"
        )
        self._backend_event_task = task
        self._backend_event_backend = backend
        task.add_done_callback(self._backend_event_finished)

    async def _forward_backend_events(
        self, subscription: SessionEventSubscription
    ) -> None:
        async for envelope in subscription.events:
            await self._forward_backend_event(envelope)

    async def _forward_backend_event(self, envelope: SessionBackendEvent) -> None:
        if isinstance(envelope.event, CallbackRequested):
            await self._deliver_callback(envelope.event.callback)
        elif isinstance(envelope.event, ConnectorAuthorizationRequiredEvent):
            await self._forward_connector_authorization(envelope.event)
        elif isinstance(envelope.event, MCPAuthorizationRequiredEvent):
            await self._forward_mcp_authorization(envelope.event)
        else:
            if envelope.method is None or envelope.params is None:
                raise RuntimeError(
                    "Session backend event has no Vibe notification mapping"
                )
            await self._route_notification(envelope.method, envelope.params)
        if envelope.method == "turn/completed" and envelope.session_id is not None:
            service = self._connector_catalog_service
            if service is not None:
                runtime_updated = await service.converge_pending_connector_candidate(
                    envelope.session_id, self._root
                )
                if runtime_updated is not None:
                    await self._route_notification("runtime/updated", runtime_updated)

    async def _forward_connector_authorization(
        self, event: ConnectorAuthorizationRequiredEvent
    ) -> None:
        service = self._connector_catalog_service
        if service is None or event.raw_connector_id is None or event.action is None:
            return
        authorization = await service.accept_auth_required(
            event.params,
            raw_connector_id=event.raw_connector_id,
            action=event.action,
            root=self._root,
            notify=self._notify,
        )
        if authorization is None:
            return
        try:
            await self._route_notification(
                "runtime/updated", authorization.runtime_updated
            )
            await self._route_notification(
                "connector_catalog/authRequired", event.params
            )
        except BaseException:
            authorization.release_reservation()
            raise
        authorization.start_broker()

    async def _forward_mcp_authorization(
        self, event: MCPAuthorizationRequiredEvent
    ) -> None:
        service = self._mcp_catalog_service
        if service is None:
            return
        runtime_updated = await service.accept_auth_required(event.params, self._root)
        if runtime_updated is None:
            return
        await self._route_notification("mcp_catalog/authRequired", event.params)
        await self._route_notification("runtime/updated", runtime_updated)

    def _backend_event_finished(self, task: asyncio.Task[None]) -> None:
        if self._backend_event_task is task:
            self._backend_event_task = None
            self._backend_event_backend = None
        if task.cancelled():
            return
        error = task.exception()
        if error is None or self._closed:
            return
        self._request_error = error
        if self._serve_task is not None:
            self._serve_task.cancel()

    async def _replay_pending_backend_events(
        self, backend: SessionBackend, snapshot_event_id: int
    ) -> None:
        pending = self._pending_notifications
        self._pending_notifications = []
        for notification in pending:
            params = notification.params
            if not isinstance(params, EventNotificationParams):
                self._pending_notifications.append(notification)
                continue
            if params.event_id <= snapshot_event_id:
                continue
            if not isinstance(backend, SessionBackendNotificationSink):
                self._pending_notifications.append(notification)
                continue
            if await backend.publish_notification(notification.method, params):
                await self._flush_backend_events(backend)
                continue
            self._pending_notifications.append(notification)

    @staticmethod
    async def _flush_backend_events(backend: SessionBackend) -> None:
        if isinstance(backend, SessionBackendEventDrain):
            await backend.flush_events()

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
        async with self._lifecycle_transition():
            root = self._root
            try:
                await self._session_backend_host.shutdown()
            finally:
                self._root = None
                if root is not None and self._connector_catalog_service is not None:
                    self._connector_catalog_service.discard_session(root.session_id)

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
        backend_events = self._backend_event_task
        self._backend_event_task = None
        self._backend_event_backend = None
        if backend_events is not None and backend_events is not current:
            tasks.append(backend_events)
        errors = await cancel_tasks(tasks, label="app-server background")
        if isinstance(self._session_backend_host, SessionBackendHostBackgroundTasks):
            errors.extend(
                await self._session_backend_host.stop_background_tasks(current)
            )
        return errors

    async def _stop_request_tasks(
        self, current: asyncio.Task[object] | None
    ) -> list[BaseException]:
        tasks = [task for task in self._request_tasks if task is not current]
        backend_events = self._backend_event_task
        self._backend_event_task = None
        self._backend_event_backend = None
        if backend_events is not None and backend_events is not current:
            tasks.append(backend_events)
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
        except (RequestFailure, SessionBackendError) as exc:
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
            logger.exception("Unhandled error dispatching %s", request.method)
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
            if not isinstance(self._root, SessionBackendRuntimeView):
                raise RuntimeError(
                    "The selected backend cannot project its runtime state"
                )
            await self._notify("runtime/updated", self._root.runtime_updated_params())
        if request.method == "session/stop":
            await self.close()

    async def _dispatch_request(  # noqa: PLR0911 - explicit route ownership
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method not in SERVER_METHODS:
            raise method_not_found(method)
        if method == "events/read":
            return self._events_read(raw_params)
        if self._mcp_catalog_service is not None and self._mcp_catalog_service.handles(
            method
        ):
            return await self._mcp_catalog_service.dispatch(
                method, raw_params, root=self._root, notify=self._notify
            )
        if (
            self._connector_catalog_service is not None
            and self._connector_catalog_service.handles(method)
        ):
            return await self._connector_catalog_service.dispatch(
                method, raw_params, root=self._root, notify=self._route_notification
            )
        if (
            self._plugin_catalog_service is not None
            and self._plugin_catalog_service.handles(method)
        ):
            return await self._plugin_catalog_service.dispatch(
                method, raw_params, root=self._root
            )
        if method in {
            "config/schema",
            "session/history/get",
            "workspace/git/checkouts",
            "workspace/git/worktrees/list",
            "workspace/git/worktrees/remove",
            "workspace/trust/status",
            "workspace/trust/untrustedConfig",
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
        is_root = root is not None and params.session_id == root.session_id
        references_child = isinstance(
            root, SessionBackendChildSessionIndex
        ) and root.references_child(params.session_id)
        if is_root or references_child:
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT, "Deleting a live session is not supported"
            )
        return await self._host_handler.dispatch("session/delete", raw_params)

    async def _dispatch_without_root(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method in {"session/start", "session/resume", "session/continue"}:
            return await self._open_initial_session(method, raw_params)
        if result := await self._dispatch_backend_host_operation(method, raw_params):
            return result
        if self._host_handler.handles(method):
            return await self._host_handler.dispatch(method, raw_params)
        return await self._open_initial_session(method, raw_params)

    async def _dispatch_to_root(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        root = self._root
        if root is None:
            raise RuntimeError("Root session closed while dispatching a request")
        host_result = await self._dispatch_backend_host_operation(method, raw_params)
        if host_result is not None:
            return host_result
        root.guard_request()
        backend_result = await self._dispatch_backend_operation(
            root, method, raw_params
        )
        if backend_result is not None:
            await self._flush_backend_events(root)
            return backend_result
        if method in _SESSION_BACKEND_METHODS:
            raise RuntimeError(f"Session backend method was not routed: {method}")
        if not isinstance(root, SessionBackendExtension):
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                f"The selected session backend does not support {method}",
            )
        try:
            result = await root.dispatch_extension(method, raw_params)
        except SessionBackendError as exc:
            if exc.code is ProtocolErrorCode.NOT_FOUND and method in {
                "session/read",
                "session/rename",
                "session/history/list",
            }:
                return await self._host_handler.dispatch(method, raw_params)
            raise
        await self._flush_backend_events(root)
        return result

    async def _dispatch_backend_host_operation(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "session/list":
            response = await self._session_backend_host.list(
                validate_wire(SessionListParams, raw_params)
            )
            return DispatchResult(response)
        if method == "session/read":
            response = await self._session_backend_host.read(
                validate_wire(SessionReadParams, raw_params)
            )
            return DispatchResult(response)
        if method == "session/fork":
            params = validate_wire(SessionForkParams, raw_params)
            result = await self._session_backend_host.fork(params)
            response = result.response
            if result.backend is not None:
                snapshot = await self._activate_backend(
                    result.backend, params.history_limit
                )
                response = response.model_copy(
                    update={
                        "state": snapshot.state,
                        "last_event_id": snapshot.last_event_id,
                    }
                )
            return DispatchResult(response, session_attached=result.backend is not None)
        return await self._dispatch_backend_host_lifecycle(method, raw_params)

    async def _dispatch_backend_host_lifecycle(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method not in _SESSION_BACKEND_HOST_LIFECYCLE_METHODS:
            return None
        async with self._lifecycle_transition():
            return await self._dispatch_backend_host_lifecycle_locked(
                method, raw_params
            )

    async def _dispatch_backend_host_lifecycle_locked(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method == "session/start":
            params = validate_wire(SessionStartParams, raw_params)
            lifecycle = await self._session_backend_host.start(params)
            self._record_session_created(lifecycle.backend)
            response = await self._activate_backend(
                lifecycle.backend, params.history_limit
            )
            return DispatchResult(
                SessionStartResponse(
                    state=response.state, last_event_id=response.last_event_id
                ),
                after_response=lifecycle.after_response,
                session_attached=True,
            )
        if method == "session/resume":
            params = validate_wire(SessionResumeParams, raw_params)
            lifecycle = await self._session_backend_host.resume(params)
            response = await self._activate_backend(
                lifecycle.backend, params.history_limit
            )
            return DispatchResult(
                SessionResumeResponse(
                    state=response.state, last_event_id=response.last_event_id
                ),
                after_response=lifecycle.after_response,
                session_attached=True,
                runtime_updated=True,
            )
        if method == "session/continue":
            params = validate_wire(SessionContinueParams, raw_params)
            lifecycle = await self._session_backend_host.continue_latest(params)
            response = await self._activate_backend(
                lifecycle.backend, params.history_limit
            )
            return DispatchResult(
                SessionContinueResponse(
                    state=response.state, last_event_id=response.last_event_id
                ),
                after_response=lifecycle.after_response,
                session_attached=True,
                runtime_updated=True,
            )
        raise RuntimeError(f"Unsupported session lifecycle method: {method}")

    def _record_session_created(self, backend: SessionBackend) -> None:
        logger.debug(
            "Session created: harness=%s session_id=%s",
            self._session_backend_host.harness_kind,
            backend.session_id,
        )

    async def _dispatch_backend_operation(
        self, root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        match method.partition("/")[0]:
            case "session":
                return await self._dispatch_backend_session(root, method, raw_params)
            case "turn":
                return await self._dispatch_backend_turn(root, method, raw_params)
            case "config":
                return await self._dispatch_backend_config(root, method, raw_params)
            case "callback":
                return await self._dispatch_backend_callback(root, method, raw_params)
        return None

    @staticmethod
    async def _dispatch_backend_session(
        root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "session/agent/update":
            result = await root.switch_agent(
                validate_wire(AgentSwitchParams, raw_params)
            )
            return DispatchResult(
                result.response,
                after_response=result.after_response,
                runtime_updated=True,
            )
        if method == "session/settings/update":
            result = await root.update_settings(
                validate_wire(SessionSettingsUpdateParams, raw_params)
            )
            return DispatchResult(result.response, after_response=result.after_response)
        if method == "session/context/inject":
            result = await root.inject_context(
                validate_wire(ContextInjectParams, raw_params)
            )
            return DispatchResult(result.response, after_response=result.after_response)
        if method == "session/compact":
            result = await root.compact(validate_wire(SessionCompactParams, raw_params))
            return DispatchResult(result.response, after_response=result.after_response)
        return None

    @staticmethod
    async def _dispatch_backend_turn(
        root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "turn/start":
            result = await root.start_turn(validate_wire(TurnStartParams, raw_params))
        elif method == "turn/steer":
            result = await root.steer_turn(validate_wire(TurnSteerParams, raw_params))
        elif method == "turn/interrupt":
            result = await root.interrupt_turn(
                validate_wire(TurnInterruptParams, raw_params)
            )
        else:
            return None
        return DispatchResult(result.response, after_response=result.after_response)

    async def _dispatch_backend_config(
        self, root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "config/write":
            result = await root.write_config(
                validate_wire(ConfigWriteParams, raw_params)
            )
            runtime_updated = (
                not result.response.rejected and not result.response.failures
            )
        elif method == "config/reload":
            plan = (
                await self._mcp_catalog_service.prepare_config_reload(root)
                if self._mcp_catalog_service is not None
                else None
            )
            try:
                result = await root.reload_config(
                    validate_wire(ConfigReloadParams, raw_params)
                )
                mcp_runtime = (
                    await self._mcp_catalog_service.finish_config_reload(root, plan)
                    if self._mcp_catalog_service is not None
                    else None
                )
            except Exception:
                if self._mcp_catalog_service is not None:
                    await self._mcp_catalog_service.fail_config_reload(root, plan)
                raise
            if mcp_runtime is not None:
                result = SessionBackendResult(
                    response=result.response.model_copy(
                        update={"runtime": mcp_runtime}
                    ),
                    after_response=result.after_response,
                )
            runtime_updated = True
        else:
            return None
        return DispatchResult(
            result.response,
            after_response=result.after_response,
            runtime_updated=runtime_updated,
        )

    @staticmethod
    async def _dispatch_backend_callback(
        root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method != "callback/result":
            return None
        result = await root.respond_to_callback(
            validate_wire(CallbackResultParams, raw_params)
        )
        return DispatchResult(result.response, after_response=result.after_response)

    async def _open_initial_session(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        async with self._lifecycle_transition():
            root = self._root
            if root is None:
                return await self._open_initial_session_locked(method, raw_params)
        return await self._dispatch_to_root(method, raw_params)

    @asynccontextmanager
    async def _lifecycle_transition(self) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if task is not None and task is self._lifecycle_lock_owner:
            yield
            return
        async with self._lifecycle_lock:
            self._lifecycle_lock_owner = task
            try:
                yield
            finally:
                self._lifecycle_lock_owner = None

    async def _open_initial_session_locked(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        result = await self._dispatch_backend_host_operation(method, raw_params)
        if result is not None:
            return result
        raise RequestFailure(
            ProtocolErrorCode.CONFLICT,
            "Start, resume, or continue a session before using this method",
        )

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
        params = self._sequence_notification(params)
        root = self._root
        if isinstance(root, SessionBackendNotificationSink):
            if await root.publish_notification(method, params):
                return
        await self._route_notification(method, params)

    async def _route_notification(self, method: str, params: ProtocolModel) -> None:
        if (
            method in self._client_capabilities.disabled_notifications
            and not isinstance(params, EventNotificationParams)
        ):
            return
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

    async def _publish_callback(self, callback: PublicCallbackEntry) -> None:
        root = self._root
        if isinstance(root, SessionBackendCallbackSink):
            if await root.publish_callback(callback):
                return
        await self._deliver_callback(callback)

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
        root = self._root
        if not isinstance(root, SessionBackendOpenCallbacks):
            return
        for callback in root.open_callbacks():
            if not isinstance(callback.state, OpenCallbackState):
                continue
            await self._deliver_callback(callback)

    def _validate_open_callback_capabilities(self) -> None:
        root = self._root
        if not isinstance(root, SessionBackendOpenCallbacks):
            return
        required = {
            callback.detail.kind
            for callback in root.open_callbacks()
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

    def client_info(self) -> ClientInfo:
        return self._initialized_client_info()

    def client_capabilities(self) -> ClientCapabilities:
        return self._client_capabilities

    def current_session_id(self) -> str:
        return self._require_root().session_id

    def event_watermark(self, session_id: str) -> int:
        return self._event_watermark(session_id)

    def account_gateway(self) -> AccountGateway | None:
        return self._account_gateway

    def identity_gateway(self) -> IdentityGateway | None:
        return self._identity_gateway

    def lifecycle_transition(self) -> AbstractAsyncContextManager[None]:
        return self._lifecycle_transition()

    def task_finished(self, task: asyncio.Task[None]) -> None:
        self._request_finished(task)

    async def notify(self, method: str, params: ProtocolModel) -> None:
        await self._notify(method, params)

    async def publish_callback(self, callback: PublicCallbackEntry) -> None:
        await self._publish_callback(callback)

    async def record_child_notification(
        self, method: str, params: ProtocolModel
    ) -> None:
        await self._record_child_notification(method, params)

    async def request_client_result[ResultT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResultT]
    ) -> ResultT:
        return await self._request_client_result(method, params, response_type)

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
        root = self._root
        if isinstance(root, SessionBackendOpenCallbacks):
            await root.reject_callback_delivery(session_id, callback_id, error)
            return
        raise RuntimeError("The selected backend cannot reject callback delivery")

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


def _notification_session_id(params: EventNotificationParams) -> str:
    return params.session_id
