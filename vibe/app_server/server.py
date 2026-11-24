from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
    nullcontext,
    suppress,
)
from dataclasses import dataclass, field
from enum import StrEnum, auto
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from vibe import __version__
from vibe.app_server._account import AccountGateway
from vibe.app_server._client_provided_tools import (
    ClientToolCall,
    ClientToolError,
    ClientToolResult,
    ClientToolRunner,
)
from vibe.app_server._config_changes import ConfigChanges
from vibe.app_server._connection_protocol import (
    AgentPlugin,
    ClientToolCallerParams,
    ClientToolCallParams,
    ClientToolCallResponse,
    ClientToolDefinitionParams,
)
from vibe.app_server._credential_selection import (
    MISTRAL_API_KEY_ENV,
    ConnectionCredentialSelection,
    select_platform_credentials,
)
from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._execution import cancel_tasks, raise_if_cancelled
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._imports import ImportsService
from vibe.app_server._left_turns import (
    AccountLender,
    LeftTurns,
    LenderOf,
    ListOnOwnChannel,
    OpenLeftTurnChannel,
    Owner,
    ResumeOne,
    SignsInItself,
    owner_key,
)
from vibe.app_server._left_turns_claim import LeftTurnsClaim
from vibe.app_server._local_projects import LocalProjectStore
from vibe.app_server._local_projects_controller import list_local_projects
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._open_sessions import OpenSessions
from vibe.app_server._parked_sessions import attach_parked, park_running_session
from vibe.app_server._platform_credential_lenders import (
    AccountPlatformCredentialProvider,
    LenderAvailability,
    PlatformCredentialLenders,
    account_key,
)
from vibe.app_server._platform_credentials import ConnectionPlatformCredentialProvider
from vibe.app_server._scratch_folders import scratch_folder_for
from vibe.app_server._session_backend_port import (
    SessionBackend,
    SessionBackendCallbackSink,
    SessionBackendChildSessionIndex,
    SessionBackendError,
    SessionBackendEvent,
    SessionBackendEventDrain,
    SessionBackendExtension,
    SessionBackendHeldEvents,
    SessionBackendHistoryClearHost,
    SessionBackendHost,
    SessionBackendHostArchive,
    SessionBackendHostArtifacts,
    SessionBackendHostBackgroundTasks,
    SessionBackendHostClientLoss,
    SessionBackendHostConfigRead,
    SessionBackendHostDelete,
    SessionBackendHostHistoryList,
    SessionBackendHostLeaseWatch,
    SessionBackendHostMove,
    SessionBackendHostPin,
    SessionBackendHostRelocate,
    SessionBackendHostSeenState,
    SessionBackendHostStorageRoot,
    SessionBackendNotificationSink,
    SessionBackendOpenCallbacks,
    SessionBackendQueuedTurnEditing,
    SessionBackendQueuedTurnSteering,
    SessionBackendResult,
    SessionBackendRewindForkHost,
    SessionBackendRunningTurnAnnouncement,
    SessionBackendRuntimeView,
    SessionBackendWorktreeTrust,
    SessionEventSubscription,
)
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server._session_lease_subscription import SessionLeaseSubscription
from vibe.app_server._session_link import SessionLink
from vibe.app_server._session_peers import SessionPeerError
from vibe.app_server._session_spawn import SessionSpawnError
from vibe.app_server._setup_oidc import SetupOidcHandler
from vibe.app_server._storage_usage import StorageUsageService, touch_stale_marker
from vibe.app_server._vibe_bot import (
    VIBE_BOT_ARCHIVE_LOCAL_METHOD,
    VIBE_BOT_CLIENT_ID_FILE,
    VIBE_BOT_METHODS,
    VIBE_BOT_REPORT_METHOD,
    VIBE_BOT_SEND_LOCAL_METHOD,
    VIBE_BOT_START_LOCAL_METHOD,
    VIBE_BOT_STOP_LOCAL_METHOD,
    VibeBotArchiveLocalParams,
    VibeBotCallRefused,
    VibeBotController,
    VibeBotEmpty,
    VibeBotLocalSessionGone,
    VibeBotLocalSessions,
    VibeBotReportParams,
    VibeBotReportResponse,
    VibeBotSendLocalParams,
    VibeBotSendLocalResponse,
    VibeBotStartLocalParams,
    VibeBotStartLocalResponse,
    VibeBotStopLocalParams,
    bot_sender_id,
)
from vibe.app_server._vibe_bot_tools import (
    VIBE_BOT_MCP_CACHE_DIR,
    HarnessLocalMCP,
    LocalMCP,
    LocalTools,
)
from vibe.app_server._vibe_home_access import vibe_home_access_denied
from vibe.app_server.connector_catalog import ConnectorCatalogService
from vibe.app_server.events import (
    CallbackRequested,
    ConnectorAuthorizationRequiredEvent,
    MCPAuthorizationRequiredEvent,
)
from vibe.app_server.models import OpenCallbackState, PublicCallbackEntry
from vibe.app_server.protocol import (
    PEER_SEND_NOT_DELIVERED_IN_TIME,
    SERVER_METHODS,
    SESSION_PEER_ARCHIVE_METHOD,
    SESSION_PEER_LIST_METHOD,
    SESSION_PEER_MARK_SEEN_METHOD,
    SESSION_PEER_READ_METHOD,
    SESSION_PEER_RENAME_METHOD,
    SESSION_PEER_RESTORE_METHOD,
    SESSION_PEER_SEND_METHOD,
    SESSION_SPAWN_METHOD,
    AgentInstallParams,
    AgentSwitchParams,
    ApprovalPolicySwitchParams,
    ArtifactsListParams,
    ArtifactsMarkViewedParams,
    ArtifactsSyncParams,
    CallbackCallParams,
    CallbackCallResponse,
    CallbackResultError,
    CallbackResultParams,
    ClientAccount,
    ClientCapabilities,
    ClientInfo,
    ConfigChangedParams,
    ConfigReadParams,
    ConfigReloadParams,
    ConfigSubscribeParams,
    ConfigWriteParams,
    ContextInjectParams,
    EmptyResponse,
    EventBatch,
    EventNotificationParams,
    EventsReadParams,
    ImportDiscoveryParams,
    ImportJobReadParams,
    ImportJobStartParams,
    ImportJobUpdatedParams,
    InitializeParams,
    InitializeResponse,
    InvalidParamsData,
    InvalidParamsIssue,
    JsonRpcErrorResponse,
    JsonRpcProtocolError,
    JsonRpcSuccessResponse,
    LeftTurnEntry,
    LeftTurnsInterruptParams,
    LeftTurnsInterruptResponse,
    LeftTurnsReadParams,
    LeftTurnsReadResponse,
    LeftTurnsStatus,
    MCPAuthUrlParams,
    ModelConfigWriteParams,
    Notification,
    PageRequest,
    PeerDelivery,
    PlatformCredentialsInvalidatedParams,
    PlatformCredentialsResolveParams,
    PlatformCredentialsResolveResponse,
    ProtocolError,
    ProtocolErrorCode,
    ServerInfo,
    ServerRequest,
    SessionArchiveParams,
    SessionCompactParams,
    SessionContinueParams,
    SessionContinueResponse,
    SessionDeleteParams,
    SessionForkParams,
    SessionHandoffParams,
    SessionHistoryClearParams,
    SessionHistoryClearResponse,
    SessionHistoryListParams,
    SessionListParams,
    SessionMarkAsSeenParams,
    SessionMarkAsUnseenParams,
    SessionMoveParams,
    SessionPeerArchiveParams,
    SessionPeerArchiveResponse,
    SessionPeerListParams,
    SessionPeerListResponse,
    SessionPeerMarkSeenParams,
    SessionPeerMarkSeenResponse,
    SessionPeerReadParams,
    SessionPeerReadResponse,
    SessionPeerRenameParams,
    SessionPeerRenameResponse,
    SessionPeerRestoreParams,
    SessionPeerRestoreResponse,
    SessionPeerSendParams,
    SessionPeerSendResponse,
    SessionPinParams,
    SessionReadParams,
    SessionReadResponse,
    SessionRelocateParams,
    SessionResumeParams,
    SessionResumeResponse,
    SessionRewindParams,
    SessionSettingsUpdateParams,
    SessionSnapshotParams,
    SessionSpawnParams,
    SessionSpawnResponse,
    SessionStartParams,
    SessionStartResponse,
    SessionTitleUpdateParams,
    StoppedLeftTurnEntry,
    StorageLeftoversCleanParams,
    StorageSessionKept,
    StorageSessionKeptReason,
    StorageSessionsDeleteParams,
    StorageSessionsDeleteResponse,
    StorageUsageReadParams,
    StorageUsageRefreshParams,
    StorageUsageUpdatedParams,
    TransportKind,
    TurnEnqueueParams,
    TurnInterruptParams,
    TurnQueueEditAbandonParams,
    TurnQueueEditCancelParams,
    TurnQueueEditSaveParams,
    TurnQueueEditStartParams,
    TurnQueueReadParams,
    TurnQueueRemoveParams,
    TurnQueueReplaceParams,
    TurnQueueResumeParams,
    TurnQueueSteerParams,
    TurnRetryParams,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    format_invalid_params_issues,
    validate_callback_acknowledgement,
    validate_json_rpc_envelope,
)
from vibe.app_server.transport import ClientLossAwareTransport, JsonRpcTransport
from vibe.core.paths import VIBE_HOME, WORKTREES_DIR
from vibe.core.platform_auth import PlatformCredentialProvider
from vibe.core.telemetry.send import TelemetryClient
from vibe.observability.logging import logger
from vibe.observability.sentry import (
    capture_sentry_exception,
    cli_crash_reporting_available,
    init_cli_sentry,
    report_unhandled_asyncio_errors,
)
from vibe.setup.auth._oidc_tenant import OidcTenantResolver

if TYPE_CHECKING:
    from vibe.app_server._session_backend_port import ResolvedMCPCatalog
    from vibe.app_server.mcp_catalog import MCPCatalogService
    from vibe.app_server.plugin_catalog import PluginCatalogService
    from vibe.core.config import VibeConfigSchema


class InitializationState(StrEnum):
    UNINITIALIZED = auto()
    INITIALIZE_RECEIVED = auto()
    INITIALIZED = auto()


type SessionBackendHostFactory = Callable[[SessionBackendServices], SessionBackendHost]


# Requests that can hand the connection a different session. Their answer is
# what tells the client the session changed, so notifications raised while they
# run are held back and flushed once it has been sent: an event naming the new
# session ahead of the response reaches a client still projecting the old one.
_SESSION_ATTACHMENT_CANDIDATES = frozenset({
    "session/fork",
    "session/start",
    "session/resume",
    "session/continue",
    "session/rewind",
    "session/history/clear",
})

# Requests that reopen a session another connection or process may have left
# mid-turn. The client never saw that turn's `turn/started`, so it is announced
# again, before that turn's live events are forwarded. A new session's first
# turn announces itself, and announcing it twice would reset what the client
# tracks for it.
_RUNNING_TURN_ANNOUNCEMENT_METHODS = frozenset({"session/resume", "session/continue"})

# How long open callbacks wait for that announcement to reach the client before
# they are sent anyway: a stuck event stream must not hide a question.
_RUNNING_TURN_ANNOUNCEMENT_TIMEOUT_SECONDS = 5.0

_SESSION_BACKEND_HOST_LIFECYCLE_METHODS = frozenset({
    "session/start",
    "session/resume",
    "session/continue",
})

_SESSION_OPTIONAL_METHODS = frozenset({
    "agents/list",
    "config/read",
    "skills/list",
    "workspace/trust/decision",
})

_SESSION_BACKEND_METHODS = frozenset({
    "agents/install",
    "agents/uninstall",
    "callback/result",
    "config/reload",
    "config/model/write",
    "config/write",
    "session/agent/update",
    "session/approvalPolicy/update",
    "session/compact",
    "session/context/inject",
    "session/settings/update",
    "session/turn/enqueue",
    "session/turn/queue/edit/abandon",
    "session/turn/queue/edit/cancel",
    "session/turn/queue/edit/save",
    "session/turn/queue/edit/start",
    "session/turn/queue/read",
    "session/turn/queue/remove",
    "session/turn/queue/replace",
    "session/turn/queue/steer",
    "session/turn/queue/resume",
    "turn/interrupt",
    "turn/retry",
    "turn/start",
    "turn/steer",
})


@dataclass(slots=True)
class CallbackDelivery:
    session_id: str
    callback_id: str
    answered: bool = False


@dataclass(slots=True)
class CallbackDeliveries:
    """The client's callback/call requests, and the callbacks sent since its last attach began."""

    requests: dict[int, CallbackDelivery] = field(default_factory=dict)
    # The re-send after an attach awaits its read, and live delivery runs meanwhile.
    sent_since_attach: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class PendingNotification:
    method: str
    params: ProtocolModel


@dataclass(frozen=True, slots=True)
class _ErrorDispatch:
    error: ProtocolError
    after_response: Callable[[], None] | None = None
    on_response_abandoned: Callable[[], None] | None = None


@dataclass(slots=True)
class ChannelRuntime:
    """What a channel runs its session on. It moves, as one, with a session
    another channel attaches to: the session reaches its client only through
    ``link``.
    """

    link: SessionLink
    session_backend_host: SessionBackendHost
    host_handler: HostRequestHandler
    mcp_catalog_service: MCPCatalogService | None
    connector_catalog_service: ConnectorCatalogService | None
    plugin_catalog_service: PluginCatalogService | None
    imports_service: ImportsService | None
    configure_agent_plugins: (
        Callable[
            [list[AgentPlugin], list[ClientToolDefinitionParams], ClientToolRunner],
            None,
        ]
        | None
    )


class AppServer:
    # Reset with ``_client_info``: the account the client reported at initialize.
    _client_account: ClientAccount | None = None
    # This connection's lending provider; kept after a close, so a parked
    # session can still ask about its account's lenders.
    _lending: AccountPlatformCredentialProvider | None = None
    _client_instance: str | None = None

    def __init__(  # noqa: PLR0913 - one collaborator per concern, all keyword-only
        self,
        transport: JsonRpcTransport,
        *,
        session_backend_host_factory: SessionBackendHostFactory,
        host_handler: HostRequestHandler,
        oidc_tenants: OidcTenantResolver,
        platform_credential_lenders: PlatformCredentialLenders,
        transport_kind: TransportKind = "in_process",
        account_gateway: AccountGateway | None = None,
        identity_gateway: IdentityGateway | None = None,
        mcp_catalog_service: MCPCatalogService | None = None,
        connector_catalog_service: ConnectorCatalogService | None = None,
        plugin_catalog_service: PluginCatalogService | None = None,
        imports_service: ImportsService | None = None,
        storage_usage: StorageUsageService | None = None,
        config_changes: ConfigChanges | None = None,
        vibe_bot_sessions: VibeBotLocalSessions | None = None,
        configure_agent_plugins: Callable[
            [list[AgentPlugin], list[ClientToolDefinitionParams], ClientToolRunner],
            None,
        ]
        | None = None,
        open_sessions: OpenSessions | None = None,
        left_turns: LeftTurns | None = None,
        open_left_turn_channel: OpenLeftTurnChannel | None = None,
        list_on_own_channel: ListOnOwnChannel | None = None,
        left_turn_session: str | None = None,
    ) -> None:
        self._root: SessionBackend | None = None
        self._setup_oidc = SetupOidcHandler(
            lambda client: self._host_handler.setup_telemetry(client)
        )
        self._transport: JsonRpcTransport | None = transport
        self._initialization = InitializationState.UNINITIALIZED
        self._client_info: ClientInfo | None = None
        self._client_capabilities = ClientCapabilities()
        self._next_request_id = 1
        self._event_watermarks: dict[str, int] = {}
        self._callback_deliveries = CallbackDeliveries()
        self._platform_credential_requests: dict[
            int, asyncio.Future[PlatformCredentialsResolveResponse]
        ] = {}
        self._abandoned_platform_credential_requests: set[int] = set()
        self._client_requests: dict[int, _ClientRequest] = {}
        # Given up on our side; the client may still answer.
        self._unanswered_client_requests: dict[int, str] = {}
        self._request_tasks: set[asyncio.Task[None]] = set()
        self._crash_reporting_task: asyncio.Task[None] | None = None
        self._crash_reporting_tasks: set[asyncio.Task[None]] = set()
        self._leases = SessionLeaseSubscription(self._send_notification)
        # The connection's artifacts/subscribe, until it ends.
        self._unsubscribe_artifacts: Callable[[], None] | None = None
        # The connection's config/subscribe, until it ends.
        self._config_changes = config_changes
        self._unsubscribe_config: Callable[[], None] | None = None
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
        self._platform_credentials: ConnectionPlatformCredentialProvider | None = None
        # This connection's providers, built at ``initialize`` once the client
        # is known, and kept for the connection's lifetime.
        self._credential_selection: ConnectionCredentialSelection | None = None
        self._storage_usage = storage_usage
        link = SessionLink(self)
        self._runtime = ChannelRuntime(
            link=link,
            session_backend_host=session_backend_host_factory(link),
            host_handler=host_handler,
            mcp_catalog_service=mcp_catalog_service,
            connector_catalog_service=connector_catalog_service,
            plugin_catalog_service=plugin_catalog_service,
            imports_service=imports_service,
            configure_agent_plugins=configure_agent_plugins,
        )
        if self._runtime.session_backend_host is None:
            raise TypeError(
                "session_backend_host_factory must return a SessionBackendHost"
            )
        self._vibe_bot = self._new_vibe_bot(vibe_bot_sessions)
        # This connection's storage/usage/updated, from its first storage call.
        self._stop_storage_updates: Callable[[], None] | None = None
        self._oidc_tenants = oidc_tenants
        self._platform_credential_lenders = platform_credential_lenders
        self._open_sessions = open_sessions
        # With ``left_turn_session``, this channel is the process's own resume
        # of that session: it waits for no one, and borrows a sign-in without
        # lending its own.
        self._left_turns, self._open_left_turn_channel, self._left_turn_session = (
            left_turns,
            open_left_turn_channel,
            left_turn_session,
        )
        self._list_on_own_channel = list_on_own_channel

    @property
    def session_link(self) -> SessionLink:
        return self._runtime.link

    @property
    def _link(self) -> SessionLink:
        return self._runtime.link

    @property
    def _session_backend_host(self) -> SessionBackendHost:
        return self._runtime.session_backend_host

    @property
    def _host_handler(self) -> HostRequestHandler:
        return self._runtime.host_handler

    @property
    def _mcp_catalog_service(self) -> MCPCatalogService | None:
        return self._runtime.mcp_catalog_service

    @property
    def _connector_catalog_service(self) -> ConnectorCatalogService | None:
        return self._runtime.connector_catalog_service

    @property
    def _plugin_catalog_service(self) -> PluginCatalogService | None:
        return self._runtime.plugin_catalog_service

    @property
    def _imports_service(self) -> ImportsService | None:
        return self._runtime.imports_service

    @property
    def _configure_agent_plugins(
        self,
    ) -> (
        Callable[
            [list[AgentPlugin], list[ClientToolDefinitionParams], ClientToolRunner],
            None,
        ]
        | None
    ):
        return self._runtime.configure_agent_plugins

    def _require_root(self) -> SessionBackend:
        if self._root is None:
            raise RuntimeError("No app-server session runtime is active")
        return self._root

    def _require_imports(self) -> ImportsService:
        if self._imports_service is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED, "Imports are not configured"
            )
        return self._imports_service

    def _crash_reporting_client(self) -> ClientInfo | None:
        # Only the terminal UI hands a DSN: Desktop, run servers and vibe-acp never do.
        return self._client_info if cli_crash_reporting_available() else None

    def _gate_crash_reporting(self, read_gate: Callable[[], Awaitable[bool]]) -> None:
        """Applies the crash-report gate off the request path, in the order asked."""
        client = self._crash_reporting_client()
        if client is None:
            return
        previous = self._crash_reporting_task

        async def gate() -> None:
            if previous is not None:
                await asyncio.wait({previous})
            try:
                enabled = await read_gate()
                await asyncio.to_thread(_init_crash_reporting, client, enabled=enabled)
                if enabled:
                    report_unhandled_asyncio_errors(asyncio.get_running_loop())
            except Exception:
                logger.warning("Could not configure crash reporting", exc_info=True)

        task = asyncio.create_task(gate())
        self._crash_reporting_task = task
        # Every gate, not only the last: shutdown must stop an earlier opt-in too.
        self._crash_reporting_tasks.add(task)
        task.add_done_callback(self._crash_reporting_tasks.discard)

    def _note_self_hosted_deployment(self) -> None:
        """Every client, with or without crash reporting, gets the log line."""

        async def note() -> None:
            try:
                await self._host_handler.log_self_hosted_services()
            except Exception:
                logger.warning("Could not read the deployment's config", exc_info=True)

        task = asyncio.create_task(note())
        # Cancelled with the crash-reporting gates at shutdown.
        self._crash_reporting_tasks.add(task)
        task.add_done_callback(self._crash_reporting_tasks.discard)

    def _configure_crash_reporting(self, backend: SessionBackend) -> None:
        """The attached session's config gates reporting, as the TUI's at Ready."""
        if not isinstance(backend, SessionBackendRuntimeView):
            return
        try:
            enabled = (
                backend.runtime_updated_params().runtime.config.crash_reports_enabled
            )
        except Exception:
            logger.warning("Could not read the crash-report gate", exc_info=True)
            return
        self._gate_crash_reporting(lambda: _resolved(enabled))

    def _report_dispatch_error(
        self, method: str, exc: Exception, *, boundary: str = "app_server_dispatch"
    ) -> None:
        # vibe-acp reports its own failures, including the ones it gets from here.
        if self._crash_reporting_client() is None:
            return
        tags = {"vibe_boundary": boundary, "app_server_method": method}
        fingerprint = None
        if isinstance(exc, SessionBackendError) and (code := _harness_code(exc)):
            tags["harness_code"] = code
            # One harness code is one bug, whatever session or path its message names.
            fingerprint = ["app-server-harness-error", method, code]
        capture_sentry_exception(exc, fatal=False, tags=tags, fingerprint=fingerprint)

    async def _activate_backend(
        self,
        backend: SessionBackend,
        history_limit: int,
        *,
        announce_running_turn: bool = False,
    ) -> SessionReadResponse:
        params = SessionReadParams(
            session_id=backend.session_id, history=PageRequest(limit=history_limit)
        )
        if backend is self._backend_event_backend:
            return await backend.read(params)
        subscription = await backend.subscribe(params)
        previous = self._root
        self._root = backend
        self._configure_crash_reporting(backend)
        # The client treats a turn as running only after `turn/started`. Queue
        # that announcement before the forwarder, so recovered history updates
        # and token deltas cannot reach the client first and be discarded.
        if announce_running_turn:
            await self._announce_running_turn()
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
        if (
            envelope.child_session
            and not self._client_capabilities.child_session_events
        ):
            return
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
        if (
            envelope.method == "turn/completed"
            and envelope.session_id is not None
            and not envelope.child_session
        ):
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
            self._client_account = None
            self._client_instance = None
            self._client_capabilities = ClientCapabilities()
            self._platform_credentials = None
            self._credential_selection = None
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
                # The stream ended without a session/stop: the client is gone.
                if (
                    close_on_disconnect
                    and not self._closed
                    and (
                        not isinstance(transport, ClientLossAwareTransport)
                        or transport.client_lost
                    )
                    and isinstance(
                        self._session_backend_host, SessionBackendHostClientLoss
                    )
                ):
                    self._session_backend_host.mark_client_disconnected()
            except asyncio.CancelledError:
                if self._request_error is not None:
                    raise self._request_error
                # close() (e.g. on session/stop) cancels serving: a clean end.
                current = asyncio.current_task()
                if self._closed and current is not None and current.uncancel() == 0:
                    return
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
            # Before the session shutdown, which can take a while: no other
            # channel's session may borrow from a client that is gone.
            self._stop_lending("The app server closed")
            # Before parking: the session's event forwarder must be gone before
            # another channel attaches and reads the same event stream.
            errors = await self._stop_background_tasks(current)
            try:
                await self._setup_oidc.close()
            except BaseException as exc:
                errors.append(exc)
            if not self._park_running_session():
                try:
                    await self._close_root()
                except BaseException as exc:
                    errors.append(exc)
            self._callback_deliveries.requests.clear()
            self._abandon_client_requests("The app server closed")
            self._abandon_platform_credential_requests("The app server closed")
            self._pending_notifications.clear()
            self._connection_attached = False
            self._attaching = False
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

    def _park_running_session(self) -> bool:
        if self._open_sessions is None:
            return False
        return park_running_session(self, self._open_sessions)

    def _opening(self, session_id: str) -> AbstractAsyncContextManager[None]:
        if self._open_sessions is None or self._left_turn_session == session_id:
            return nullcontext()
        return self._open_sessions.opening(session_id)

    async def _attach_parked(
        self, params: SessionResumeParams
    ) -> SessionReadResponse | None:
        if self._open_sessions is None:
            return None
        return await attach_parked(self, self._open_sessions, params)

    def _subscribe_artifacts(self) -> None:
        self._stop_artifact_subscription()
        host = self._session_backend_host
        if isinstance(host, SessionBackendHostArtifacts):
            self._unsubscribe_artifacts = host.subscribe_artifacts(
                self._send_notification_soon
            )

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
        # A reconnectable connection also drops its selection below: only a
        # closed channel (``close``) keeps one its parked sessions borrow with.
        self._stop_lending("The client disconnected")
        errors = await self._stop_request_tasks(asyncio.current_task())
        try:
            await self._setup_oidc.close()
        except BaseException as exc:
            errors.append(exc)
        if isinstance(self._root, SessionBackendQueuedTurnEditing):
            try:
                await self._root.abandon_active_queued_turn_edit()
            except BaseException as exc:
                errors.append(exc)
        self._callback_deliveries.requests.clear()
        self._abandon_client_requests("The client disconnected")
        self._abandon_platform_credential_requests("The client disconnected")
        self._pending_notifications.clear()
        self._connection_attached = False
        self._attaching = False
        self._transport = None
        self._serve_task = None
        self._initialization = InitializationState.UNINITIALIZED
        self._client_info = None
        self._client_account = None
        self._client_instance = None
        self._client_capabilities = ClientCapabilities()
        self._platform_credentials = None
        self._credential_selection = None
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
        tasks.extend(self._leases.take())
        tasks.extend(self._vibe_bot.close())
        self._stop_artifact_subscription()
        self._stop_storage_updates_now()
        self._stop_config_subscription()
        backend_events = self._backend_event_task
        self._backend_event_task = None
        self._backend_event_backend = None
        if backend_events is not None and backend_events is not current:
            tasks.append(backend_events)
        tasks.extend(self._crash_reporting_tasks)
        errors = await cancel_tasks(tasks, label="app-server background")
        try:
            await self._vibe_bot.aclose_tools()
        except Exception as exc:
            errors.append(exc)
        if isinstance(self._session_backend_host, SessionBackendHostBackgroundTasks):
            errors.extend(
                await self._session_backend_host.stop_background_tasks(current)
            )
        return errors

    async def _stop_request_tasks(
        self, current: asyncio.Task[object] | None
    ) -> list[BaseException]:
        tasks = [task for task in self._request_tasks if task is not current]
        tasks.extend(self._leases.take())
        tasks.extend(self._vibe_bot.close())
        self._stop_artifact_subscription()
        self._stop_storage_updates_now()
        self._stop_config_subscription()
        backend_events = self._backend_event_task
        self._backend_event_task = None
        self._backend_event_backend = None
        if backend_events is not None and backend_events is not current:
            tasks.append(backend_events)
        errors = await cancel_tasks(tasks, label="app-server request")
        try:
            await self._vibe_bot.drop_tools()
        except Exception as exc:
            errors.append(exc)
        return errors

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
        if isinstance(outcome, _ErrorDispatch):
            try:
                await self._send_error(request.id, outcome.error)
            except BaseException:
                if outcome.on_response_abandoned is not None:
                    outcome.on_response_abandoned()
                if attachment_candidate:
                    await self._finish_attachment(was_attached)
                raise
            if attachment_candidate:
                await self._finish_attachment(was_attached)
            if outcome.after_response is not None:
                outcome.after_response()
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
            if outcome.on_response_abandoned is not None:
                outcome.on_response_abandoned()
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
        self._callback_deliveries.sent_since_attach.clear()

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
    ) -> DispatchResult | _ErrorDispatch:
        try:
            if request.method == "initialize":
                return DispatchResult(self._initialize(request.params))
            if self._initialization is not InitializationState.INITIALIZED:
                raise RequestFailure(
                    ProtocolErrorCode.NOT_INITIALIZED,
                    "initialize must be the first request",
                )
            try:
                dispatched = await self._dispatch_request(
                    request.method, request.params
                )
            except Exception as exc:
                if (denied := vibe_home_access_denied(exc)) is None:
                    raise
                raise denied from exc
            if dispatched.session_attached and not dispatched.callbacks_checked:
                await self._validate_open_callback_capabilities(request.method)
            return dispatched
        except (RequestFailure, SessionBackendError) as exc:
            if isinstance(exc, SessionBackendError) and _is_unexpected(exc):
                self._report_dispatch_error(request.method, exc)
            return _ErrorDispatch(
                ProtocolError(code=exc.code, message=str(exc), data=exc.data),
                after_response=(
                    exc.after_response if isinstance(exc, SessionBackendError) else None
                ),
                on_response_abandoned=(
                    exc.on_response_abandoned
                    if isinstance(exc, SessionBackendError)
                    else None
                ),
            )
        except ValidationError as exc:
            data = InvalidParamsData(
                error_count=exc.error_count(),
                issues=[
                    InvalidParamsIssue(path=list(issue["loc"]), message=issue["msg"])
                    for issue in exc.errors()
                ],
            ).model_dump(mode="json", by_alias=True)
            logger.warning(
                "Rejected %s: invalid request parameters: %s",
                request.method,
                format_invalid_params_issues(data) or "<no field detail>",
            )
            return _ErrorDispatch(
                ProtocolError(
                    code=ProtocolErrorCode.INVALID_PARAMS,
                    message="Invalid request parameters",
                    data=data,
                )
            )
        except Exception as exc:
            logger.exception("Unhandled error dispatching %s", request.method)
            self._report_dispatch_error(request.method, exc)
            return _ErrorDispatch(
                ProtocolError(
                    code=ProtocolErrorCode.INTERNAL_ERROR,
                    message=str(exc),
                    # The message names paths and values; the type lets a client
                    # group the failures by cause.
                    data={"exceptionType": type(exc).__name__},
                )
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
            if request.method in _RUNNING_TURN_ANNOUNCEMENT_METHODS:
                await self._wait_for_running_turn_announcement()
            await self._redeliver_open_callbacks(request.method)
        if dispatched.runtime_updated and self._root is not None:
            if not isinstance(self._root, SessionBackendRuntimeView):
                raise RuntimeError(
                    "The selected backend cannot project its runtime state"
                )
            await self._notify("runtime/updated", self._root.runtime_updated_params())
        if request.method == "session/stop":
            await self.close()

    async def _dispatch_request(  # noqa: PLR0911, PLR0912 - explicit route ownership
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method not in SERVER_METHODS:
            raise method_not_found(method)
        if method.startswith("setup/oidc-"):
            return DispatchResult(
                await self._setup_oidc.dispatch(
                    method, raw_params, client_info=self._initialized_client_info()
                )
            )
        if method == "platformCredentials/headers":
            from vibe.app_server._platform_credential_headers import (
                platform_credential_headers,
            )

            return DispatchResult(
                await platform_credential_headers(
                    raw_params,
                    client=self._client_info,
                    desktop_credentials=self._platform_credentials is not None,
                    credentials=self.platform_credentials,
                )
            )
        if method == "events/read":
            return self._events_read(raw_params)
        if method in {"session/leftTurns/read", "session/leftTurns/interrupt"}:
            return await self._dispatch_left_turns(method, raw_params)
        if method == "imports/discover":
            validate_wire(ImportDiscoveryParams, raw_params)
            return await self._require_imports().discover()
        if method == "imports/start":
            params = validate_wire(ImportJobStartParams, raw_params)
            return await self._require_imports().start(
                params.harnesses, notify=self._route_notification
            )
        if method == "imports/read":
            params = validate_wire(ImportJobReadParams, raw_params)
            return await self._require_imports().read(
                params.after_event_id, params.wait_ms
            )
        if method in VIBE_BOT_METHODS:
            return DispatchResult(await self._vibe_bot.dispatch(method, raw_params))
        if method.startswith((
            "storage/usage/",
            "storage/leftovers/",
            "storage/sessions/",
        )):
            return await self._dispatch_storage_usage(method, raw_params)
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
            "config/telemetry/read",
            "config/telemetry/update",
            "plugins/local/list",
            "plugins/local/setEnabled",
            "projects/create",
            "projects/link",
            "projects/list",
            "projects/remove",
            "projects/rename",
            "projects/retireCloud",
            "projects/setCloud",
            "projects/setRoots",
            "session/history/get",
            "setup/status",
            "setup/store-credential",
            "setup/submit-choices",
            "skills/local/delete",
            "skills/local/list",
            "skills/local/read",
            "skills/local/setEnabled",
            "skills/local/write",
            "workspace/git/checkouts",
            "workspace/git/worktrees/auto/update",
            "workspace/git/worktrees/limit/update",
            "workspace/git/worktrees/list",
            "workspace/git/worktrees/prune",
            "workspace/git/worktrees/remove",
            "workspace/trust/folders/add",
            "workspace/trust/folders/inspect",
            "workspace/trust/folders/list",
            "workspace/trust/folders/update",
            "workspace/trust/status",
            "workspace/trust/untrustedConfig",
        } or method.startswith("projectLinks/"):
            # The attached session is the caller; its own worktree holder does
            # not count as in use for the worktree-remove questions it asks.
            # The setup methods are session-less by construction: the wizard
            # runs before any session, so they never build or read a runtime.
            return await self._host_handler.dispatch(
                method,
                raw_params,
                session_id=self._root.session_id if self._root is not None else None,
                worktree_trust_store=(
                    self._root.worktree_trust_store
                    if method == "workspace/git/worktrees/remove"
                    and isinstance(self._root, SessionBackendWorktreeTrust)
                    else None
                ),
                client_info=self._initialized_client_info(),
            )
        if method == "session/delete":
            return await self._delete_session(raw_params)
        if method in {
            "session/lease/subscribe",
            "artifacts/subscribe",
            "config/subscribe",
        }:
            return await self._subscribe(method, raw_params)
        if _omits_session_id(method, raw_params):
            # The attached session, if any, tells the config reads the
            # connection's own session is already fetching what they would.
            return await self._host_handler.dispatch(
                method,
                raw_params,
                session_id=self._root.session_id if self._root is not None else None,
                client_info=self._client_info,
            )
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
        raise_if_cancelled(errors)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("App-server session shutdown failed", errors)

    async def _delete_session(self, raw_params: dict[str, Any]) -> DispatchResult:
        result = await self._delete_session_now(raw_params)
        await asyncio.to_thread(touch_stale_marker)
        return result

    async def _delete_session_now(self, raw_params: dict[str, Any]) -> DispatchResult:
        params = validate_wire(SessionDeleteParams, raw_params)
        root = self._root
        is_root = root is not None and params.session_id == root.session_id
        references_child = isinstance(
            root, SessionBackendChildSessionIndex
        ) and root.references_child(params.session_id)
        if isinstance(self._session_backend_host, SessionBackendHostDelete):
            response = await self._session_backend_host.delete(params)
            if is_root:
                backend_events = self._backend_event_task
                self._backend_event_task = None
                self._backend_event_backend = None
                if backend_events is not None:
                    await cancel_tasks([backend_events], label="deleted session events")
                self._root = None
                self._callback_deliveries.requests.clear()
                if self._connector_catalog_service is not None:
                    self._connector_catalog_service.discard_session(params.session_id)
            return DispatchResult(response=response)
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
        if method == "session/history/list":
            return await self._dispatch_stored_session(method, raw_params)
        if self._host_handler.handles(method):
            return await self._host_handler.dispatch(
                method, raw_params, client_info=self._client_info
            )
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
        previous_session_id = root.session_id
        try:
            result = await root.dispatch_extension(method, raw_params)
        except SessionBackendError as exc:
            if exc.code is ProtocolErrorCode.NOT_FOUND and method in {
                "session/read",
                "session/history/list",
            }:
                return await self._dispatch_stored_session(method, raw_params)
            raise
        if result.session_attached and root.session_id != previous_session_id:
            subscription = await root.subscribe(
                SessionReadParams(
                    session_id=root.session_id, history=PageRequest(limit=200)
                )
            )
            await self._replace_backend_event_task(root, subscription)
            await self._replay_pending_backend_events(
                root, subscription.snapshot.last_event_id
            )
        await self._flush_backend_events(root)
        return result

    async def _dispatch_stored_session(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        host = self._session_backend_host
        if method == "session/history/list" and isinstance(
            host, SessionBackendHostHistoryList
        ):
            try:
                return DispatchResult(
                    await host.list_history(
                        validate_wire(SessionHistoryListParams, raw_params)
                    )
                )
            except SessionBackendError as exc:
                if exc.code is not ProtocolErrorCode.NOT_FOUND:
                    raise
        return await self._host_handler.dispatch(method, raw_params)

    async def _dispatch_backend_host_operation(  # noqa: PLR0911 - explicit route ownership
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method in {"session/list", "session/read", "config/read"}:
            return await self._dispatch_backend_host_read(method, raw_params)
        # The artifacts namespace is session-scoped but answers without a
        # runtime: a stored session reads, marks viewed and syncs its persisted
        # metadata, so a connection with no session attached — Desktop's
        # catalogue channel — can serve it.
        host = self._session_backend_host
        if method == "artifacts/list" and isinstance(host, SessionBackendHostArtifacts):
            return DispatchResult(
                await host.list_artifacts(
                    validate_wire(ArtifactsListParams, raw_params)
                )
            )
        if method == "artifacts/markViewed" and isinstance(
            host, SessionBackendHostArtifacts
        ):
            return DispatchResult(
                await host.mark_artifacts_viewed(
                    validate_wire(ArtifactsMarkViewedParams, raw_params)
                )
            )
        if method == "artifacts/sync" and isinstance(host, SessionBackendHostArtifacts):
            return DispatchResult(
                await host.sync_artifacts(
                    validate_wire(ArtifactsSyncParams, raw_params)
                )
            )
        if method in {"session/rename", "session/relocate", "session/move"}:
            result = await self._dispatch_backend_host_session_update(
                method, raw_params
            )
            if result is not None:
                return result
        if method in {
            "session/pin",
            "session/archive",
            "session/markAsSeen",
            "session/markAsUnseen",
        }:
            return await self._dispatch_backend_host_metadata(method, raw_params)
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
            return DispatchResult(
                response,
                after_response=result.after_response,
                session_attached=result.backend is not None,
            )
        if handoff := await self._dispatch_backend_host_handoff(method, raw_params):
            return handoff
        return await self._dispatch_backend_host_lifecycle(method, raw_params)

    async def _dispatch_backend_host_session_update(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "session/rename":
            params = validate_wire(SessionTitleUpdateParams, raw_params)
            response = await self._session_backend_host.rename(params)
            if self._root is not None and self._root.session_id == params.session_id:
                await self._flush_backend_events(self._root)
            return DispatchResult(response)
        if method == "session/move":
            if not isinstance(self._session_backend_host, SessionBackendHostMove):
                return None
            params = validate_wire(SessionMoveParams, raw_params)
            attached = self._attached_root(params.session_id)
            response = await self._session_backend_host.move(params, attached)
        else:
            if not isinstance(self._session_backend_host, SessionBackendHostRelocate):
                return None
            params = validate_wire(SessionRelocateParams, raw_params)
            attached = self._attached_root(params.session_id)
            response = await self._session_backend_host.relocate(params, attached)
        if attached is not None:
            await self._flush_backend_events(attached)
        return DispatchResult(response)

    def _attached_root(self, session_id: str) -> SessionBackend | None:
        if self._root is not None and self._root.session_id == session_id:
            return self._root
        return None

    async def _read_stored_session_config(
        self, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        """A stored session's configuration, answered without resuming it.

        The attached session answers for itself: it is the one holding the
        configuration the next turn will run. Only a session this connection is
        not sitting on reaches the store.
        """
        params = validate_wire(ConfigReadParams, raw_params)
        if params.session_id is None:
            return None
        if self._root is not None and self._root.session_id == params.session_id:
            return None
        if not isinstance(self._session_backend_host, SessionBackendHostConfigRead):
            return None
        return DispatchResult(await self._session_backend_host.read_config(params))

    async def _dispatch_backend_host_metadata(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method == "session/pin":
            if not isinstance(self._session_backend_host, SessionBackendHostPin):
                raise method_not_found(method)
            params = validate_wire(SessionPinParams, raw_params)
            response: ProtocolModel = await self._session_backend_host.pin(params)
        elif method == "session/archive":
            if not isinstance(self._session_backend_host, SessionBackendHostArchive):
                raise method_not_found(method)
            params = validate_wire(SessionArchiveParams, raw_params)
            response = await self._session_backend_host.archive(params)
            await asyncio.to_thread(touch_stale_marker)
        elif method == "session/markAsSeen":
            if not isinstance(self._session_backend_host, SessionBackendHostSeenState):
                raise method_not_found(method)
            params = validate_wire(SessionMarkAsSeenParams, raw_params)
            response = await self._session_backend_host.mark_as_seen(params)
        else:
            if not isinstance(self._session_backend_host, SessionBackendHostSeenState):
                raise method_not_found(method)
            params = validate_wire(SessionMarkAsUnseenParams, raw_params)
            response = await self._session_backend_host.mark_as_unseen(params)
        if self._root is not None and self._root.session_id == params.session_id:
            await self._flush_backend_events(self._root)
        return DispatchResult(response)

    async def _dispatch_backend_host_read(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "config/read":
            return await self._read_stored_session_config(raw_params)
        if method == "session/list":
            response = await self._session_backend_host.list(
                validate_wire(SessionListParams, raw_params)
            )
            return DispatchResult(response)
        response = await self._session_backend_host.read(
            validate_wire(SessionReadParams, raw_params)
        )
        return DispatchResult(response)

    async def _dispatch_backend_host_handoff(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        """Route the Host operations that hand the connection a new session.

        Both replace the attached backend rather than mutate it, so both run
        under a lifecycle transition and answer with the session re-attached.
        """
        if rewound := await self._dispatch_rewind_fork(method, raw_params):
            return rewound
        return await self._dispatch_history_clear(method, raw_params)

    async def _dispatch_history_clear(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method != "session/history/clear":
            return None
        if not isinstance(self._session_backend_host, SessionBackendHistoryClearHost):
            return None
        root = self._require_root()
        params = validate_wire(SessionHistoryClearParams, raw_params)
        async with self._lifecycle_transition():
            result = await self._session_backend_host.clear_history(root, params)
            try:
                await self._activate_backend(result.backend, 200)
            except BaseException:
                if result.on_failure is not None:
                    result.on_failure()
                raise
        return DispatchResult(
            SessionHistoryClearResponse(
                state=result.state, session_log=result.session_log
            ),
            after_response=result.after_response,
            session_attached=True,
            runtime_updated=True,
        )

    async def _dispatch_rewind_fork(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        """Route the fork half of ``session/rewind`` to the Host, if it owns it.

        Only the fork half: an in-place rewind never changes session identity,
        so it stays on the attached backend. A Host without this capability —
        or a request arriving before a session is attached — falls through to
        the backend's own ``session/rewind``.
        """
        if method != "session/rewind" or self._root is None:
            return None
        if not isinstance(self._session_backend_host, SessionBackendRewindForkHost):
            return None
        params = validate_wire(SessionRewindParams, raw_params)
        if params.inplace:
            return None
        async with self._lifecycle_transition():
            root = self._require_root()
            result = await self._session_backend_host.rewind_fork(root, params)
            await self._activate_backend(result.backend, 200)
        return DispatchResult(
            result.response,
            after_response=result.after_response,
            session_attached=True,
            runtime_updated=True,
        )

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
            async with scratch_folder_for(
                validate_wire(SessionStartParams, raw_params)
            ) as params:
                lifecycle = await self._session_backend_host.start(params)
                try:
                    first_turn = await self._apply_start_choices(
                        lifecycle.backend, params
                    )
                except BaseException:
                    await self._discard_started(lifecycle.backend)
                    raise
            self._record_session_created(lifecycle.backend)
            response = await self._activate_lifecycle_backend(
                method, lifecycle.backend, params.history_limit
            )
            return DispatchResult(
                SessionStartResponse(
                    state=response.state, last_event_id=response.last_event_id
                ),
                after_response=_in_order(
                    lifecycle.after_response,
                    first_turn.after_response if first_turn else None,
                ),
                on_response_abandoned=(
                    first_turn.on_response_abandoned if first_turn else None
                ),
                session_attached=True,
            )
        if method == "session/resume":
            params = validate_wire(SessionResumeParams, raw_params)
            after_response = None
            response = await self._attach_parked(params)
            attached_parked = response is not None
            if response is None:
                async with self._opening(params.session_id):
                    lifecycle = await self._session_backend_host.resume(params)
                    after_response = lifecycle.after_response
                    response = await self._activate_lifecycle_backend(
                        method, lifecycle.backend, params.history_limit
                    )
            return DispatchResult(
                SessionResumeResponse(
                    state=response.state, last_event_id=response.last_event_id
                ),
                after_response=after_response,
                session_attached=True,
                runtime_updated=True,
                callbacks_checked=attached_parked,
            )
        if method == "session/continue":
            params = validate_wire(SessionContinueParams, raw_params)
            lifecycle = await self._session_backend_host.continue_latest(params)
            response = await self._activate_lifecycle_backend(
                method, lifecycle.backend, params.history_limit
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

    async def _discard_started(self, backend: SessionBackend) -> None:
        """A first turn that failed may already have saved the session, and the
        client never gets its id to delete it.
        """
        host = self._session_backend_host
        if not isinstance(host, SessionBackendHostDelete):
            await backend.shutdown()
            return
        await host.delete(SessionDeleteParams(session_id=backend.session_id))

    async def _apply_start_choices(
        self, backend: SessionBackend, params: SessionStartParams
    ) -> SessionBackendResult[TurnStartResponse] | None:
        """Everything but the first turn applies while the session is unsaved, so
        a rejected choice leaves no session behind. The model goes last: unless
        `session_only`, it also writes the user's default, which a choice
        rejected after it would leave changed. The host already applied and
        pinned `agent_config.agent`.
        """
        session_id = backend.session_id
        if params.title is not None:
            await self._session_backend_host.rename(
                SessionTitleUpdateParams(session_id=session_id, title=params.title)
            )
        if params.model is not None or params.reasoning_effort is not None:
            written = await backend.write_model_config(
                ModelConfigWriteParams(
                    session_id=session_id,
                    model_alias=params.model,
                    reasoning_effort=params.reasoning_effort,
                    session_only=params.session_only,
                )
            )
            if written.response.rejected or written.response.failures:
                raise RequestFailure(
                    ProtocolErrorCode.INVALID_PARAMS,
                    "; ".join(written.response.failures)
                    or "The configuration rejected the model choice",
                )
        if params.first_turn is None:
            return None
        return await backend.start_turn(
            TurnStartParams(
                session_id=session_id,
                message=params.first_turn.message,
                client_user_message_id=params.first_turn.client_user_message_id,
            )
        )

    def _record_session_created(self, backend: SessionBackend) -> None:
        logger.debug(
            "Session created: harness=%s session_id=%s",
            self._session_backend_host.harness_kind,
            backend.session_id,
        )

    async def _dispatch_backend_operation(  # noqa: PLR0911 - explicit route ownership
        self, root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method.startswith("session/turn/"):
            return await self._dispatch_backend_turn(root, method, raw_params)
        match method.partition("/")[0]:
            case "agents":
                return await self._dispatch_backend_agents(root, method, raw_params)
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
                runtime_updated=result.response.applied,
            )
        if method == "session/approvalPolicy/update":
            result = await root.switch_approval_policy(
                validate_wire(ApprovalPolicySwitchParams, raw_params)
            )
            return DispatchResult(
                result.response,
                after_response=result.after_response,
                runtime_updated=result.response.applied,
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
            return DispatchResult(
                result.response,
                after_response=result.after_response,
                on_response_abandoned=result.on_response_abandoned,
            )
        return None

    @staticmethod
    async def _dispatch_backend_agents(
        root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "agents/install":
            result = await root.install_agent(
                validate_wire(AgentInstallParams, raw_params)
            )
        elif method == "agents/uninstall":
            result = await root.uninstall_agent(
                validate_wire(AgentInstallParams, raw_params)
            )
        else:
            return None
        return DispatchResult(
            result.response, after_response=result.after_response, runtime_updated=True
        )

    @staticmethod
    async def _dispatch_backend_turn(
        root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "session/turn/enqueue":
            result = await root.enqueue_turn(
                validate_wire(TurnEnqueueParams, raw_params)
            )
        elif method == "session/turn/queue/read":
            result = await root.read_turn_queue(
                validate_wire(TurnQueueReadParams, raw_params)
            )
        elif method == "session/turn/queue/remove":
            result = await root.remove_queued_turn(
                validate_wire(TurnQueueRemoveParams, raw_params)
            )
        elif method == "session/turn/queue/replace":
            result = await root.replace_queued_turn(
                validate_wire(TurnQueueReplaceParams, raw_params)
            )
        elif method in {
            "session/turn/queue/edit/start",
            "session/turn/queue/edit/save",
            "session/turn/queue/edit/cancel",
            "session/turn/queue/edit/abandon",
        }:
            result = await AppServer._dispatch_backend_queued_edit(
                root, method, raw_params
            )
        elif method == "session/turn/queue/steer":
            if not isinstance(root, SessionBackendQueuedTurnSteering):
                raise RequestFailure(
                    ProtocolErrorCode.NOT_IMPLEMENTED,
                    "The selected session backend does not support queued steering",
                )
            result = await root.steer_queued_turn(
                validate_wire(TurnQueueSteerParams, raw_params)
            )
        elif method == "session/turn/queue/resume":
            result = await root.resume_turn_queue(
                validate_wire(TurnQueueResumeParams, raw_params)
            )
        elif method == "turn/start":
            result = await root.start_turn(validate_wire(TurnStartParams, raw_params))
        elif method == "turn/steer":
            result = await root.steer_turn(validate_wire(TurnSteerParams, raw_params))
        elif method == "turn/retry":
            result = await root.retry_turn(validate_wire(TurnRetryParams, raw_params))
        elif method == "turn/interrupt":
            result = await root.interrupt_turn(
                validate_wire(TurnInterruptParams, raw_params)
            )
        else:
            return None
        return DispatchResult(
            result.response,
            after_response=result.after_response,
            on_response_abandoned=result.on_response_abandoned,
            runtime_updated=result.runtime_updated,
        )

    @staticmethod
    async def _dispatch_backend_queued_edit(
        root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> SessionBackendResult[Any]:
        if not isinstance(root, SessionBackendQueuedTurnEditing):
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "The selected session backend does not support queued editing",
            )
        if method == "session/turn/queue/edit/start":
            return await root.start_queued_turn_edit(
                validate_wire(TurnQueueEditStartParams, raw_params)
            )
        if method == "session/turn/queue/edit/save":
            return await root.save_queued_turn_edit(
                validate_wire(TurnQueueEditSaveParams, raw_params)
            )
        if method == "session/turn/queue/edit/abandon":
            return await root.abandon_queued_turn_edit(
                validate_wire(TurnQueueEditAbandonParams, raw_params)
            )
        return await root.cancel_queued_turn_edit(
            validate_wire(TurnQueueEditCancelParams, raw_params)
        )

    async def _dispatch_backend_config(
        self, root: SessionBackend, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult | None:
        if method == "config/write":
            result = await root.write_config(
                validate_wire(ConfigWriteParams, raw_params)
            )
            runtime_updated = result.response.applied
        elif method == "config/model/write":
            result = await root.write_model_config(
                validate_wire(ModelConfigWriteParams, raw_params)
            )
            runtime_updated = result.response.applied
        elif method == "config/reload":
            result = await root.reload_config(
                validate_wire(ConfigReloadParams, raw_params)
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
        plugins = params.capabilities.agent_plugins
        definitions = params.capabilities.client_tool_definitions
        if (
            plugins or definitions
        ) and "tools/call" not in params.capabilities.client_tools:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS, "Client plugins require tools/call"
            )
        if (plugins or definitions) and self._configure_agent_plugins is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED, "Client tools are unavailable"
            )
        if self._configure_agent_plugins is not None:
            try:
                self._configure_agent_plugins(
                    plugins, definitions, _ConnectionClientToolRunner(self._link)
                )
            except ValueError as exc:
                raise RequestFailure(
                    ProtocolErrorCode.INVALID_PARAMS, str(exc)
                ) from exc
        self._client_info = params.client_info
        self._client_account = params.account
        self._client_instance = params.client_instance
        self._client_capabilities = params.capabilities
        if params.capabilities.platform_credentials is not None:
            self._platform_credentials = ConnectionPlatformCredentialProvider(
                self._resolve_platform_credentials,
                kind=params.capabilities.platform_credentials.kind,
                tenants=self._oidc_tenants,
            )
        self._credential_selection = ConnectionCredentialSelection(
            client=self._client_info,
            connection=self._start_lending(params.account),
            tenants=self._oidc_tenants,
        )
        self._initialization = InitializationState.INITIALIZE_RECEIVED
        return InitializeResponse(
            server_info=ServerInfo(name="vibe-app-server", version=__version__),
            process_id=os.getpid(),
            platform_credentials=self._platform_credentials is not None,
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
            self._note_self_hosted_deployment()
            self._gate_crash_reporting(self._host_handler.crash_reports_allowed)
            self._resume_left_turns()
            return
        if (
            notification.method == "platformCredentials/invalidated"
            and self._initialization is InitializationState.INITIALIZED
            and self._platform_credentials is not None
        ):
            validate_wire(PlatformCredentialsInvalidatedParams, notification.params)
            self._platform_credentials.invalidate()
            self._platform_credential_lenders.reinstate(self._platform_credentials)
            return
        raise JsonRpcProtocolError(
            f"Unknown or unexpected client notification: {notification.method}"
        )

    async def _handle_response(  # noqa: PLR0911 - explicit pending-request routing
        self, response: JsonRpcErrorResponse | JsonRpcSuccessResponse
    ) -> None:
        request_id = response.id
        if not isinstance(request_id, int):
            raise JsonRpcProtocolError("Server request responses require integer IDs")
        platform_credentials = self._platform_credential_requests.pop(request_id, None)
        if platform_credentials is not None:
            _settle_platform_credentials(platform_credentials, response)
            return
        if request_id in self._abandoned_platform_credential_requests:
            self._abandoned_platform_credential_requests.discard(request_id)
            return
        pending = self._client_requests.pop(request_id, None)
        if pending is not None:
            if not pending.answer.done():
                pending.answer.set_result(response)
            return
        method = self._unanswered_client_requests.pop(request_id, None)
        if method is not None:
            logger.info(
                "%s %s answered after the tool gave up on it: %s",
                method,
                request_id,
                "refused" if isinstance(response, JsonRpcErrorResponse) else "answered",
            )
            return
        callback_route = self._callback_deliveries.requests.pop(request_id, None)
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
        sessionless_notification = (
            self._root is None
            and self._initialization is InitializationState.INITIALIZED
            and isinstance(
                params,
                MCPAuthUrlParams | ImportJobUpdatedParams | StorageUsageUpdatedParams,
            )
        )
        if not self._connection_attached and not sessionless_notification:
            return
        await self._send_notification(method, params)

    async def _subscribe(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        """The connection's subscriptions to changes of stored sessions, and of
        the config a new session would run.
        """
        if method == "config/subscribe":
            validate_wire(ConfigSubscribeParams, raw_params)
            self._subscribe_config_changes()
            return DispatchResult(EmptyResponse())
        host = self._session_backend_host
        if method == "session/lease/subscribe" and isinstance(
            host, SessionBackendHostLeaseWatch
        ):
            return await self._leases.subscribe(host)
        if method == "artifacts/subscribe" and isinstance(
            host, SessionBackendHostArtifacts
        ):
            self._subscribe_artifacts()
            return DispatchResult(EmptyResponse())
        raise method_not_found(method)

    def _stop_storage_updates_now(self) -> None:
        stop = self._stop_storage_updates
        self._stop_storage_updates = None
        if stop is not None:
            stop()

    async def _dispatch_storage_usage(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        service = self._storage_usage
        if service is None:
            raise method_not_found(method)
        if self._stop_storage_updates is None:
            self._stop_storage_updates = service.listen(
                self._route_notification, self._storage_telemetry
            )
        if method == "storage/usage/read":
            validate_wire(StorageUsageReadParams, raw_params)
            return DispatchResult(await service.read())
        if method == "storage/usage/refresh":
            params = validate_wire(StorageUsageRefreshParams, raw_params)
            return DispatchResult(await service.refresh(params))
        if method == "storage/sessions/delete":
            delete = validate_wire(StorageSessionsDeleteParams, raw_params)
            return DispatchResult(await self._delete_stored_sessions(delete))
        if method == "storage/leftovers/clean":
            clean = validate_wire(StorageLeftoversCleanParams, raw_params)
            return DispatchResult(
                await service.clean_leftovers(
                    clean, self._host_handler.harness_trust_store()
                )
            )
        raise method_not_found(method)

    async def _delete_stored_sessions(
        self, params: StorageSessionsDeleteParams
    ) -> StorageSessionsDeleteResponse:
        """A plain delete of the named sessions, archived or not, as
        ``session/delete`` does: each through it, one at a time, unless a live
        process holds its lease, read again right before that session's
        delete. Then the worktrees they left with no session, removed the
        same way for every client.
        """
        service = self._storage_usage
        if service is None:
            raise method_not_found("storage/sessions/delete")
        before = await service.read()
        histories = (
            {}
            if before.snapshot is None
            else {row.session_id: row.history_bytes for row in before.snapshot.sessions}
        )
        removed: list[str] = []
        kept: list[StorageSessionKept] = []
        for session_id in dict.fromkeys(params.session_ids):
            # Per session: one may have been opened while earlier ones went.
            if await asyncio.to_thread(service.open_session_ids, {session_id}):
                kept.append(StorageSessionKept(id=session_id, reason="in_use"))
                continue
            try:
                await self._delete_session_now({"sessionId": session_id})
            # The adapter refuses with SessionBackendError, the host handler
            # with RequestFailure; both carry a protocol code.
            except (RequestFailure, SessionBackendError) as exc:
                reason = _kept_reason(exc.code, exc.data)
                if reason == "error":
                    logger.warning(
                        "Failed to delete stored session %s", session_id, exc_info=True
                    )
                kept.append(StorageSessionKept(id=session_id, reason=reason))
                continue
            # Broad: one failed delete must not keep the others from running.
            except Exception:
                logger.warning(
                    "Failed to delete stored session %s", session_id, exc_info=True
                )
                kept.append(StorageSessionKept(id=session_id, reason="error"))
                continue
            removed.append(session_id)
        # Past this point the sessions are gone: whatever fails, the answer
        # must still list them, or clients would keep sessions with no files.
        if removed:
            await asyncio.to_thread(touch_stale_marker)
        freed = await service.finish_deletes(
            removed, self._host_handler.harness_trust_store()
        )
        return StorageSessionsDeleteResponse(
            removed=removed,
            kept=kept,
            removed_worktree_ids=freed.removed_worktree_ids,
            kept_worktrees=freed.kept_worktrees,
            freed_bytes=sum(histories.get(item, 0) for item in removed)
            + freed.worktree_bytes,
            freed_bytes_at_least=freed.at_least,
        )

    async def _storage_telemetry(self) -> TelemetryClient:
        return await self._host_handler.setup_telemetry(self._initialized_client_info())

    def _subscribe_config_changes(self) -> None:
        if self._config_changes is None:
            raise method_not_found("config/subscribe")
        if self._unsubscribe_config is not None:
            return
        self._unsubscribe_config = self._config_changes.subscribe(
            lambda: self._send_notification_soon(
                "config/changed", ConfigChangedParams()
            )
        )

    def _stop_config_subscription(self) -> None:
        unsubscribe = self._unsubscribe_config
        self._unsubscribe_config = None
        if unsubscribe is not None:
            unsubscribe()

    def _stop_artifact_subscription(self) -> None:
        unsubscribe = self._unsubscribe_artifacts
        self._unsubscribe_artifacts = None
        if unsubscribe is not None:
            unsubscribe()

    def _send_notification_soon(self, method: str, params: ProtocolModel) -> None:
        task = asyncio.create_task(self._send_notification(method, params))
        self._request_tasks.add(task)
        task.add_done_callback(self._request_tasks.discard)

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
        deliveries = self._callback_deliveries
        if callback.callback_id in deliveries.sent_since_attach:
            # The re-send after an attach and live delivery overlap, in either
            # order: the client already has it.
            return
        callback_kind = callback.detail.kind
        if callback_kind not in self._client_capabilities.callback_kinds:
            raise RuntimeError(f"Client does not support {callback_kind} callbacks")
        deliveries.sent_since_attach.add(callback.callback_id)
        request_id = self._next_request_id
        self._next_request_id += 1
        deliveries.requests[request_id] = CallbackDelivery(
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
            self._callback_deliveries.requests.pop(request_id, None)
            self._disconnect_current_client()

    async def _publish_callback(self, callback: PublicCallbackEntry) -> None:
        root = self._root
        if isinstance(root, SessionBackendCallbackSink):
            if await root.publish_callback(callback):
                return
        await self._deliver_callback(callback)

    def _mark_callback_answered(self, callback_id: str) -> None:
        for delivery in self._callback_deliveries.requests.values():
            if delivery.callback_id == callback_id:
                delivery.answered = True

    async def _activate_lifecycle_backend(
        self, method: str, backend: SessionBackend, history_limit: int
    ) -> SessionReadResponse:
        return await self._activate_backend(
            backend,
            history_limit,
            announce_running_turn=method in _RUNNING_TURN_ANNOUNCEMENT_METHODS,
        )

    async def _announce_running_turn(self) -> None:
        root = self._root
        if not isinstance(root, SessionBackendRunningTurnAnnouncement):
            return
        await root.announce_running_turn()
        await self._flush_backend_events(root)

    async def _wait_for_running_turn_announcement(self) -> None:
        """Let a reopened turn's `turn/started` reach the client before its callbacks.

        A client reads a `callback/call` against the turn it knows is running.
        """
        root = self._root
        if not isinstance(root, SessionBackendHeldEvents):
            return
        try:
            await asyncio.wait_for(
                root.wait_for_held_events(),
                timeout=_RUNNING_TURN_ANNOUNCEMENT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "The resumed turn was not announced in time; sending its open "
                "callbacks anyway",
                extra={"session_id": root.session_id},
            )

    async def _redeliver_open_callbacks(self, attach_method: str) -> None:
        callbacks = await self._read_open_callbacks(attach_method)
        supported = set(self._client_capabilities.callback_kinds)
        # _deliver_callback skips one already sent live while the read was in flight.
        for callback in callbacks or []:
            # The check refuses such a client, unless it could not read the
            # callbacks or this one opened since: the client cannot answer it,
            # so it is rejected, as a refused delivery is.
            if callback.detail.kind not in supported:
                await self._reject_undeliverable_callback(callback)
                continue
            await self._deliver_callback(callback)

    async def _reject_undeliverable_callback(
        self, callback: PublicCallbackEntry
    ) -> None:
        kind = callback.detail.kind
        logger.warning(
            "Rejecting callback %s: the client does not support %s",
            callback.callback_id,
            kind,
        )
        try:
            await self._reject_callback(
                callback.session_id,
                callback.callback_id,
                f"The client does not support {kind} callbacks",
            )
        except Exception:
            # It may have closed since the read; the client stays attached.
            logger.warning(
                "Could not reject callback %s", callback.callback_id, exc_info=True
            )

    async def _validate_open_callback_capabilities(
        self, attach_method: str, root: SessionBackend | None = None
    ) -> None:
        callbacks = (
            await self._read_open_callbacks(attach_method)
            if root is None
            else await self._read_callbacks_of(root, attach_method)
        )
        if callbacks is None:
            return
        required = {callback.detail.kind for callback in callbacks}
        missing = sorted(required - set(self._client_capabilities.callback_kinds))
        if not missing:
            return
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            f"Client does not support open callback kinds: {', '.join(missing)}",
            data=cast(JsonValue, {"missingCallbackKinds": missing}),
        )

    async def _read_open_callbacks(
        self, attach_method: str
    ) -> list[PublicCallbackEntry] | None:
        """The open callbacks an attach checks and sends, or None if unreadable.

        A failed read must not lock the client out of its Session: it still
        attaches and can answer, and callbacks that open later reach it live.
        """
        return await self._read_callbacks_of(self._root, attach_method)

    async def _read_callbacks_of(
        self, root: SessionBackend | None, attach_method: str
    ) -> list[PublicCallbackEntry] | None:
        if not isinstance(root, SessionBackendOpenCallbacks):
            return None
        try:
            callbacks = await root.read_open_callbacks()
        except Exception as exc:
            logger.warning("Could not read the open callbacks", exc_info=exc)
            self._report_dispatch_error(
                attach_method, exc, boundary="app_server_open_callbacks"
            )
            return None
        return [
            callback
            for callback in callbacks
            if isinstance(callback.state, OpenCallbackState)
        ]

    def client_info(self) -> ClientInfo:
        return self._initialized_client_info()

    def client_capabilities(self) -> ClientCapabilities:
        return self._client_capabilities

    def client_owner(self) -> JsonValue:
        """Who the connection is: its entrypoint and the account it reported.
        None when it reported no account: such a connection owns no turn.
        """
        account = self._client_account
        if account is None:
            return None
        return {
            "entrypoint": self._initialized_client_info().entrypoint,
            "account": account.model_dump(mode="json"),
        }

    def lender_availability(self) -> LenderAvailability:
        """Whether a session of this connection's account can still borrow a
        sign-in once its own channel is gone. The account is kept after a
        close, so a parked session's link still answers.
        """
        return "none" if self._lending is None else self._lending.availability()

    def current_session_id(self) -> str:
        return self._require_root().session_id

    def account_gateway(self) -> AccountGateway | None:
        return self._account_gateway

    def identity_gateway(self) -> IdentityGateway | None:
        return self._identity_gateway

    def _new_vibe_bot(self, sessions: VibeBotLocalSessions | None) -> VibeBotController:
        return VibeBotController(
            local_tools=self._new_vibe_bot_tools(),
            credentials=lambda: self.platform_credentials(MISTRAL_API_KEY_ENV),
            client=_VibeBotClientRequests(self),
            api_origin=lambda: self._host_handler.mistral_api_origin(),
            project_roots=_local_project_roots,
            client_instance=lambda: self._client_instance,
            sessions=sessions
            or VibeBotLocalSessions(
                client_id_file=VIBE_HOME.path / VIBE_BOT_CLIENT_ID_FILE
            ),
        )

    def _new_vibe_bot_tools(self) -> LocalTools | None:
        """Read from the user's own config only: a repository's config could
        define a server under a name the user allowlisted.
        """
        if self._mcp_catalog_service is None:
            return None

        def catalogs() -> MCPCatalogService:
            service = self._mcp_catalog_service
            if service is None:
                raise RuntimeError("The connection has no MCP catalog")
            return service

        async def read_config() -> VibeConfigSchema:
            return (await self._host_handler.user_config_orchestrator()).config

        async def catalog(owner: object) -> ResolvedMCPCatalog:
            orchestrator = await self._host_handler.user_config_orchestrator()
            return await catalogs().resolve_catalog(orchestrator, owner=owner)

        async def mcp() -> LocalMCP:
            return HarnessLocalMCP(
                catalog=catalog,
                authorization=catalogs().authentication,
                cache_root=VIBE_HOME.path / VIBE_BOT_MCP_CACHE_DIR,
            )

        return LocalTools(read_config=read_config, mcp=mcp)

    def platform_credentials(self, api_key_env_var: str) -> PlatformCredentialProvider:
        if self._credential_selection is not None:
            return self._credential_selection(api_key_env_var)
        # Before ``initialize``: no client yet, so nothing to keep.
        return select_platform_credentials(
            client=self._client_info,
            connection=self._platform_credentials,
            tenants=self._oidc_tenants,
            api_key_env_var=api_key_env_var,
        )

    def task_finished(self, task: asyncio.Task[None]) -> None:
        self._request_finished(task)

    async def notify(self, method: str, params: ProtocolModel) -> None:
        await self._notify(method, params)

    async def publish_callback(self, callback: PublicCallbackEntry) -> None:
        await self._publish_callback(callback)

    async def spawn_session(self, params: SessionSpawnParams) -> SessionSpawnResponse:
        if not self._client_capabilities.session_spawn:
            raise SessionSpawnError("The attached client cannot start sessions")
        try:
            return await self._request_client(
                SESSION_SPAWN_METHOD, params, SessionSpawnResponse
            )
        except _ClientUnreachable as exc:
            raise SessionSpawnError(
                f"Could not reach the client to start the session: {exc}"
            ) from exc
        except _ClientRequestLost as exc:
            raise SessionSpawnError(
                f"{exc} before confirming the session, so it may or may not have "
                "started. Do not start it again before the user has checked their "
                "sessions."
            ) from exc
        except _ClientRequestFailed as exc:
            raise SessionSpawnError(str(exc)) from exc

    async def list_peer_sessions(
        self, params: SessionPeerListParams
    ) -> SessionPeerListResponse:
        return await self._request_peers(
            SESSION_PEER_LIST_METHOD, params, SessionPeerListResponse
        )

    async def read_peer_session(
        self, params: SessionPeerReadParams
    ) -> SessionPeerReadResponse:
        return await self._request_peers(
            SESSION_PEER_READ_METHOD, params, SessionPeerReadResponse
        )

    async def send_to_peer_session(
        self, params: SessionPeerSendParams
    ) -> SessionPeerSendResponse:
        if self._vibe_bot.sessions.handles(params.target_session_id):
            return await self._vibe_bot.sessions.deliver_reply(
                params, instance=self._client_instance
            )
        return await self._request_peers(
            SESSION_PEER_SEND_METHOD,
            params,
            SessionPeerSendResponse,
            lost=(
                "before confirming the message, so it may or may not have been "
                "sent. Read the session before sending it again."
            ),
        )

    async def archive_peer_sessions(
        self, params: SessionPeerArchiveParams
    ) -> SessionPeerArchiveResponse:
        return await self._request_peers(
            SESSION_PEER_ARCHIVE_METHOD,
            params,
            SessionPeerArchiveResponse,
            lost=(
                "before confirming, so some sessions may have been archived. "
                "List them before archiving again."
            ),
        )

    async def restore_peer_sessions(
        self, params: SessionPeerRestoreParams
    ) -> SessionPeerRestoreResponse:
        if not self._client_capabilities.session_peer_restore:
            raise SessionPeerError("The attached client cannot restore sessions")
        return await self._request_peers(
            SESSION_PEER_RESTORE_METHOD,
            params,
            SessionPeerRestoreResponse,
            lost=(
                "before confirming, so some sessions may have been restored. "
                "List them before restoring again."
            ),
        )

    async def rename_peer_session(
        self, params: SessionPeerRenameParams
    ) -> SessionPeerRenameResponse:
        if not self._client_capabilities.session_peer_rename:
            raise SessionPeerError("The attached client cannot rename sessions")
        return await self._request_peers(
            SESSION_PEER_RENAME_METHOD,
            params,
            SessionPeerRenameResponse,
            lost="before confirming; the rename may have been applied.",
        )

    async def mark_peer_sessions_seen(
        self, params: SessionPeerMarkSeenParams
    ) -> SessionPeerMarkSeenResponse:
        if not self._client_capabilities.session_peer_mark_seen:
            raise SessionPeerError("The attached client cannot mark sessions as seen")
        return await self._request_peers(
            SESSION_PEER_MARK_SEEN_METHOD,
            params,
            SessionPeerMarkSeenResponse,
            lost=(
                "before confirming, so some sessions may have been marked seen. "
                "List them before marking them again."
            ),
        )

    async def _request_peers[ResponseT: ProtocolModel](
        self,
        method: str,
        params: ProtocolModel,
        response_type: type[ResponseT],
        *,
        lost: str = "before answering. Try again.",
    ) -> ResponseT:
        if not self._client_capabilities.session_peers:
            raise SessionPeerError("The attached client cannot reach other sessions")
        try:
            return await self._request_client(method, params, response_type)
        except _ClientRequestLost as exc:
            raise SessionPeerError(f"{exc} {lost}") from exc
        except _ClientRequestFailed as exc:
            if _refusal_reason(exc.data) == PEER_SEND_NOT_DELIVERED_IN_TIME:
                raise SessionPeerError(
                    "The session could not be reached in time, so the message "
                    "was not sent. Send it again later, or tell the user.",
                    reason=PEER_SEND_NOT_DELIVERED_IN_TIME,
                ) from exc
            raise SessionPeerError(str(exc)) from exc

    async def _request_client[ResponseT: ProtocolModel](
        self,
        method: str,
        params: ProtocolModel,
        response_type: type[ResponseT],
        *,
        sessionless: bool = False,
    ) -> ResponseT:
        """A refusal or a malformed answer fails this request, not the
        connection. A ``sessionless`` request only needs an initialized
        client, not an attached session.
        """
        transport = self._transport
        if transport is None or not (
            self._connection_attached
            or (sessionless and self._initialization is InitializationState.INITIALIZED)
        ):
            raise _ClientRequestFailed("No client is attached")
        request_id = self._next_request_id
        self._next_request_id += 1
        pending = _ClientRequest(
            method=method, answer=asyncio.get_running_loop().create_future()
        )
        self._client_requests[request_id] = pending
        try:
            try:
                await transport.send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    # An option left out is absent rather than null.
                    "params": params.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                })
            except Exception as exc:
                raise _ClientUnreachable(str(exc)) from exc
            response = await pending.answer
        finally:
            self._client_requests.pop(request_id, None)
            if pending.answer.cancelled() or not pending.answer.done():
                self._unanswered_client_requests[request_id] = method
        if isinstance(response, JsonRpcErrorResponse):
            raise _ClientRequestFailed(
                response.error.message, response.error.code, data=response.error.data
            )
        try:
            return validate_wire(response_type, response.result)
        except ValidationError as exc:
            raise _ClientRequestFailed(
                f"The client answered {method} invalidly: {exc}"
            ) from exc

    async def _resolve_platform_credentials(
        self, params: PlatformCredentialsResolveParams
    ) -> PlatformCredentialsResolveResponse:
        transport = self._transport
        if transport is None or self._platform_credentials is None:
            raise RuntimeError("Desktop platform credentials are unavailable")
        request_id = self._next_request_id
        self._next_request_id += 1
        pending: asyncio.Future[PlatformCredentialsResolveResponse] = (
            asyncio.get_running_loop().create_future()
        )
        self._platform_credential_requests[request_id] = pending
        try:
            await transport.send({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "platformCredentials/resolve",
                "params": params.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
            })
            return await pending
        finally:
            self._platform_credential_requests.pop(request_id, None)
            if pending.cancelled() or not pending.done():
                self._abandoned_platform_credential_requests.add(request_id)

    def _owner_key(self) -> Owner | None:
        if self._client_account is None or self._client_info is None:
            return None
        return owner_key(self._client_info, self._client_account)

    def _left_turn_owner(
        self,
    ) -> tuple[Owner, ResumeOne, OpenSessions, InitializeParams] | None:
        """This connection's owner, and how to resume one of its sessions on a
        channel of its own, initialized as this connection was so that a
        client of the same owner opening the session later attaches to it.
        """
        if (
            self._left_turns is None
            or self._open_left_turn_channel is None
            or self._open_sessions is None
            or self._client_account is None
            or self._client_info is None
        ):
            return None
        params = InitializeParams(
            client_info=self._client_info,
            capabilities=self._client_capabilities.model_copy(
                update={"resume_left_turns": False}
            ),
            account=self._client_account,
        )
        open_channel = self._open_left_turn_channel

        async def resume(session_id: str, interrupt: bool) -> None:
            await open_channel(params, session_id, interrupt)

        return (
            owner_key(self._client_info, self._client_account),
            resume,
            self._open_sessions,
            params,
        )

    def _resume_left_turns(self) -> None:
        found = self._left_turn_owner()
        if (
            found is None
            or self._left_turns is None
            or not self._client_capabilities.resume_left_turns
        ):
            return
        owner, resume, sessions, params = found
        list_on_own_channel = self._list_on_own_channel
        if list_on_own_channel is None:
            return

        # On a channel of the process's own, as its resumes: this connection
        # may have closed by the time the owner's claim lets it list. Listing
        # needs no sign-in, so that channel neither lends nor borrows one.
        listing = params.model_copy(
            update={
                "capabilities": params.capabilities.model_copy(
                    update={"platform_credentials": None}
                )
            }
        )

        async def list_left() -> list[str]:
            return await list_on_own_channel(listing)

        if self._lacks_process_lender():
            return
        assert self._client_account is not None
        host = self._session_backend_host
        # Only a store shared with other processes needs a claim among them.
        claim = (
            LeftTurnsClaim(owner, host.storage_root)
            if isinstance(host, SessionBackendHostStorageRoot)
            else None
        )
        # A client without platform credentials signs in on its own.
        lender: LenderOf = (
            AccountLender(
                self._platform_credential_lenders, account_key(self._client_account)
            )
            if self._platform_credentials is not None
            else SignsInItself()
        )
        self._left_turns.resume_once(
            owner, list_left, resume, sessions, lender=lender, claim=claim
        )

    def _lacks_process_lender(self) -> bool:
        """Lends its sign-in only while it is open: a left turn would run on
        it and fail once it closes.
        """
        capability = self._client_capabilities.platform_credentials
        return capability is not None and capability.lifetime != "process"

    def _left_turns_status(self, status: LeftTurnsStatus) -> LeftTurnsStatus:
        if (
            status == "not_resuming"
            and self._client_capabilities.resume_left_turns
            and self._lacks_process_lender()
        ):
            return "needs_process_lender"
        return status

    async def _dispatch_left_turns(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        owner = self._owner_key()
        # This server resumes no left turns (it is not multiplexed).
        unserved: LeftTurnsStatus = "no_account" if owner is None else "not_resuming"
        if method == "session/leftTurns/read":
            validate_wire(LeftTurnsReadParams, raw_params)
            if owner is None or self._left_turns is None:
                return DispatchResult(LeftTurnsReadResponse(status=unserved))
            read = await self._left_turns.read(owner)
            return DispatchResult(
                LeftTurnsReadResponse(
                    status=self._left_turns_status(read.status),
                    turns=[
                        LeftTurnEntry(session_id=turn.session_id, state=turn.state)
                        for turn in read.turns
                    ],
                )
            )
        validate_wire(LeftTurnsInterruptParams, raw_params)
        if owner is None or self._left_turns is None:
            return DispatchResult(LeftTurnsInterruptResponse(status=unserved))
        stopped = await self._left_turns.interrupt(owner)
        return DispatchResult(
            LeftTurnsInterruptResponse(
                status=self._left_turns_status(stopped.status),
                turns=[
                    StoppedLeftTurnEntry(
                        session_id=turn.session_id, outcome=turn.outcome
                    )
                    for turn in stopped.turns
                ],
            )
        )

    def _start_lending(
        self, account: ClientAccount | None
    ) -> PlatformCredentialProvider | None:
        """Lend this channel's sign-in to every session of ``account`` in the
        process, and give this connection a provider that borrows once it is
        gone. A channel without an account neither lends nor borrows.
        """
        own = self._platform_credentials
        self._lending = None
        if own is None or account is None:
            return own
        capability = self._client_capabilities.platform_credentials
        if self._left_turn_session is None:
            self._platform_credential_lenders.register(
                account_key(account),
                own,
                durable=capability is not None and capability.lifetime == "process",
            )
        self._lending = AccountPlatformCredentialProvider(
            own, account=account, lenders=self._platform_credential_lenders
        )
        return self._lending

    def _stop_lending(self, reason: str) -> None:
        """Stop lending, and fail the resolves in flight on this channel now:
        a session borrowing from it retries a live channel instead of waiting
        for this one's shutdown.
        """
        if self._platform_credentials is not None:
            self._platform_credential_lenders.unregister(self._platform_credentials)
        # The client may still answer them while the channel closes: such a
        # late reply is dropped, not a protocol error.
        self._abandoned_platform_credential_requests.update(
            self._platform_credential_requests
        )
        self._fail_platform_credential_requests(reason)

    def _abandon_platform_credential_requests(self, reason: str) -> None:
        self._fail_platform_credential_requests(reason)
        self._abandoned_platform_credential_requests.clear()

    def _fail_platform_credential_requests(self, reason: str) -> None:
        for pending in self._platform_credential_requests.values():
            if not pending.done():
                pending.set_exception(RuntimeError(reason))
        self._platform_credential_requests.clear()

    def _abandon_client_requests(self, reason: str) -> None:
        for pending in self._client_requests.values():
            if not pending.answer.done():
                pending.answer.set_exception(_ClientRequestLost(reason))
        self._client_requests.clear()
        self._unanswered_client_requests.clear()

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


def _init_crash_reporting(client: ClientInfo, *, enabled: bool) -> None:
    init_cli_sentry(
        enabled=enabled,
        headless=client.entrypoint == "programmatic",
        tags={
            "entrypoint": client.entrypoint,
            "client_name": client.name,
            "client_version": client.version,
        },
    )


async def _resolved(value: bool) -> bool:
    return value


def _harness_code(exc: SessionBackendError) -> str | None:
    code = exc.data.get("harnessCode") if isinstance(exc.data, dict) else None
    return code if isinstance(code, str) else None


# Conflicts the harness raises because the session is running or held.
_BUSY_HARNESS_CODES = frozenset({"session_busy"})


def _kept_reason(code: ProtocolErrorCode, data: object) -> StorageSessionKeptReason:
    """Why ``storage/sessions/delete`` kept a session its delete refused:
    ``in_use`` only when the session is busy or a host holds it; any other
    conflict, such as a child needing its parent, is ``error``.
    """
    if code is ProtocolErrorCode.NOT_FOUND:
        return "not_found"
    if code is not ProtocolErrorCode.CONFLICT:
        return "error"
    harness_code = data.get("harnessCode") if isinstance(data, dict) else None
    if harness_code is None or harness_code in _BUSY_HARNESS_CODES:
        return "in_use"
    return "error"


def _is_unexpected(exc: SessionBackendError) -> bool:
    """Only internal errors are bugs; other codes are refusals the client explains."""
    return exc.code is ProtocolErrorCode.INTERNAL_ERROR and not exc.expected


def _in_order(*callbacks: Callable[[], None] | None) -> Callable[[], None] | None:
    present = [callback for callback in callbacks if callback is not None]
    if not present:
        return None

    def run() -> None:
        for callback in present:
            callback()

    return run


@dataclass(frozen=True, slots=True)
class _ClientRequest:
    method: str
    answer: asyncio.Future[JsonRpcErrorResponse | JsonRpcSuccessResponse]


class _ClientRequestFailed(Exception):
    def __init__(
        self, message: str, code: str | None = None, *, data: JsonValue = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class _ClientUnreachable(_ClientRequestFailed):
    pass


class _ClientRequestLost(_ClientRequestFailed):
    """The client went away after the request was sent, so it may have acted."""


def _refusal_reason(data: JsonValue) -> JsonValue:
    return data.get("reason") if isinstance(data, dict) else None


def _settle_platform_credentials(
    pending: asyncio.Future[PlatformCredentialsResolveResponse],
    response: JsonRpcErrorResponse | JsonRpcSuccessResponse,
) -> None:
    """Settle a credential request without turning a broker failure into a
    connection failure. No response detail is copied into the exception so a
    misbehaving host cannot reflect credential material into logs.
    """
    if pending.done():
        return
    if isinstance(response, JsonRpcErrorResponse):
        pending.set_exception(
            RuntimeError("Desktop platform credential request failed")
        )
        return
    try:
        pending.set_result(
            TypeAdapter(PlatformCredentialsResolveResponse).validate_python(
                response.result, by_alias=True, by_name=False
            )
        )
    except ValidationError:
        pending.set_exception(
            RuntimeError("Desktop returned an invalid platform credential response")
        )


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


class _ConnectionClientToolRunner:
    def __init__(self, link: SessionLink) -> None:
        self._link = link

    @property
    def _server(self) -> AppServer:
        return self._link.server

    async def call_tool(self, call: ClientToolCall) -> ClientToolResult:
        caller = call.caller
        if caller is None:
            return ClientToolResult(
                error=ClientToolError("Client tool caller is unavailable")
            )
        params = ClientToolCallParams(
            call_id=call.call_id,
            name=call.name,
            input=call.input,
            tool_call_id=call.tool_call_id,
            caller=ClientToolCallerParams(
                session_id=caller.session_id,
                root_session_id=caller.root_session_id,
                parent_session_id=caller.parent_session_id,
                name=caller.name,
                agent_type=caller.agent_type,
            ),
        )
        try:
            result = await self._server._request_client(
                "tools/call", params, ClientToolCallResponse
            )
        except asyncio.CancelledError:
            transport = self._server._transport
            if transport is not None and self._server._connection_attached:
                try:
                    await transport.send({
                        "jsonrpc": "2.0",
                        "method": "tools/cancel",
                        "params": {"callId": call.call_id},
                    })
                except Exception:
                    pass
            raise
        except _ClientRequestLost as exc:
            return ClientToolResult(
                error=ClientToolError(
                    f"{exc} before the client confirmed the call, so it may or may "
                    "not have run. Check its effect before calling it again.",
                    code="client_tool_unavailable",
                )
            )
        except _ClientRequestFailed as exc:
            return ClientToolResult(
                error=ClientToolError(str(exc), code="client_tool_unavailable")
            )
        return ClientToolResult(
            output=result.output,
            annotations=result.annotations,
            error=ClientToolError(
                result.error.message, result.error.code, result.error.details
            )
            if result.error is not None
            else None,
        )


async def _local_project_roots() -> list[Path]:
    """Where the bot may start a session: the user's local projects and Vibe's worktrees."""

    def read() -> list[Path]:
        projects = list_local_projects(LocalProjectStore())
        roots = [root for project in projects for root in project.root_paths]
        return [*roots, WORKTREES_DIR.path]

    return await asyncio.to_thread(read)


class _VibeBotClientRequests:
    """``VibeBotClient`` as requests to this connection's client."""

    def __init__(self, server: AppServer) -> None:
        self._server = server

    async def _ask[ResponseT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResponseT]
    ) -> ResponseT:
        try:
            return await self._server._request_client(
                method, params, response_type, sessionless=True
            )
        except _ClientRequestFailed as exc:
            if exc.code == ProtocolErrorCode.NOT_FOUND:
                raise VibeBotLocalSessionGone(str(exc)) from exc
            raise RuntimeError(str(exc)) from exc

    def session_tools(self) -> bool:
        return self._server._client_capabilities.session_peers

    async def start_local(
        self,
        *,
        bot: str,
        idempotency_key: str,
        thread_id: str,
        prompt: str,
        title: str,
        checkout: dict[str, Any] | None,
        model: str | None = None,
        approval_policy: str | None = None,
    ) -> tuple[str, str | None]:
        response = await self._ask(
            VIBE_BOT_START_LOCAL_METHOD,
            VibeBotStartLocalParams(
                bot_workflow_id=bot,
                idempotency_key=idempotency_key,
                thread_id=thread_id,
                prompt=prompt,
                title=title,
                checkout=checkout,
                starter_session_id=bot_sender_id(bot, thread_id),
                model=model,
                approval_policy=approval_policy,
            ),
            VibeBotStartLocalResponse,
        )
        return response.local_session_id, response.turn_id

    async def send_local(
        self,
        *,
        bot: str,
        local_session_id: str,
        thread_id: str,
        text: str,
        idempotency_key: str,
        delivery: PeerDelivery = "start_turn",
    ) -> str | None:
        response = await self._ask(
            VIBE_BOT_SEND_LOCAL_METHOD,
            VibeBotSendLocalParams(
                bot_workflow_id=bot,
                local_session_id=local_session_id,
                text=text,
                idempotency_key=idempotency_key,
                sender_session_id=bot_sender_id(bot, thread_id),
                delivery=delivery,
            ),
            VibeBotSendLocalResponse,
        )
        return response.turn_id

    async def stop_local(
        self, *, bot: str, local_session_id: str, idempotency_key: str
    ) -> None:
        await self._ask(
            VIBE_BOT_STOP_LOCAL_METHOD,
            VibeBotStopLocalParams(
                bot_workflow_id=bot,
                local_session_id=local_session_id,
                idempotency_key=idempotency_key,
            ),
            VibeBotEmpty,
        )

    async def archive_local(self, *, bot: str, local_session_id: str) -> None:
        await self._ask(
            VIBE_BOT_ARCHIVE_LOCAL_METHOD,
            VibeBotArchiveLocalParams(
                bot_workflow_id=bot, local_session_id=local_session_id
            ),
            VibeBotEmpty,
        )

    async def report(self, bot: str, event: dict[str, Any]) -> bool:
        response = await self._ask(
            VIBE_BOT_REPORT_METHOD,
            VibeBotReportParams(bot_workflow_id=bot, event=event),
            VibeBotReportResponse,
        )
        if response.refused is not None:
            raise VibeBotCallRefused(response.refused)
        return response.duplicate
