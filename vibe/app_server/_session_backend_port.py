from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import JsonValue

from vibe.app_server._dispatch import DispatchResult
from vibe.app_server._model import ProtocolModel
from vibe.app_server.events import AppServerEvent
from vibe.app_server.models import (
    PublicCallbackEntry,
    PublicSessionState,
    SessionLogSummary,
)
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
    ProtocolErrorCode,
    RuntimeMutationResponse,
    RuntimeUpdatedParams,
    SessionCompactParams,
    SessionCompactResponse,
    SessionContinueParams,
    SessionDeleteParams,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryClearParams,
    SessionListParams,
    SessionListResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    SessionRewindParams,
    SessionRewindResponse,
    SessionSettingsUpdateParams,
    SessionStartParams,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    TurnEnqueueParams,
    TurnEnqueueResponse,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnQueueReadParams,
    TurnQueueReadResponse,
    TurnQueueRemoveParams,
    TurnQueueRemoveResponse,
    TurnQueueReplaceParams,
    TurnQueueReplaceResponse,
    TurnQueueResumeParams,
    TurnQueueResumeResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
)

type SessionBackendKind = Literal["python", "rust"]
type MCPAuthorizationReason = Literal["missing", "expired", "rejected", "invalid"]
type ConnectorAuthAction = Literal["none", "oauth", "credentials_setup", "unknown"]
type MCPCatalogOwner = Literal["config", "plugin"]


@dataclass(frozen=True, slots=True)
class MCPAuthorizationRef:
    server_name: str
    server_fingerprint: str
    kind: Literal["none", "static", "oauth"]
    descriptor_revision: str
    # Two catalogs share one name namespace, so the name alone is ambiguous.
    # Carried rather than inferred: resolving a plugin's reference against a
    # configured server would hand the plugin that server's credentials.
    owner: MCPCatalogOwner = "config"


@dataclass(frozen=True, slots=True)
class MCPAuthorizationSnapshot:
    headers: Mapping[str, str]
    connection_revision: str
    descriptor_revision: str
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MCPAuthorizationRequired:
    reason: MCPAuthorizationReason
    descriptor_revision: str
    observed_connection_revision: str | None = None


type MCPAuthorizationResult = MCPAuthorizationSnapshot | MCPAuthorizationRequired


class MCPAuthorizationProvider(Protocol):
    async def resolve(
        self, reference: MCPAuthorizationRef
    ) -> MCPAuthorizationResult: ...

    async def reject(
        self,
        reference: MCPAuthorizationRef,
        *,
        observed_connection_revision: str,
        reason: Literal["http_unauthorized", "mcp_unauthorized"],
    ) -> MCPAuthorizationResult: ...


@dataclass(frozen=True, slots=True)
class ResolvedMCPServerConfig:
    name: str
    transport: Literal["http", "streamable-http", "stdio"]
    url: str | None
    command: str | None
    args: tuple[str, ...]
    cwd: Path | None
    env: Mapping[str, str]
    authorization: MCPAuthorizationRef
    prompt: str | None
    startup_timeout_s: float
    tool_timeout_s: float
    sampling_enabled: bool
    disabled: bool
    disabled_tools: frozenset[str]


@dataclass(frozen=True, slots=True)
class ResolvedMCPCatalog:
    revision: str
    servers: tuple[ResolvedMCPServerConfig, ...]


@dataclass(frozen=True, slots=True)
class ResolvedConnectorTool:
    raw_name: str
    description: str | None
    input_schema: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ResolvedConnector:
    raw_id: str
    alias: str
    display_name: str
    ready: bool
    auth_action: ConnectorAuthAction
    tools: tuple[ResolvedConnectorTool, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedConnectorCatalog:
    provider_fingerprint: str
    revision: str
    connectors: tuple[ResolvedConnector, ...]


@dataclass(frozen=True, slots=True)
class ResolvedConnectorSetting:
    alias: str
    disabled: bool
    disabled_tools: frozenset[str]


@dataclass(frozen=True, slots=True)
class ResolvedConnectorSelection:
    selection_revision: str
    enable_connectors: bool
    implicit_source_enabled: bool
    connector_settings: tuple[ResolvedConnectorSetting, ...]
    enabled_tools: tuple[str, ...]
    disabled_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionConnectorToolDescriptor:
    raw_name: str
    description: str | None
    enabled: bool
    display_name: str


@dataclass(frozen=True, slots=True)
class SessionConnectorSourceState:
    raw_id: str
    alias: str
    display_name: str
    status: Literal["disabled", "connected", "needs_auth", "needs_setup", "unavailable"]
    tools: tuple[SessionConnectorToolDescriptor, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SessionConnectorState:
    accepted_catalog_revision: str
    accepted_selection_revision: str
    route_revision: str
    sources: tuple[SessionConnectorSourceState, ...]
    discovery_errors: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ConnectorAuthRequest:
    session_id: str
    raw_connector_id: str
    alias: str
    accepted_catalog_revision: str
    action: ConnectorAuthAction
    reason: Literal["needs_auth", "needs_setup", "gateway_rejected"]


@runtime_checkable
class SessionConnectorControl(Protocol):
    async def read_connectors(self) -> SessionConnectorState: ...

    async def reconfigure_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        force: bool,
    ) -> SessionConnectorState: ...

    async def suspend_connectors(
        self,
        *,
        name: str,
        tool_name: str | None,
        reason: Literal["disable", "replace", "gateway_rejected"],
    ) -> SessionConnectorState: ...

    async def request_connector_auth(self, *, alias: str) -> ConnectorAuthRequest: ...


@dataclass(frozen=True, slots=True)
class SessionMCPToolDescriptor:
    remote_name: str
    description: str
    enabled: bool
    display_name: str


@dataclass(frozen=True, slots=True)
class SessionMCPSourceState:
    name: str
    transport: Literal["http", "streamable-http", "stdio"]
    status: Literal["disabled", "enabled", "connected", "needs_auth", "unavailable"]
    tools: tuple[SessionMCPToolDescriptor, ...] = ()
    descriptor_revision: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SessionMCPState:
    catalog_revision: str
    route_revision: str
    sources: tuple[SessionMCPSourceState, ...]
    discovery_errors: Mapping[str, str]


@runtime_checkable
class SessionMCPControl(Protocol):
    async def read_mcp(self) -> SessionMCPState: ...

    async def reconfigure_mcp(
        self, configuration: ResolvedMCPCatalog, *, force_remote_discovery: bool
    ) -> SessionMCPState: ...

    async def authorization_changed(
        self, *, name: str, descriptor_revision: str
    ) -> SessionMCPState: ...

    async def suspend_mcp(
        self,
        *,
        name: str,
        tool_name: str | None,
        reason: Literal["logout", "remove", "disable", "replace"],
    ) -> SessionMCPState: ...


class SessionBackendError(Exception):
    def __init__(
        self,
        code: ProtocolErrorCode,
        message: str,
        data: JsonValue = None,
        *,
        after_response: Callable[[], None] | None = None,
        on_response_abandoned: Callable[[], None] | None = None,
    ) -> None:
        self.code = code
        self.data = data
        self.after_response = after_response
        self.on_response_abandoned = on_response_abandoned
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SessionBackendEvent:
    event: AppServerEvent
    method: str | None = None
    params: ProtocolModel | None = None
    session_id: str | None = None
    event_id: int | None = None


@dataclass(frozen=True, slots=True)
class SessionEventSubscription:
    snapshot: SessionReadResponse
    events: AsyncIterator[SessionBackendEvent]


@dataclass(frozen=True, slots=True)
class SessionForkResult:
    response: SessionForkResponse
    backend: SessionBackend | None
    after_response: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class SessionLifecycleResult:
    """Result of a session lifecycle operation (start, resume, continue).

    Carries the activated backend plus an optional deferred action to run
    after the RPC response is sent. The server calls ``after_response()``
    once the client has received the result, so long-running post-activation
    work (e.g. awaiting deferred init) does not block the response.
    """

    backend: SessionBackend
    after_response: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class SessionHistoryClearResult:
    backend: SessionBackend
    state: PublicSessionState
    session_log: SessionLogSummary
    after_response: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class SessionRewindForkResult:
    """A rewind that lands on a new session, leaving the source untouched."""

    backend: SessionBackend
    response: SessionRewindResponse
    after_response: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class SessionBackendResult[ResponseT: ProtocolModel]:
    response: ResponseT
    after_response: Callable[[], None] | None = None
    on_response_abandoned: Callable[[], None] | None = None
    runtime_updated: bool = False


class SessionBackend(Protocol):
    @property
    def session_id(self) -> str: ...

    async def read(self, params: SessionReadParams) -> SessionReadResponse: ...

    async def subscribe(
        self, params: SessionReadParams
    ) -> SessionEventSubscription: ...

    def guard_request(self) -> None: ...

    async def switch_agent(
        self, params: AgentSwitchParams
    ) -> SessionBackendResult[RuntimeMutationResponse]: ...

    async def update_settings(
        self, params: SessionSettingsUpdateParams
    ) -> SessionBackendResult[EmptyResponse]: ...

    async def write_config(
        self, params: ConfigWriteParams
    ) -> SessionBackendResult[ConfigWriteResponse]: ...

    async def reload_config(
        self, params: ConfigReloadParams
    ) -> SessionBackendResult[ConfigMutationResponse]: ...

    async def start_turn(
        self, params: TurnStartParams
    ) -> SessionBackendResult[TurnStartResponse]: ...

    async def enqueue_turn(
        self, params: TurnEnqueueParams
    ) -> SessionBackendResult[TurnEnqueueResponse]: ...

    async def read_turn_queue(
        self, params: TurnQueueReadParams
    ) -> SessionBackendResult[TurnQueueReadResponse]: ...

    async def remove_queued_turn(
        self, params: TurnQueueRemoveParams
    ) -> SessionBackendResult[TurnQueueRemoveResponse]: ...

    async def replace_queued_turn(
        self, params: TurnQueueReplaceParams
    ) -> SessionBackendResult[TurnQueueReplaceResponse]: ...

    async def resume_turn_queue(
        self, params: TurnQueueResumeParams
    ) -> SessionBackendResult[TurnQueueResumeResponse]: ...

    async def steer_turn(
        self, params: TurnSteerParams
    ) -> SessionBackendResult[TurnSteerResponse]: ...

    async def interrupt_turn(
        self, params: TurnInterruptParams
    ) -> SessionBackendResult[TurnInterruptResponse]: ...

    async def inject_context(
        self, params: ContextInjectParams
    ) -> SessionBackendResult[ContextInjectResponse]: ...

    async def respond_to_callback(
        self, params: CallbackResultParams
    ) -> SessionBackendResult[CallbackResultResponse]: ...

    async def compact(
        self, params: SessionCompactParams
    ) -> SessionBackendResult[SessionCompactResponse]: ...

    async def shutdown(self) -> None: ...


@runtime_checkable
class SessionBackendExtension(Protocol):
    """Optional Vibe Host operations outside the shared session surface."""

    async def dispatch_extension(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult: ...


@runtime_checkable
class SessionBackendNotificationSink(Protocol):
    """Optional bridge for adapters whose events originate in the Vibe Host."""

    async def publish_notification(
        self, method: str, params: ProtocolModel
    ) -> bool: ...


@runtime_checkable
class SessionBackendCallbackSink(Protocol):
    """Optional bridge for callbacks originating in the Vibe Host."""

    async def publish_callback(self, callback: PublicCallbackEntry) -> bool: ...


@runtime_checkable
class SessionBackendEventDrain(Protocol):
    """Optional ordering barrier for events bridged through the Vibe Host."""

    async def flush_events(self) -> None: ...


@runtime_checkable
class SessionBackendRuntimeView(Protocol):
    """Optional Vibe projection used for runtime-updated notifications."""

    def runtime_updated_params(self) -> RuntimeUpdatedParams: ...


@runtime_checkable
class SessionBackendOpenCallbacks(Protocol):
    """Optional access to open callbacks owned by a backend."""

    def open_callbacks(self) -> list[PublicCallbackEntry]: ...

    async def reject_callback_delivery(
        self, session_id: str, callback_id: str, error: CallbackResultError
    ) -> None: ...


@runtime_checkable
class SessionBackendChildSessionIndex(Protocol):
    """Optional child-session index used to reject deletion of live children."""

    def references_child(self, session_id: str) -> bool: ...


class SessionBackendHost(Protocol):
    @property
    def harness_kind(self) -> SessionBackendKind: ...

    async def start(self, params: SessionStartParams) -> SessionLifecycleResult: ...

    async def resume(self, params: SessionResumeParams) -> SessionLifecycleResult: ...

    async def continue_latest(
        self, params: SessionContinueParams
    ) -> SessionLifecycleResult: ...

    async def fork(self, params: SessionForkParams) -> SessionForkResult: ...

    async def list(self, params: SessionListParams) -> SessionListResponse: ...

    async def read(self, params: SessionReadParams) -> SessionReadResponse: ...

    async def rename(
        self, params: SessionTitleUpdateParams
    ) -> SessionTitleUpdateResponse: ...

    async def shutdown(self) -> None: ...


@runtime_checkable
class SessionBackendHostDelete(Protocol):
    """Optional selected-backend ownership of durable session deletion."""

    async def delete(self, params: SessionDeleteParams) -> EmptyResponse: ...


@runtime_checkable
class SessionBackendHistoryClearHost(Protocol):
    async def clear_history(
        self, source: SessionBackend, params: SessionHistoryClearParams
    ) -> SessionHistoryClearResult: ...


@runtime_checkable
class SessionBackendRewindForkHost(Protocol):
    """Optional Host ownership of the fork half of ``session/rewind``.

    A backend whose sessions are immutable stores cannot rewind into a new
    session from inside the session it is rewinding: the replacement has to be
    opened by whoever owns session identity. Backends that rewind in place
    answer ``session/rewind`` themselves and do not implement this.
    """

    async def rewind_fork(
        self, source: SessionBackend, params: SessionRewindParams
    ) -> SessionRewindForkResult: ...


@runtime_checkable
class SessionBackendHostBackgroundTasks(Protocol):
    """Optional background-task owner attached to a backend host."""

    async def stop_background_tasks(self, current: Any) -> list[BaseException]: ...
