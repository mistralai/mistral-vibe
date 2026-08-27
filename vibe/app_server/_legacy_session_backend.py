from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from vibe.app_server._dispatch import DispatchResult, RequestFailure
from vibe.app_server._execution import SessionExecutionConflict, SessionExecutionKind
from vibe.app_server._handler import CoreRequestHandler
from vibe.app_server._host import HostRequestHandler, project_session_list
from vibe.app_server._model import ProtocolModel
from vibe.app_server._resources import ResourceRequestHandler
from vibe.app_server._root_session import RootSessionCoordinator
from vibe.app_server._session_backend_port import (
    ConnectorAuthRequest,
    MCPAuthorizationProvider,
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationSnapshot,
    ResolvedConnector,
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
    ResolvedMCPCatalog,
    SessionBackendError,
    SessionBackendEvent,
    SessionBackendKind,
    SessionBackendResult,
    SessionConnectorSourceState,
    SessionConnectorState,
    SessionConnectorToolDescriptor,
    SessionEventSubscription,
    SessionForkResult,
    SessionLifecycleResult,
    SessionMCPSourceState,
    SessionMCPState,
    SessionMCPToolDescriptor,
)
from vibe.app_server._sessions import SessionRuntime, SessionRuntimeRegistry
from vibe.app_server._streaming import finish_event_queue
from vibe.app_server.connector_catalog import (
    connector_source_enabled,
    connector_tool_enabled,
)
from vibe.app_server.events import (
    CallbackRequested,
    ClientProjection,
    EventSequenceError,
    MCPAuthorizationRequiredEvent,
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
    MCPAuthRequiredParams,
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
    SessionRelocateResponse,
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
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.git.worktree import ManagedWorktree, PreparedWorktree
from vibe.core.session.session_lease import SessionBusyError
from vibe.core.tools.connectors.connector_registry import (
    ConnectorAuthAction,
    ConnectorCatalogEntry,
    ConnectorToolDefinition,
)
from vibe.core.tools.mcp.authorization import (
    MCPAuthorizationProvider as LegacyMCPAuthorizationProvider,
    MCPAuthorizationRef as LegacyMCPAuthorizationRef,
    MCPAuthorizationRequired as LegacyMCPAuthorizationRequired,
    MCPAuthorizationSnapshot as LegacyMCPAuthorizationSnapshot,
)
from vibe.core.tools.mcp.registry import MCPRegistry
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


class _LegacyMCPAuthorizationProviderAdapter(LegacyMCPAuthorizationProvider):
    def __init__(self, provider: MCPAuthorizationProvider) -> None:
        self._provider = provider

    async def resolve(
        self, reference: LegacyMCPAuthorizationRef
    ) -> LegacyMCPAuthorizationSnapshot | LegacyMCPAuthorizationRequired:
        result = await self._provider.resolve(_app_authorization_ref(reference))
        return _legacy_authorization_result(result)

    async def reject(
        self,
        reference: LegacyMCPAuthorizationRef,
        *,
        observed_connection_revision: str,
        reason: str,
    ) -> LegacyMCPAuthorizationSnapshot | LegacyMCPAuthorizationRequired:
        if reason not in {"http_unauthorized", "mcp_unauthorized"}:
            raise ValueError("Unsupported MCP authorization rejection reason")
        result = await self._provider.reject(
            _app_authorization_ref(reference),
            observed_connection_revision=observed_connection_revision,
            reason=cast(Any, reason),
        )
        return _legacy_authorization_result(result)


def _app_authorization_ref(reference: LegacyMCPAuthorizationRef) -> MCPAuthorizationRef:
    return MCPAuthorizationRef(
        server_name=reference.server_name,
        server_fingerprint=reference.server_fingerprint,
        kind=reference.kind,
        descriptor_revision=reference.descriptor_revision,
    )


def _legacy_authorization_result(
    result: MCPAuthorizationSnapshot | MCPAuthorizationRequired,
) -> LegacyMCPAuthorizationSnapshot | LegacyMCPAuthorizationRequired:
    if isinstance(result, MCPAuthorizationRequired):
        return LegacyMCPAuthorizationRequired(
            reason=result.reason,
            descriptor_revision=result.descriptor_revision,
            observed_connection_revision=result.observed_connection_revision,
        )
    return LegacyMCPAuthorizationSnapshot(
        headers=result.headers,
        connection_revision=result.connection_revision,
        descriptor_revision=result.descriptor_revision,
        expires_at=result.expires_at,
    )


def configure_legacy_mcp_registry(
    registry: MCPRegistry,
    configuration: ResolvedMCPCatalog,
    provider: MCPAuthorizationProvider,
    *,
    required_sink: Callable[
        [str, LegacyMCPAuthorizationRequired], Awaitable[None] | None
    ]
    | None = None,
    descriptor_cache_root: Path | None = None,
) -> None:
    registry.configure_authorization(
        _LegacyMCPAuthorizationProviderAdapter(provider),
        {
            server.name: LegacyMCPAuthorizationRef(
                server_name=server.authorization.server_name,
                server_fingerprint=server.authorization.server_fingerprint,
                kind=server.authorization.kind,
                descriptor_revision=server.authorization.descriptor_revision,
            )
            for server in configuration.servers
        },
        required_sink=required_sink,
        descriptor_cache_root=descriptor_cache_root,
    )


def create_legacy_mcp_registry(
    configuration: ResolvedMCPCatalog,
    provider: MCPAuthorizationProvider,
    *,
    descriptor_cache_root: Path | None = None,
) -> MCPRegistry:
    registry = MCPRegistry()
    configure_legacy_mcp_registry(
        registry, configuration, provider, descriptor_cache_root=descriptor_cache_root
    )
    return registry


def _legacy_connector_entries(
    catalog: ResolvedConnectorCatalog,
) -> tuple[ConnectorCatalogEntry, ...]:
    return tuple(
        ConnectorCatalogEntry(
            connector_id=connector.raw_id,
            alias=connector.alias,
            display_name=connector.display_name,
            ready=connector.ready,
            auth_action=(
                ConnectorAuthAction(connector.auth_action)
                if connector.auth_action != "unknown"
                else ConnectorAuthAction.NONE
            ),
            tools=tuple(
                ConnectorToolDefinition(
                    name=tool.raw_name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema),
                )
                for tool in connector.tools
            ),
            diagnostic="; ".join(connector.diagnostics) or None,
        )
        for connector in catalog.connectors
    )


def _legacy_connector_source(
    connector: ResolvedConnector,
    selection: ResolvedConnectorSelection,
    *,
    connected: bool,
    suspended: set[tuple[str, str | None]],
) -> SessionConnectorSourceState:
    source_enabled = (
        connector_source_enabled(selection, connector.alias)
        and (connector.alias, None) not in suspended
    )
    if not source_enabled:
        status = "disabled"
    elif not connector.ready and connector.auth_action == "oauth":
        status = "needs_auth"
    elif not connector.ready and connector.auth_action == "credentials_setup":
        status = "needs_setup"
    elif not connector.ready or connector.auth_action == "unknown":
        status = "unavailable"
    elif connected:
        status = "connected"
    else:
        status = "unavailable"
    return SessionConnectorSourceState(
        raw_id=connector.raw_id,
        alias=connector.alias,
        display_name=connector.display_name,
        status=cast(Any, status),
        tools=tuple(
            SessionConnectorToolDescriptor(
                raw_name=tool.raw_name,
                description=tool.description,
                enabled=(
                    connector_tool_enabled(
                        selection, alias=connector.alias, raw_tool_name=tool.raw_name
                    )
                    and (connector.alias, None) not in suspended
                    and (connector.alias, tool.raw_name) not in suspended
                ),
                display_name=f"connector_{connector.alias}_{tool.raw_name}",
            )
            for tool in connector.tools
        ),
        error="; ".join(connector.diagnostics) or None,
    )


@dataclass(slots=True)  # noqa: PLR0904 - implements narrow app-server ports
class LegacySessionBackend:
    session: SessionRuntime
    resources: ResourceRequestHandler
    coordinator: RootSessionCoordinator
    handler: CoreRequestHandler
    children: SessionRuntimeRegistry
    record_last_session: Callable[..., None]
    mcp_catalog: ResolvedMCPCatalog | None = None
    mcp_authorization_provider: MCPAuthorizationProvider | None = None
    mcp_descriptor_cache_root: Path | None = None
    connector_catalog: ResolvedConnectorCatalog | None = None
    connector_selection: ResolvedConnectorSelection | None = None
    created_worktree: PreparedWorktree | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _projection: ClientProjection | None = field(default=None, init=False, repr=False)
    _events: asyncio.Queue[_QueuedEvent] = field(
        default_factory=lambda: asyncio.Queue(maxsize=256), init=False, repr=False
    )
    _events_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _events_idle: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _events_subscribed: bool = field(default=False, init=False, repr=False)
    _mcp_catalog_revision: str = field(default="", init=False, repr=False)
    _mcp_route_revision: int = field(default=0, init=False, repr=False)
    _connector_route_revision: int = field(default=0, init=False, repr=False)
    _suspended_connectors: set[tuple[str, str | None]] = field(
        default_factory=set, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._events_idle.set()
        self._configure_mcp_registry()

    @property
    def connector_config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        return self.session.agent_loop.config_orchestrator

    async def read_connectors(self) -> SessionConnectorState:
        return self._session_connector_state()

    async def reconfigure_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        force: bool,
    ) -> SessionConnectorState:
        del force
        self._require_mcp_idle()
        registry = self.session.agent_loop.connector_registry
        if registry is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "The legacy Runtime has no host-managed connector executor",
            )
        registry.reconfigure(_legacy_connector_entries(catalog))
        self.connector_catalog = catalog
        self.connector_selection = selection
        self._suspended_connectors.clear()
        manager = self.session.agent_loop.tool_manager
        manager.set_connector_registry(registry)
        await manager.integrate_connectors_async()
        await self.session.agent_loop.refresh_system_prompt()
        self._connector_route_revision += 1
        return self._session_connector_state()

    async def suspend_connectors(
        self, *, name: str, tool_name: str | None, reason: str
    ) -> SessionConnectorState:
        if reason not in {"disable", "replace", "gateway_rejected"}:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, "Invalid connector suspension reason"
            )
        self._require_mcp_idle()
        self.session.agent_loop.tool_manager.suspend_connector(name, tool_name)
        self._suspended_connectors.add((name, tool_name))
        await self.session.agent_loop.refresh_system_prompt()
        self._connector_route_revision += 1
        return self._session_connector_state()

    async def request_connector_auth(self, *, alias: str) -> ConnectorAuthRequest:
        state = self._session_connector_state()
        source = next((item for item in state.sources if item.alias == alias), None)
        if source is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Connector not found: {alias}"
            )
        if source.status not in {"needs_auth", "needs_setup"}:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                f"Connector authorization is not actionable: {alias}",
            )
        catalog = self.connector_catalog
        if catalog is None:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "The connector catalog is not accepted"
            )
        connector = next(item for item in catalog.connectors if item.alias == alias)
        return ConnectorAuthRequest(
            session_id=self.session_id,
            raw_connector_id=connector.raw_id,
            alias=alias,
            accepted_catalog_revision=catalog.revision,
            action=connector.auth_action,
            reason="needs_auth" if source.status == "needs_auth" else "needs_setup",
        )

    def _session_connector_state(self) -> SessionConnectorState:
        catalog = self.connector_catalog
        selection = self.connector_selection
        if catalog is None or selection is None:
            return SessionConnectorState(
                accepted_catalog_revision="",
                accepted_selection_revision="",
                route_revision=f"legacy-connector-routes:{self._connector_route_revision}",
                sources=(),
                discovery_errors={},
            )
        registry = self.session.agent_loop.connector_registry
        sources = tuple(
            _legacy_connector_source(
                connector,
                selection,
                connected=(
                    registry.is_connected(connector.alias)
                    if registry is not None
                    else False
                ),
                suspended=self._suspended_connectors,
            )
            for connector in catalog.connectors
        )
        return SessionConnectorState(
            accepted_catalog_revision=catalog.revision,
            accepted_selection_revision=selection.selection_revision,
            route_revision=f"legacy-connector-routes:{self._connector_route_revision}",
            sources=sources,
            discovery_errors={
                source.alias: source.error
                for source in sources
                if source.error is not None
            },
        )

    def _configure_mcp_registry(self) -> None:
        catalog = self.mcp_catalog
        provider = self.mcp_authorization_provider
        if catalog is None or provider is None:
            return
        registry = self.session.agent_loop.mcp_registry
        if registry is None and self.session.agent_loop.config.mcp_servers:
            registry = MCPRegistry()
            self.session.agent_loop.mcp_registry = registry
            self.session.agent_loop.tool_manager.set_mcp_registry(registry)
        if registry is None:
            return
        configure_legacy_mcp_registry(
            registry,
            catalog,
            provider,
            required_sink=self._publish_mcp_authorization_required,
            descriptor_cache_root=self.mcp_descriptor_cache_root,
        )

    async def _publish_mcp_authorization_required(
        self,
        name: str,
        required: MCPAuthorizationRequired | LegacyMCPAuthorizationRequired,
    ) -> None:
        params = MCPAuthRequiredParams(
            session_id=self.session_id,
            name=name,
            descriptor_revision=required.descriptor_revision,
            observed_connection_revision=required.observed_connection_revision,
        )
        self._events_idle.clear()
        await self._events.put(
            SessionBackendEvent(
                event=MCPAuthorizationRequiredEvent(params),
                method="mcp_catalog/authRequired",
                params=params,
                session_id=self.session_id,
            )
        )

    @property
    def session_id(self) -> str:
        return self.session.agent_loop.session_id

    @property
    def mcp_config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        return self.session.agent_loop.config_orchestrator

    async def read_mcp(self) -> SessionMCPState:
        return self._session_mcp_state()

    async def reconfigure_mcp(
        self, configuration: ResolvedMCPCatalog, *, force_remote_discovery: bool
    ) -> SessionMCPState:
        self._require_mcp_idle()
        current_names = tuple(
            server.name for server in self.session.agent_loop.config.mcp_servers
        )
        if current_names != tuple(server.name for server in configuration.servers):
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                "The resolved MCP catalog does not match the session configuration",
            )
        self.mcp_catalog = configuration
        self._configure_mcp_registry()
        self._mcp_catalog_revision = configuration.revision
        manager = self.session.agent_loop.tool_manager
        if force_remote_discovery:
            await manager.refresh_remote_tools_async()
        else:
            await manager.reconfigure_mcp_async()
        await self.session.agent_loop.refresh_system_prompt()
        self._mcp_route_revision += 1
        return self._session_mcp_state()

    async def authorization_changed(
        self, *, name: str, descriptor_revision: str
    ) -> SessionMCPState:
        self._require_mcp_idle()
        registry = self.session.agent_loop.mcp_registry
        if registry is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, "No MCP servers are configured"
            )
        catalog = self.mcp_catalog
        provider = self.mcp_authorization_provider
        resolved = (
            next((server for server in catalog.servers if server.name == name), None)
            if catalog is not None
            else None
        )
        if catalog is None or resolved is None or provider is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"MCP server not found: {name}"
            )
        resolved = replace(
            resolved,
            authorization=replace(
                resolved.authorization, descriptor_revision=descriptor_revision
            ),
        )
        self.mcp_catalog = replace(
            catalog,
            servers=tuple(
                resolved if server.name == name else server
                for server in catalog.servers
            ),
        )
        self._configure_mcp_registry()
        manager = self.session.agent_loop.tool_manager
        if resolved.disabled:
            registry.record_disabled_authorization(name, descriptor_revision)
            manager.suspend_mcp(name)
            await self.session.agent_loop.refresh_system_prompt()
            self._mcp_route_revision += 1
            return self._session_mcp_state()
        authorization = await provider.resolve(resolved.authorization)
        if authorization.descriptor_revision != descriptor_revision:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                "The MCP authorization descriptor revision is stale",
            )
        if isinstance(authorization, MCPAuthorizationRequired):
            registry.mark_needs_auth(name, descriptor_revision)
            manager.suspend_mcp(name)
            await self._publish_mcp_authorization_required(name, authorization)
            await self.session.agent_loop.refresh_system_prompt()
            self._mcp_route_revision += 1
            return self._session_mcp_state()
        registry.invalidate(name)
        await manager.reconfigure_mcp_async()
        await self.session.agent_loop.refresh_system_prompt()
        self._mcp_route_revision += 1
        return self._session_mcp_state()

    async def suspend_mcp(
        self, *, name: str, tool_name: str | None, reason: str
    ) -> SessionMCPState:
        if reason not in {"logout", "remove", "disable", "replace"}:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, "Invalid MCP suspension reason"
            )
        self._require_mcp_idle()
        self.session.agent_loop.tool_manager.suspend_mcp(name, tool_name)
        await self.session.agent_loop.refresh_system_prompt()
        self._mcp_route_revision += 1
        return self._session_mcp_state()

    def _require_mcp_idle(self) -> None:
        try:
            self.session.execution.require_idle()
        except SessionExecutionConflict as exc:
            raise SessionBackendError(ProtocolErrorCode.CONFLICT, str(exc)) from exc

    def _session_mcp_state(self) -> SessionMCPState:
        public = self.resources.runtime_snapshot().mcp
        registry = self.session.agent_loop.mcp_registry
        transports = {
            server.name: server.transport
            for server in self.session.agent_loop.config.mcp_servers
        }
        sources = tuple(
            SessionMCPSourceState(
                name=source.name,
                transport=cast(Any, transports[source.name]),
                status=cast(Any, source.status.value),
                tools=tuple(
                    SessionMCPToolDescriptor(
                        remote_name=tool.name,
                        description=tool.description,
                        enabled=tool.enabled,
                        display_name=f"{source.name}_{tool.name}",
                    )
                    for tool in source.tools
                ),
                descriptor_revision=(
                    registry.descriptor_revision(source.name)
                    if registry is not None
                    else ""
                ),
                error=public.discovery_errors.get(source.name),
            )
            for source in public.sources
            if source.name in transports
        )
        return SessionMCPState(
            catalog_revision=self._mcp_catalog_revision,
            route_revision=f"legacy-mcp-routes:{self._mcp_route_revision}",
            sources=sources,
            discovery_errors=public.discovery_errors,
        )

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
            dispatched.response,
            SessionHistoryClearResponse
            | SessionRelocateResponse
            | SessionRewindResponse,
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
