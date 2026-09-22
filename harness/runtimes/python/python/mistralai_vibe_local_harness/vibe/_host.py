"""Process lifetime and Harness Session registry ownership."""

import asyncio
import builtins
import json
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import JsonValue, TypeAdapter

from mistralai_vibe_local_harness import HarnessSession
from mistralai_vibe_local_harness.protocol import (
    RustContextSettings,
    RustDisabledCompactionPolicy,
    RustDisabledLargeOutputPolicy,
    RustDisabledRuntimeToolFeature,
    RustEnabledRuntimeToolFeature,
    RustHarnessCapabilitySet,
    RustHarnessConfig,
    RustHarnessHookBinding,
    RustHarnessSettings,
    RustImageContentBlock,
    RustProgrammaticToolSettings,
    RustProvidedToolCallAction,
    RustProvidedToolDefinition,
    RustToolGroupDefinition,
    RustToolSettings,
    RustTurnSettings,
    RustUnixCommandEnvironment,
)
from mistralai_vibe_local_harness.session_protocol import (
    APP_SERVER_SESSION_IMPLEMENTED_PROCEDURES,
    APP_SERVER_SESSION_PLUGIN_PROCEDURES,
    TURN_QUEUE_MAX_ITEMS,
    BlockedSessionStatus,
    CompletedPublicTurn,
    FailedPublicTurn,
    HistoryCursor,
    IdleSessionStatus,
    InterruptedPublicTurn,
    JsonObject,
    LatestPublicHistoryPage,
    PluginInfo,
    Procedure,
    PublicSession,
    PublicSessionState,
    ResolvedPluginDefinition,
    RunningSessionStatus,
    SessionReadParams,
    SessionSnapshot,
    SessionStartParams,
    TurnQueue,
)
from mistralai_vibe_local_harness.vibe._connector_actions import (
    build_connector_action_executor,
)
from mistralai_vibe_local_harness.vibe._connector_models import (
    ConnectorAuthorizationRequiredSignal,
    ConnectorGateway,
    ConnectorRouteSnapshot,
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
)
from mistralai_vibe_local_harness.vibe._connector_runtime import (
    ConnectorRuntime,
    plan_connector_snapshot,
)
from mistralai_vibe_local_harness.vibe._errors import (
    HarnessChildSessionRequiresParentError,
    HarnessInvalidMigrationSourceError,
    HarnessReplayDivergenceError,
    HarnessSessionBusyError,
    HarnessSessionDeleteError,
    HarnessSessionNotFoundError,
    HarnessUnfinishedMigrationError,
)
from mistralai_vibe_local_harness.vibe._file_image_fallback import (
    materialize_file_image_fallback,
)
from mistralai_vibe_local_harness.vibe._local_actions import (
    HookHandlers,
    ProvidedToolApproval,
    ProvidedToolExecutor,
    ProvidedToolExecutorFactory,
    _LocalActionState,
    foreign_binding_ids,
    has_hook_handlers,
    merge_hook_handlers,
    streams_provisional_content,
)
from mistralai_vibe_local_harness.vibe._mcp_actions import build_mcp_action_executor
from mistralai_vibe_local_harness.vibe._mcp_models import (
    MCPAuthorizationProvider,
    MCPAuthorizationRequiredSignal,
    MCPDescriptorCachePolicy,
    MCPHTTPTransportPolicy,
    MCPRouteSnapshot,
    MCPSamplingCompletion,
    MCPTransportFactory,
    ResolvedMCPCatalog,
)
from mistralai_vibe_local_harness.vibe._mcp_pool import MCPStdioPool
from mistralai_vibe_local_harness.vibe._mcp_runtime import MCPRuntime
from mistralai_vibe_local_harness.vibe._observability import (
    add_recovery_failure,
    add_subagent_orphan_detected,
    record_session_operation,
)
from mistralai_vibe_local_harness.vibe._process_actions import validate_process_config
from mistralai_vibe_local_harness.vibe._processes._backend import TerminalBackend
from mistralai_vibe_local_harness.vibe._processes._manager import (
    ProcessTerminalSnapshot,
    SessionProcessManager,
)
from mistralai_vibe_local_harness.vibe._processes._output import ProcessOutputStore
from mistralai_vibe_local_harness.vibe._projection import (
    renamed_session_state,
    settle_stalled_projection,
    with_session_preview,
)
from mistralai_vibe_local_harness.vibe._runtime import (
    DurableSessionRuntime,
)
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._session import UnifiedHarnessSessionBackend
from mistralai_vibe_local_harness.vibe._session_catalog import (
    SessionCatalogEntryV1,
    UnifiedSessionCatalog,
)
from mistralai_vibe_local_harness.vibe._session_id import generate_session_id
from mistralai_vibe_local_harness.vibe._storage import (
    ImportProvenanceV1,
    InteropAssistantContentPartV1,
    InteropAssistantMessageV1,
    InteropHistoryMessageV1,
    InteropImageContentBlockV1,
    InteropToolMessageV1,
    LegacyInteropSourceV1,
    PluginLockV1,
    ProjectionStateV1,
    RuntimeStateV3,
    SessionLease,
    SessionMetadataV1,
    SessionPin,
    StoredSession,
    UnifiedInteropSourceV1,
    UnifiedSessionStore,
    canonical_json,
    committed_history,
    compute_projection_delta,
    empty_runtime_state,
    history_fingerprint,
    session_is_live,
)
from mistralai_vibe_local_harness.vibe._subagents._configuration import (
    LocalChildSessionBinding,
    ResolvedSubagentConfiguration,
    advertise_bound_agent_types,
    child_config,
    resolve_declared_agent_types,
    resolve_subagent_configuration,
)
from mistralai_vibe_local_harness.vibe._subagents._controller import SubagentController
from mistralai_vibe_local_harness.vibe._subagents._host import (
    ChildCommandAdmission,
    ChildSessionHandle,
    ResolvedChildSessionBinding,
)
from mistralai_vibe_local_harness.vibe._subagents._models import (
    ChildGenerationRef,
    ChildTurnOutcome,
    CloseRunningTarget,
    CompletedChildTurnOutcome,
    DeletingSessionTree,
    FailedChildTurnOutcome,
    InterruptedChildTurnOutcome,
    InterruptTarget,
    SessionIdentity,
    SpawnTarget,
    SubagentFailure,
    SubagentSessionIdentity,
)
from mistralai_vibe_local_harness.vibe.plugins import (
    PluginPackageStore,
    PluginRestoreDiagnostic,
    PluginRestoreDiagnosticCode,
    PluginRestoreError,
    SessionPluginBinder,
    SessionPluginBinding,
    SessionPluginProvider,
    empty_plugin_binding,
)


@dataclass(frozen=True, slots=True)
class LegacySessionReference:
    session_id: str
    cwd: str


@dataclass(frozen=True, slots=True)
class LegacyImportSource:
    state: Literal["absent", "quiescent", "recoverable", "invalid"]
    reference: LegacySessionReference | None = None
    store_revision: str | None = None
    history: list[JsonObject] | None = None
    active_model: str | None = None
    agent_name: str | None = None
    reasoning_effort: str | None = None
    error: str | None = None

    def pin(self, pin: SessionPin) -> str | None:
        return getattr(self, pin.value)

    def pin_values(self) -> dict[str, str | None]:
        """The imported pins as constructor kwargs, so propagation copies them as a set."""
        return {pin.value: getattr(self, pin.value) for pin in SessionPin}


type LegacySourceLoader = Callable[[str], LegacyImportSource]
type LegacySourceResolver = Callable[[str], LegacySessionReference | None]


_INTEROP_HISTORY_ADAPTER = TypeAdapter(list[InteropHistoryMessageV1])

logger = logging.getLogger(__name__)
_SHORT_SESSION_ID_LENGTH = 8
_PROCESS_OUTPUT_TARGET_BYTES = 500 * 1024 * 1024
_LEASE_RETRY_DELAY_SECONDS = 0.01
_PROCESS_OUTPUT_CLEANUP_DEBOUNCE_SECONDS = 5.0
_IMPORTED_ENTRY_ID_PREFIX = "imported-"


@dataclass(frozen=True, slots=True)
class HarnessSessionListItem:
    session: PublicSession
    cwd: str | None


@dataclass(frozen=True, slots=True)
class HarnessSessionListResult:
    items: tuple[HarnessSessionListItem, ...]
    continue_session_id: str | None
    next_cursor: str | None = None
    previous_cursor: str | None = None

    @property
    def sessions(self) -> tuple[PublicSession, ...]:
        return tuple(item.session for item in self.items)


@dataclass(frozen=True, slots=True)
class HarnessSessionReadResult:
    snapshot: SessionSnapshot
    cwd: str | None


@dataclass(frozen=True, slots=True)
class HarnessSessionForkResult:
    source_session_id: str
    session: UnifiedHarnessSessionBackend


@dataclass(frozen=True, slots=True)
class HarnessSessionDeleteResult:
    root_session_id: str
    deleted_session_ids: tuple[str, ...]


@dataclass(slots=True)
class _TreeDeletionLeases:
    root: SessionLease
    children: dict[str, SessionLease]


@dataclass(slots=True)
class _LoadedSessionEntry:
    session: UnifiedHarnessSessionBackend
    attachments: int
    maintenance_pins: int = 0
    state: Literal["open", "evicting", "closing"] = "open"
    closed: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class _LoadingSession:
    identity: tuple[object, ...]
    completed: asyncio.Future[tuple[str | None, BaseException | None]]


class _SessionAttachment:
    def __init__(
        self,
        session: UnifiedHarnessSessionBackend,
        detach: Callable[[UnifiedHarnessSessionBackend], Awaitable[None]],
    ) -> None:
        self._session = session
        self._detach = detach
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def cwd(self) -> str | None:
        return self._session.cwd

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._detach(self._session)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def _extend_subagent_bindings(
    configured: ResolvedSubagentConfiguration | None,
    adapter: LocalRuntimeAdapterConfig | None,
    plugins: SessionPluginBinding | None,
) -> ResolvedSubagentConfiguration | None:
    # Takes its inputs rather than reading ``self``: the ephemeral path resolves
    # against the configuration it captured at creation, and a promoted config built
    # from one ceiling with bindings resolved under another will not open.
    if configured is None or adapter is None or plugins is None or not plugins.agent_profiles:
        return configured
    return resolve_declared_agent_types(configured, adapter, plugins.agent_profiles)


class UnifiedHarnessSessionBackendHost:
    def __init__(self, storage_root: Path | None = None) -> None:
        self._storage_root = storage_root or _default_storage_root()
        self._sessions: dict[str, _LoadedSessionEntry] = {}
        self._loading_sessions: dict[str, _LoadingSession] = {}
        self._maintenance_sessions: dict[str, asyncio.Event] = {}
        self._registry_lock = asyncio.Lock()
        self._legacy_source_loader: LegacySourceLoader | None = None
        self._legacy_source_resolver: LegacySourceResolver | None = None
        self._runtime_config_template = _core_config("runtime-template")
        self._adapter_config_template: LocalRuntimeAdapterConfig | None = None
        self._configured_runtime_config: RustHarnessConfig | None = None
        self._configured_adapter_config: LocalRuntimeAdapterConfig | None = None
        self._hook_handlers = HookHandlers()
        self._provided_tool_groups: dict[str, _ProvidedToolGroup] = {}
        self._plugin_provider: SessionPluginProvider | None = None
        self._requested_plugins: tuple[ResolvedPluginDefinition, ...] = ()
        self._mcp_catalog: ResolvedMCPCatalog | None = None
        self._mcp_authorization_provider: MCPAuthorizationProvider | None = None
        self._mcp_cache_root = self._storage_root / "mcp-descriptors"
        self._mcp_cache_policy = MCPDescriptorCachePolicy()
        self._mcp_http_transport_policy = MCPHTTPTransportPolicy()
        self._mcp_sampling_completion: MCPSamplingCompletion | None = None
        self._mcp_transport_factory: MCPTransportFactory | None = None
        self._mcp_stdio_pool: MCPStdioPool | None = None
        self._retired_mcp_stdio_pools: list[MCPStdioPool] = []
        # Guards the two fields above. ``_stdio_pool`` runs on ``asyncio.to_thread``
        # worker threads during session creation, so overlapping creates can otherwise
        # each build a pool and orphan one; an ``asyncio.Lock`` cannot serialise work
        # off the loop thread.
        self._mcp_stdio_pool_lock = threading.Lock()
        self._connector_catalog: ResolvedConnectorCatalog | None = None
        self._connector_selection: ResolvedConnectorSelection | None = None
        self._connector_gateway_factory: Callable[[], ConnectorGateway] | None = None
        self._connector_gateway_authority_digest: str | None = None
        self._subagent_configuration: ResolvedSubagentConfiguration | None = None
        self._child_lock = asyncio.Lock()
        self._tree_deletion_leases: dict[str, _TreeDeletionLeases] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_requested = False
        self._cleanup_finished_at: float | None = None
        self._orphan_diagnostic_tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    @property
    def harness_kind(self) -> Literal["unified"]:
        return "unified"

    def configure_storage(self, storage_root: str | os.PathLike[str]) -> None:
        root = Path(storage_root).expanduser().resolve()
        if self._live_session_ids() and root != self._storage_root:
            raise RuntimeError("cannot change storage root while sessions are bound")
        self._storage_root = root

    def configure_legacy_source_loader(self, loader: LegacySourceLoader) -> None:
        self._legacy_source_loader = loader

    def configure_legacy_source_resolver(self, resolver: LegacySourceResolver) -> None:
        self._legacy_source_resolver = resolver

    def configure_runtime(
        self,
        config: RustHarnessConfig,
        adapter_config: LocalRuntimeAdapterConfig | None = None,
    ) -> None:
        self._configured_runtime_config = config
        self._configured_adapter_config = adapter_config
        if adapter_config is not None:
            validate_process_config(adapter_config)
            if (
                adapter_config.process_authority == "host_shell"
                and config.settings.tools.background_processes.mode == "enabled"
                and config.settings.tools.command_environment.mode
                != adapter_config.command_environment
            ):
                raise ValueError("Core and Runtime command environments do not match")
        self._runtime_config_template = config
        if adapter_config is not None:
            self._adapter_config_template = adapter_config
        self._refresh_subagent_configuration()

    def configure_plugins(
        self,
        provider: SessionPluginProvider,
        requested: Sequence[ResolvedPluginDefinition] = (),
    ) -> None:
        """Install the plugin seam and the set every new session pins.

        The request is separate from the provider on purpose: a provider that
        authored its own request could quietly pin something the caller never
        asked for, and there would be nothing to audit it against.
        """
        self._plugin_provider = provider
        self._requested_plugins = tuple(requested)

    def implemented_procedures(self) -> tuple[Procedure, ...]:
        """The Session procedures this Host serves, for ``app_server/info``.

        The plugin namespace is advertised only once a provider is installed.
        Without one there is nothing to read and nothing to reload, and a
        Client that saw the procedures advertised would reasonably show a
        plugin surface that answers with an empty catalogue forever.
        """
        if self._plugin_provider is None:
            return APP_SERVER_SESSION_IMPLEMENTED_PROCEDURES
        return (
            *APP_SERVER_SESSION_IMPLEMENTED_PROCEDURES,
            *APP_SERVER_SESSION_PLUGIN_PROCEDURES,
        )

    def configure_hook_handlers(self, hook_handlers: HookHandlers) -> None:
        """Register the Host-wide Runtime hook bodies, keyed by binding ID.

        Isolation is per session: a session runs a hook only when its Core carries the
        matching binding (see ``start`` and ``_runtime_config``). Handlers for bindings a
        session does not have are never consulted, so the registry can be Host-global.
        """
        self._hook_handlers = hook_handlers

    def configure_provided_tool_executor(
        self,
        group_names: Sequence[str],
        factory: ProvidedToolExecutorFactory,
        *,
        mode: ProvidedToolApproval | None = None,
    ) -> None:
        """Route an embedder's own provided-tool groups to its own executor.

        Registered groups are routed ahead of the connector and MCP executors, and
        get an executor even in a session that has neither. The factory runs once per
        session; registering a name twice replaces the earlier entry. ``mode`` pins
        these groups' approval bar, overriding ``config.provided_tool_mode``; pass a
        mapping keyed by tool name when one group's tools do not share a bar.
        """
        group = _ProvidedToolGroup(factory=factory, mode=mode)
        for name in group_names:
            self._provided_tool_groups[name] = group

    def configure_mcp(
        self,
        catalog: ResolvedMCPCatalog,
        authorization_provider: MCPAuthorizationProvider,
        *,
        cache_root: str | os.PathLike[str] | None = None,
        cache_policy: MCPDescriptorCachePolicy | None = None,
        http_transport_policy: MCPHTTPTransportPolicy | None = None,
        sampling_completion: MCPSamplingCompletion | None = None,
        transport_factory: MCPTransportFactory | None = None,
    ) -> None:
        self._mcp_catalog = catalog
        self._mcp_authorization_provider = authorization_provider
        if cache_root is not None:
            self._mcp_cache_root = Path(cache_root).expanduser().resolve()
        if cache_policy is not None:
            self._mcp_cache_policy = cache_policy
        if http_transport_policy is not None:
            self._mcp_http_transport_policy = http_transport_policy
        self._retire_stdio_pool_if_stale(sampling_completion, transport_factory)
        self._mcp_sampling_completion = sampling_completion
        self._mcp_transport_factory = transport_factory
        self._refresh_subagent_configuration()

    def configure_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        gateway_factory: Callable[[], ConnectorGateway],
        *,
        gateway_authority_digest: str | None = None,
    ) -> None:
        self._connector_catalog = catalog
        self._connector_selection = selection
        self._connector_gateway_factory = gateway_factory
        self._connector_gateway_authority_digest = gateway_authority_digest
        self._refresh_subagent_configuration()

    def _resolve_subagents(
        self, config: RustHarnessConfig, adapter: LocalRuntimeAdapterConfig
    ) -> ResolvedSubagentConfiguration:
        """The bindings and the root config they were resolved under.

        Returned as one value because they are only valid together: a config
        built from one ceiling will not open bindings resolved beneath another.
        """
        return resolve_subagent_configuration(
            config,
            adapter,
            materialization_root=self._storage_root / "capabilities",
            mcp_catalog=self._mcp_catalog,
            connector_catalog=self._connector_catalog,
            connector_selection=self._connector_selection,
            connector_gateway_authority_digest=self._connector_gateway_authority_digest,
        )

    def _refresh_subagent_configuration(self) -> None:
        config = self._configured_runtime_config
        adapter = self._configured_adapter_config
        if config is None:
            return
        if adapter is None or not isinstance(
            config.settings.tools.subagents, RustEnabledRuntimeToolFeature
        ):
            self._subagent_configuration = None
            self._runtime_config_template = config
            return
        resolved = self._resolve_subagents(config, adapter)
        self._subagent_configuration = resolved
        self._runtime_config_template = resolved.root_config

    async def start(
        self,
        params: SessionStartParams,
        *,
        cwd: str | None = None,
        ephemeral: bool = False,
        initial_public_history: Sequence[JsonObject] = (),
        hook_bindings: Sequence[RustHarnessHookBinding] = (),
        hook_handlers: HookHandlers | None = None,
    ) -> UnifiedHarnessSessionBackend:
        started_at = time.perf_counter()
        self._guard_open()
        self._bind_loop()
        session_id = generate_session_id()
        lease = await asyncio.to_thread(SessionLease(self._storage_root, session_id).acquire)
        session: UnifiedHarnessSessionBackend | None = None
        metadata = SessionMetadataV1(
            cwd=str(Path(cwd or Path.cwd()).expanduser().resolve()),
            root_session_id=session_id,
            hook_bindings=list(hook_bindings),
        )
        try:
            # Pin before either create path so an ephemeral session promotes with
            # the same plugin set as a durable session.
            plugins = await self._pin_plugins(session_id)
            if ephemeral:
                session = await asyncio.to_thread(
                    self._create_ephemeral,
                    session_id,
                    metadata,
                    lease,
                    plugins,
                    hook_handlers,
                    initial_public_history,
                )
                generation = None
            else:
                session, stored = await asyncio.to_thread(
                    self._create, session_id, metadata, lease, plugins, hook_handlers
                )
                generation = stored.manifest.generation
            await self._initialize_integrations(session)
            if not session.ephemeral:
                await self._initialize_subagents(session, plugins)
        except BaseException as exc:
            record_session_operation(
                time.perf_counter() - started_at,
                operation="open",
                outcome="failure",
            )
            logger.warning(
                "Unified session open failed",
                extra={
                    "harness_backend": "unified",
                    "session_id": session_id,
                    "store_format": "mistral.vibe.unified-session-store/v1",
                    "failure_code": getattr(exc, "code", type(exc).__name__),
                },
                exc_info=exc,
            )
            if session is not None:
                await self._close_session(session)
            else:
                # The set is bound and no session owns it yet.
                await self._release_bound_plugins(session_id)
                await asyncio.to_thread(lease.release)
            raise
        attachment = await self._register(session)
        record_session_operation(
            time.perf_counter() - started_at,
            operation="open",
            outcome="success",
        )
        logger.info(
            "Unified session opened",
            extra={
                "harness_backend": "unified",
                "session_id": session_id,
                "store_format": "mistral.vibe.unified-session-store/v1",
                "generation": generation,
            },
        )
        return attachment

    async def resume(
        self,
        session_id: str,
        *,
        history_limit: int,
        hook_bindings: Sequence[RustHarnessHookBinding] | None = None,
        hook_handlers: HookHandlers | None = None,
    ) -> UnifiedHarnessSessionBackend:
        started_at = time.perf_counter()
        self._guard_open()
        self._bind_loop()
        requested_session_id = session_id
        resolved_unified_id = self._resolve_unified_session_id(session_id)
        legacy_reference = None
        if resolved_unified_id is None and self._legacy_source_resolver is not None:
            legacy_reference = self._legacy_source_resolver(session_id)
        legacy_session_id = (
            legacy_reference.session_id if legacy_reference is not None else session_id
        )
        session_id = (
            resolved_unified_id
            or self._resolve_legacy_import_id(legacy_session_id)
            or legacy_session_id
        )
        while True:
            if attachment := await self._attach_live_session(session_id):
                if not attachment.ephemeral and isinstance(
                    attachment._runtime_for_host().identity,
                    SubagentSessionIdentity,
                ):
                    await attachment.shutdown()
                    raise HarnessChildSessionRequiresParentError(session_id, "resumed")
                adapter_config = self._adapter_config()
                if adapter_config is not None:
                    try:
                        attachment.apply_adapter_config(adapter_config)
                    except BaseException:
                        await attachment.shutdown()
                        raise
                return attachment
            await self._wait_for_maintenance(session_id)
            store = UnifiedSessionStore(self._storage_root, session_id)
            load: _LoadingSession | None = None
            if store.exists:
                owns_load, load = await self._claim_session_load(session_id)
                if not owns_load:
                    loaded_session_id, error = await asyncio.shield(load.completed)
                    if error is not None:
                        raise error
                    session_id = loaded_session_id or session_id
                    continue
            try:
                lease = await self._acquire_session_lease(session_id)
            except BaseException as exc:
                if load is not None:
                    await self._finish_session_load(session_id, error=exc)
                raise
            try:
                if store.exists:
                    break
                imported_session_id = self._resolve_legacy_import_id(legacy_session_id)
            except BaseException as exc:
                await asyncio.to_thread(lease.release)
                if load is not None:
                    await self._finish_session_load(session_id, error=exc)
                raise
            if imported_session_id is None:
                break
            await asyncio.to_thread(lease.release)
            if load is not None:
                await self._finish_session_load(session_id, loaded_session_id=imported_session_id)
            session_id = imported_session_id
        operation: Literal["restore", "import"] = "restore"
        source_backend: Literal["legacy", "unified"] = "unified"
        session: UnifiedHarnessSessionBackend | None = None
        try:
            if not store.exists:
                operation = "import"
                source_backend = "legacy"
                session, stored = await asyncio.to_thread(
                    self._import_legacy,
                    session_id,
                    legacy_reference,
                    hook_bindings,
                    hook_handlers,
                )
                await asyncio.to_thread(lease.release)
                session_id = session.session_id
            else:
                stored = await asyncio.to_thread(store.load)
                if isinstance(stored.runtime_state.identity, SubagentSessionIdentity):
                    raise HarnessChildSessionRequiresParentError(session_id, "resumed")
                if isinstance(stored.runtime_state.lifecycle, DeletingSessionTree):
                    raise HarnessSessionDeleteError(
                        session_id, "Session tree deletion is incomplete"
                    )
                plugins = await self._restore_plugins(session_id, stored.runtime_state.plugin_lock)
                try:
                    stored = await asyncio.to_thread(
                        self._preflight_restore, store, stored, plugins
                    )
                    if hook_bindings is not None:
                        stored = await asyncio.to_thread(
                            self._rebind_hook_bindings,
                            store,
                            stored,
                            list(hook_bindings),
                        )
                    session = await asyncio.to_thread(
                        self._bind, stored, lease, plugins, hook_handlers
                    )
                except BaseException:
                    await self._release_bound_plugins(session_id)
                    raise
                await self._initialize_subagents(session, plugins)
                # Ready the integration routes before recovery so a fresh MCP or
                # connector call in the continued turn can dispatch, but defer the
                # Core capability reconfigure: Core rejects reconfigure while the
                # in-flight turn is running. Publish the capabilities once recovery
                # leaves Core idle.
                await self._initialize_integrations(session, push_to_core=False)
                await session.recover()
                await self._push_integration_capabilities(session)
                stored = await asyncio.to_thread(store.load)
            if operation == "import":
                await self._initialize_integrations(session)
                await self._initialize_subagents(session)
        except BaseException as exc:
            failure_code = getattr(exc, "code", type(exc).__name__)
            add_recovery_failure(
                failure_code=failure_code,
                phase=operation,
            )
            record_session_operation(
                time.perf_counter() - started_at,
                operation=operation,
                outcome="failure",
                source_backend=source_backend,
            )
            logger.warning(
                "Unified session restore failed",
                extra={
                    "harness_backend": "unified",
                    "session_id": session_id,
                    "requested_session_id": requested_session_id,
                    "store_format": "mistral.vibe.unified-session-store/v1",
                    "restore_phase": operation,
                    "source_backend": source_backend,
                    "import_outcome": "failure" if operation == "import" else "not_applicable",
                    "failure_code": failure_code,
                },
                exc_info=exc,
            )
            if session is not None:
                try:
                    await self._close_session(session)
                except BaseException as cleanup_error:
                    exc.add_note(
                        f"Unified session cleanup also failed with {type(cleanup_error).__name__}"
                    )
            await asyncio.to_thread(lease.release)
            if load is not None:
                await self._finish_session_load(session_id, error=exc)
            raise
        attachment = await self._register(session)
        if load is not None:
            await self._finish_session_load(session_id, loaded_session_id=session_id)
        record_session_operation(
            time.perf_counter() - started_at,
            operation=operation,
            outcome="success",
            source_backend=source_backend,
        )
        logger.info(
            "Unified session restored",
            extra={
                "harness_backend": "unified",
                "session_id": session_id,
                "requested_session_id": requested_session_id,
                "store_format": "mistral.vibe.unified-session-store/v1",
                "generation": stored.manifest.generation,
                "restore_phase": operation,
                "import_outcome": "success" if operation == "import" else "not_applicable",
            },
        )
        if operation == "restore":
            self._schedule_orphan_diagnostic(stored.runtime_state)
        return attachment

    async def continue_latest(
        self,
        *,
        history_limit: int,
        hook_bindings: Sequence[RustHarnessHookBinding] | None = None,
        hook_handlers: HookHandlers | None = None,
    ) -> UnifiedHarnessSessionBackend:
        self._guard_open()
        sessions = await self.list(limit=1)
        if sessions.continue_session_id is None:
            raise HarnessSessionNotFoundError("latest")
        return await self.resume(
            sessions.continue_session_id,
            history_limit=history_limit,
            hook_bindings=hook_bindings,
            hook_handlers=hook_handlers,
        )

    async def fork(
        self,
        source_session_id: str,
        *,
        entry_id: str | None = None,
        include_entry: bool = True,
        history_limit: int,
        hook_handlers: HookHandlers | None = None,
    ) -> HarnessSessionForkResult:
        """Copy a session into a new one, optionally cut at a user entry.

        ``include_entry=False`` cuts the copy where ``rewind`` would: the anchor
        turn is dropped rather than kept, so the fork starts from the state the
        user was in before sending that message.
        """
        self._guard_open()
        self._bind_loop()
        source_session_id = self._resolve_unified_session_id(source_session_id) or source_session_id
        source_session = self._live_session(source_session_id)
        if source_session is not None:
            await source_session._wait_for_pending_turns()
            unsaved = source_session._unsaved_fork_identity()
            if unsaved is not None:
                # Unused live sessions have an identity but no CURRENT pointer.
                # Copy the in-memory session instead of loading a store that does
                # not exist yet.
                metadata, plugin_lock = unsaved
                return await self._fork_unsaved_live_session(
                    source_session_id,
                    metadata=metadata,
                    plugin_lock=plugin_lock,
                    hook_handlers=hook_handlers,
                )
        source = (
            await asyncio.to_thread(UnifiedSessionStore(self._storage_root, source_session_id).load)
            if source_session is not None
            else None
        )
        temporary_lease: SessionLease | None = None
        if source is None:
            temporary_lease = await self._acquire_session_lease(source_session_id)
            try:
                source = await asyncio.to_thread(
                    UnifiedSessionStore(self._storage_root, source_session_id).load
                )
            except BaseException:
                await asyncio.to_thread(temporary_lease.release)
                raise
        try:
            if isinstance(source.runtime_state.identity, SubagentSessionIdentity):
                raise HarnessChildSessionRequiresParentError(source_session_id, "forked")
            history = _history_for_fork(
                self._importable_history(source), entry_id, include_entry=include_entry
            )
            source_export = source.interop_export
            if source_export is None:
                raise RuntimeError("quiescent source has no interop export")
            session_id = generate_session_id()
            target_lease = await asyncio.to_thread(
                SessionLease(self._storage_root, session_id).acquire
            )
            provenance_source = UnifiedInteropSourceV1(
                session_id=source_session_id,
                generation=source.manifest.generation,
                snapshot_sequence=source.manifest.snapshot_sequence,
            )
            provenance = ImportProvenanceV1(
                source=provenance_source,
                history_sha256=history_fingerprint(history),
                imported_at=_timestamp(),
            )
            source_metadata = source.runtime_state.session_metadata
            target_metadata = SessionMetadataV1(
                cwd=source_metadata.cwd,
                root_session_id=source_metadata.root_session_id,
                parent_session_id=source_session_id,
                hook_bindings=source_metadata.hook_bindings,
            ).model_copy(update=source_metadata.pin_values())
            # Verbatim: re-pinning would give the child a plugin environment
            # its inherited transcript was never recorded against.
            inherited = source.runtime_state.plugin_lock
            plugins = await self._restore_plugins(session_id, inherited)
            try:
                stored = await asyncio.to_thread(
                    self._write_initial_store,
                    session_id,
                    history,
                    provenance,
                    target_metadata,
                    plugins=plugins,
                )
                session = await asyncio.to_thread(
                    self._bind, stored, target_lease, plugins, hook_handlers
                )
                await self._initialize_integrations(session)
                await self._initialize_subagents(session, plugins)
            except BaseException:
                await self._release_bound_plugins(session_id)
                await asyncio.to_thread(target_lease.release)
                raise
            attachment = await self._register(session)
            return HarnessSessionForkResult(source_session_id=source_session_id, session=attachment)
        finally:
            if temporary_lease is not None:
                await asyncio.to_thread(temporary_lease.release)

    async def rewind(self, session_id: str, entry_id: str) -> UnifiedHarnessSessionBackend:
        self._guard_open()
        session_id = self._resolve_unified_session_id(session_id) or session_id
        session = self._live_session(session_id)
        if session is None:
            raise HarnessSessionNotFoundError(session_id)
        await session._wait_for_pending_turns()
        runtime = session._runtime_for_host()
        stored = await asyncio.to_thread(UnifiedSessionStore(self._storage_root, session_id).load)
        if isinstance(stored.runtime_state.identity, SubagentSessionIdentity):
            raise HarnessChildSessionRequiresParentError(session_id, "rewound")
        history = self._importable_history(stored)
        anchor = _history_user_anchor(history, entry_id)
        rewound_history = history[:anchor]
        public = runtime.projection.model_copy(deep=True)
        public_anchor = next(
            (
                index
                for index, entry in enumerate(public.history.entries)
                if entry.get("id") == entry_id
            ),
            None,
        )
        if public_anchor is None:
            raise ValueError(f"Cannot rewind from unknown user entry: {entry_id}")
        now = _now_milliseconds()
        entries = list(public.history.entries[:public_anchor])
        entries.append(
            {
                "type": "checkpoint",
                "id": f"checkpoint-rewind-{entry_id}",
                "sessionId": session_id,
                "turnId": None,
                "createdAt": now,
                "updatedAt": now,
                "generationStatus": "completed",
                "relatedEntryId": None,
                "kind": "rewind",
                "message": "Conversation rewound",
                "details": {"entryId": entry_id, "restoreFiles": False, "inplace": True},
            }
        )
        projection = public.model_copy(
            update={
                "session": public.session.model_copy(
                    update={"status": IdleSessionStatus(), "updated_at": now}
                ),
                "history": public.history.model_copy(update={"entries": entries}),
                "latest_turn": None,
            }
        )
        checkpoint = _create_checkpoint(
            session_id,
            rewound_history,
            runtime.config,
            attachments_root=self._attachments_root(session_id),
            keep_interop_metadata=True,
        )
        await runtime.replace_quiescent_context(
            checkpoint=checkpoint,
            projection=projection,
        )
        return session

    async def _fork_unsaved_live_session(
        self,
        source_session_id: str,
        *,
        metadata: SessionMetadataV1,
        plugin_lock: PluginLockV1,
        hook_handlers: HookHandlers | None,
    ) -> HarnessSessionForkResult:
        session_id = generate_session_id()
        target_lease = await asyncio.to_thread(SessionLease(self._storage_root, session_id).acquire)
        target_metadata = SessionMetadataV1(
            cwd=metadata.cwd,
            root_session_id=metadata.root_session_id,
            parent_session_id=source_session_id,
            hook_bindings=metadata.hook_bindings,
        ).model_copy(update=metadata.pin_values())
        plugins = await self._restore_plugins(session_id, plugin_lock)
        try:
            session = await asyncio.to_thread(
                self._create_ephemeral,
                session_id,
                target_metadata,
                target_lease,
                plugins,
                hook_handlers,
            )
            await self._initialize_integrations(session)
        except BaseException:
            await self._release_bound_plugins(session_id)
            await asyncio.to_thread(target_lease.release)
            raise
        attachment = await self._register(session)
        return HarnessSessionForkResult(source_session_id=source_session_id, session=attachment)

    async def rewrite_session_plugins(
        self, session_id: str, requested: Sequence[ResolvedPluginDefinition]
    ) -> SessionPluginBinding:
        """Re-pin a live, idle session onto a new plugin request.

        The `config/write` half of pinning, in three ordered steps: the session
        refuses a re-pin it cannot accept, then ingest, checkout and bind run
        against the new request, then a set that survived all three is written
        into a new generation. Refusing first matters because preparing binds
        the new set live — a rejection afterwards would leave the session
        running plugins its lock does not name. What the guard cannot cover is
        a turn that starts while the new set is being built; the apply half
        rejects that too, and the previous lock is re-bound before it raises.
        """
        self._guard_open()
        session = self._live_session(session_id)
        if session is None:
            raise HarnessSessionNotFoundError(session_id)
        binder = self._plugin_binder()
        if binder is None:
            if not requested:
                return empty_plugin_binding()
            raise PluginRestoreError(
                [
                    PluginRestoreDiagnostic(
                        code=PluginRestoreDiagnosticCode.LOCK_INVALID,
                        plugin_name=definition.name,
                        content_digest=definition.content_digest,
                        message="the config requests plugins but no plugin provider is configured",
                    )
                    for definition in requested
                ]
            )
        previous_lock = await session._guard_plugin_rewrite()  # noqa: SLF001
        plugins = await binder.create(requested, session_id=session_id)
        try:
            await session._rewrite_plugins(  # noqa: SLF001
                plugins=plugins,
                config=self._runtime_config(session_id, plugins=plugins),
                subagents=self._session_subagents(plugins),
            )
        except BaseException:
            # The new set is already live and the old lock is still recorded;
            # releasing would leave the session plugin-less, so put back what
            # the lock names instead.
            await self._rebind_recorded_plugins(session_id, previous_lock)
            raise
        return plugins

    async def list(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        cwd: str | None = None,
        root_session_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> HarnessSessionListResult:
        self._guard_open()
        return await asyncio.to_thread(
            self._list,
            limit,
            cursor,
            cwd,
            root_session_id,
            parent_session_id,
            self._ephemeral_session_ids(),
            self._live_listed_sessions(),
        )

    async def read(self, params: SessionReadParams) -> HarnessSessionReadResult:
        if session := self._live_session(params.session_id):
            result = await session.read(params)
            return HarnessSessionReadResult(
                snapshot=result.snapshot,
                cwd=session.cwd,
            )
        store = UnifiedSessionStore(self._storage_root, params.session_id)
        if not store.exists:
            raise HarnessSessionNotFoundError(params.session_id)
        stored = await asyncio.to_thread(store.load)
        # No live runtime here, and if no process holds the lease the session is
        # not executing anywhere: settle its stalled projection.
        snapshot = stored.projection_state.snapshot
        if not await asyncio.to_thread(session_is_live, self._storage_root, params.session_id):
            snapshot = settle_stalled_projection(snapshot, observed_at=_now_milliseconds())
        projection = stored.projection_state.model_copy(update={"snapshot": snapshot})
        return HarnessSessionReadResult(
            snapshot=_snapshot(projection, params.history_limit),
            cwd=stored.runtime_state.session_metadata.cwd,
        )

    async def rename(self, session_id: str, title: str) -> SessionSnapshot:
        self._guard_open()
        normalized = title.strip()
        if not normalized:
            raise ValueError("Session title cannot be empty.")
        if session := self._live_session(session_id):
            return await session.rename(normalized)

        store = UnifiedSessionStore(self._storage_root, session_id)
        if not store.exists:
            raise HarnessSessionNotFoundError(session_id)
        lease = await asyncio.to_thread(SessionLease(self._storage_root, session_id).acquire)
        try:
            stored = await asyncio.to_thread(store.load)
            observed_at = _now_milliseconds()
            prior = stored.projection_state.snapshot
            state = renamed_session_state(
                prior,
                normalized,
                observed_at=observed_at,
            )
            # Offline rename bypasses SessionProjector, so it builds its own
            # delta (a single envelope change, entries untouched) to keep the
            # journal on the delta path.
            await asyncio.to_thread(
                store.advance_projection_delta,
                stored.projection_state.watermark + 1,
                compute_projection_delta(prior, state),
            )
            refreshed = await asyncio.to_thread(store.load)
            return _snapshot(refreshed.projection_state, 0)
        finally:
            await asyncio.to_thread(lease.release)

    def references_child(self, root_session_id: str, child_session_id: str) -> bool:
        root = self._live_session(root_session_id)
        if root is None or root.ephemeral:
            return False
        subagents = root._runtime_for_host().runtime_state.subagents
        return subagents is not None and any(
            child.child_session_id == child_session_id for child in subagents.children.values()
        )

    def open_callbacks(self, root_session_id: str) -> tuple[JsonObject, ...]:
        callbacks: list[JsonObject] = []
        for entry in self._sessions.values():
            session = entry.session
            if session.session_id == root_session_id:
                callbacks.extend(session.open_callbacks())
                continue
            if session.ephemeral:
                continue
            identity = session._runtime_for_host().identity
            if (
                isinstance(identity, SubagentSessionIdentity)
                and identity.parent_session_id == root_session_id
            ):
                callbacks.extend(session.open_callbacks())
        return tuple(callbacks)

    async def respond_to_callback(self, root_session_id: str, params: object) -> object:
        session_id = getattr(params, "session_id", None)
        if not isinstance(session_id, str):
            raise TypeError("callback result requires a session_id")
        session = self._live_session(session_id)
        if session is None:
            raise HarnessSessionNotFoundError(session_id)
        if session_id != root_session_id:
            if session.ephemeral:
                raise HarnessSessionNotFoundError(session_id)
            identity = session._runtime_for_host().identity
            if not (
                isinstance(identity, SubagentSessionIdentity)
                and identity.parent_session_id == root_session_id
            ):
                raise HarnessSessionNotFoundError(session_id)
        return await session.respond_to_callback(params)

    async def session_cwd(self, session_id: str) -> str | None:
        """Best-effort stored cwd of a session, resolved without resuming it.

        A session runs in the cwd it was created with (the Core uses ``metadata.cwd``
        for tools and bash), so its hooks must be discovered and compiled against that
        same cwd -- not the caller's invocation cwd. The delivery adapter needs the
        stored cwd before it builds the resume/continue/fork context, so it calls this
        first. Returns None for an unknown session; the caller then falls back to the
        request cwd.
        """
        self._guard_open()
        resolved = self._resolve_unified_session_id(session_id) or session_id
        if session := self._live_session(resolved):
            return session.cwd
        entry = await asyncio.to_thread(self._catalog_entry, resolved)
        if entry is not None:
            return entry.metadata.cwd
        if self._legacy_source_resolver is not None:
            reference = self._legacy_source_resolver(session_id)
            if reference is not None:
                return reference.cwd or None
        return None

    async def session_pin(self, session_id: str, pin: SessionPin) -> str | None:
        """Best-effort value stored for a session pin (see ``SessionPin``)."""
        self._guard_open()
        resolved = self._resolve_unified_session_id(session_id) or session_id
        if session := self._live_session(resolved):
            return session.pin(pin)
        entry = await asyncio.to_thread(self._catalog_entry, resolved)
        if entry is not None:
            return entry.metadata.pin(pin)
        if self._legacy_source_loader is not None:
            source = self._legacy_source_loader(session_id)
            if source.state == "quiescent":
                return source.pin(pin)
        return None

    async def create_or_restore_child(
        self,
        *,
        identity: SubagentSessionIdentity,
        binding: ResolvedChildSessionBinding,
        spawn_key: str,
        start_new_actions: bool,
        require_existing: bool,
    ) -> ChildSessionHandle:
        """Bind one parent-owned child while retaining its exclusive store lease."""

        self._guard_open()
        core_config, adapter_config = child_config(binding, identity.session_id)
        if not isinstance(binding, LocalChildSessionBinding):
            raise TypeError("local child Host requires a local child binding")
        hook_handlers = self._child_hook_handlers(identity, core_config)
        async with self._child_lock:
            live = self._live_session(identity.session_id)
            if live is not None:
                _validate_child_runtime_state(
                    live._runtime_for_host().runtime_state,
                    identity=identity,
                    binding=binding,
                    spawn_key=spawn_key,
                )
                await live._restore_child_execution(start_new_actions=start_new_actions)
                return ChildSessionHandle(session_id=identity.session_id)

            lease = await self._acquire_session_lease(identity.session_id)
            session: UnifiedHarnessSessionBackend | None = None
            plugins = empty_plugin_binding()
            try:
                store = UnifiedSessionStore(self._storage_root, identity.session_id)
                if store.exists:
                    stored = await asyncio.to_thread(store.load)
                    _validate_child_runtime_state(
                        stored.runtime_state,
                        identity=identity,
                        binding=binding,
                        spawn_key=spawn_key,
                    )
                    plugins = await self._restore_plugins(
                        identity.session_id, stored.runtime_state.plugin_lock
                    )
                    core_config = core_config.model_copy(
                        update={"plugins": list(plugins.definitions)}, deep=True
                    )
                    restored_core = await asyncio.to_thread(stored.restore_core, core_config)
                    restored_core.close()
                else:
                    if require_existing:
                        raise RuntimeError(
                            f"durable child Session store is missing: {identity.session_id}"
                        )
                    if binding.integrations_enabled:
                        parent = self._live_session(identity.parent_session_id)
                        if parent is None:
                            raise HarnessSessionNotFoundError(identity.parent_session_id)
                        plugins = await self._restore_plugins(
                            identity.session_id,
                            parent._runtime_for_host().runtime_state.plugin_lock,
                        )
                        core_config = core_config.model_copy(
                            update={"plugins": list(plugins.definitions)}, deep=True
                        )
                    metadata = SessionMetadataV1(
                        cwd=str(adapter_config.cwd.expanduser().resolve()),
                        root_session_id=identity.root_session_id,
                        parent_session_id=identity.parent_session_id,
                        subagent_spawn_key=spawn_key,
                        subagent_template_digest=binding.template_digest,
                        subagent_policy_ceiling_digest=binding.policy_ceiling_digest,
                        hook_bindings=list(core_config.capabilities.hook_bindings),
                    )
                    stored = await asyncio.to_thread(
                        self._write_initial_store,
                        identity.session_id,
                        [],
                        None,
                        metadata,
                        config=core_config,
                        identity=identity,
                        plugins=plugins,
                    )
                session = await asyncio.to_thread(
                    self._bind,
                    stored,
                    lease,
                    plugins,
                    hook_handlers,
                    runtime_config_override=core_config,
                    adapter_config_override=adapter_config,
                    integrations_enabled=binding.integrations_enabled,
                )
                if binding.integrations_enabled:
                    # Ready the routes so a continued turn can dispatch them, but
                    # leave Core alone: settling the child adopts the binding's own
                    # capability set wholesale, and that set is the base the routes
                    # compose onto, so a push that goes first is rolled straight
                    # back off. Publish once the settle leaves Core on that base,
                    # the way resume does for a root.
                    await self._initialize_integrations(session, push_to_core=False)
                await self._register(session)
                await session._restore_child_execution(
                    start_new_actions=start_new_actions,
                    once_idle=(
                        partial(self._push_integration_capabilities, session)
                        if binding.integrations_enabled
                        else None
                    ),
                )
            except BaseException:
                if session is not None:
                    await self._close_session(session)
                else:
                    await self._release_bound_plugins(identity.session_id)
                    await asyncio.to_thread(lease.release)
                raise
            return ChildSessionHandle(session_id=identity.session_id)

    def _child_hook_handlers(
        self, identity: SubagentSessionIdentity, core_config: RustHarnessConfig
    ) -> HookHandlers | None:
        # ``_child_core_config`` copies the parent's capability set and clears only
        # ``agent_types``, so the child's Core carries the parent's ``hook_bindings``
        # verbatim while their handlers live on the parent Session. An unresolvable
        # binding is skipped, not fatal (``_resolve_hooks``), so without this the guards
        # would silently stop guarding the moment the model delegates.
        if not core_config.capabilities.hook_bindings:
            return None
        parent = self._live_session(identity.parent_session_id)
        if parent is None:
            raise HarnessSessionNotFoundError(identity.parent_session_id)
        return parent.foreign_hook_handlers

    async def start_child_turn(
        self,
        child: ChildSessionHandle,
        *,
        message: str,
        target: SpawnTarget,
        operation_key: str,
    ) -> ChildCommandAdmission:
        session = self._require_live_child(child)
        identity = session._runtime_for_host().identity
        if not isinstance(identity, SubagentSessionIdentity):
            raise ValueError("parent command targeted a non-subagent Session")
        await session._start_parent_turn(
            parent_session_id=identity.parent_session_id,
            message=message,
            target=target,
            operation_key=operation_key,
        )
        return ChildCommandAdmission(target=target, turn_id=target.command.turn_id)

    async def send_child_message(
        self,
        child: ChildSessionHandle,
        *,
        message: str,
        known_generation: ChildGenerationRef,
        operation_key: str,
    ) -> ChildCommandAdmission:
        session = self._require_live_child(child)
        identity = session._runtime_for_host().identity
        if not isinstance(identity, SubagentSessionIdentity):
            raise ValueError("parent command targeted a non-subagent Session")
        target = await session._send_parent_message(
            parent_session_id=identity.parent_session_id,
            message=message,
            known_generation=known_generation,
            operation_key=operation_key,
        )
        return ChildCommandAdmission(target=target, turn_id=target.command.turn_id)

    async def interrupt_child(
        self,
        child: ChildSessionHandle,
        *,
        target: InterruptTarget | CloseRunningTarget,
        operation_key: str,
    ) -> ChildCommandAdmission:
        session = self._require_live_child(child)
        identity = session._runtime_for_host().identity
        if not isinstance(identity, SubagentSessionIdentity):
            raise ValueError("parent command targeted a non-subagent Session")
        await session._interrupt_parent_turn(
            parent_session_id=identity.parent_session_id,
            target=target,
            operation_key=operation_key,
        )
        return ChildCommandAdmission(target=target, turn_id=target.command.turn_id)

    async def wait_for_child_generation(
        self,
        child: ChildSessionHandle,
        generation: int,
        timeout_ms: int,
    ) -> ChildTurnOutcome:
        session = self._require_live_child(child)
        turn_id = f"{child.session_id}:turn:{generation}"
        state = session._state_for_host()
        if state.latest_turn is None or state.latest_turn.id != turn_id:
            raise RuntimeError("child Session does not retain the requested generation")
        if state.latest_turn.status == "in_progress":
            if timeout_ms <= 0:
                raise TimeoutError
            state = await asyncio.wait_for(
                session._wait_for_parent_turn(turn_id),
                timeout=timeout_ms / 1000,
            )
        return _child_turn_outcome(
            state,
            generation,
            turn_id,
            retryable_failure=_terminal_failure_retryability(
                UnifiedSessionStore(self._storage_root, child.session_id).load().checkpoint,
                turn_id,
            ),
        )

    async def acknowledge_child_command(
        self,
        child: ChildSessionHandle,
        *,
        operation_key: str,
    ) -> None:
        await self._require_live_child(child)._acknowledge_parent_command(operation_key)

    async def child_command_admission(
        self,
        child_session_id: str,
        *,
        operation_key: str,
    ) -> ChildCommandAdmission | None:
        session = self._live_session(child_session_id)
        state = (
            session._runtime_for_host().runtime_state
            if session is not None
            else await asyncio.to_thread(
                lambda: (
                    UnifiedSessionStore(self._storage_root, child_session_id).load().runtime_state
                )
            )
            if UnifiedSessionStore(self._storage_root, child_session_id).exists
            else None
        )
        if state is None:
            return None
        receipt = state.parent_command_receipts.get(operation_key)
        if receipt is None:
            return None
        return ChildCommandAdmission(
            target=receipt.target,
            turn_id=receipt.result.turn_id,
        )

    async def unload_child(self, child: ChildSessionHandle) -> None:
        async with self._child_lock:
            session = self._live_session(child.session_id)
            if session is not None:
                await self._close_session(session)

    async def delete_child(self, child_session_id: str) -> None:
        async with self._child_lock:
            session = self._live_session(child_session_id)
            if session is not None:
                await self._close_session(session)
            lease = await asyncio.to_thread(
                SessionLease(self._storage_root, child_session_id).acquire
            )
            try:
                await asyncio.to_thread(
                    UnifiedSessionStore(self._storage_root, child_session_id).delete
                )
            finally:
                await asyncio.to_thread(lease.release)

    async def reconfigure_child(
        self,
        child: ChildSessionHandle,
        binding: ResolvedChildSessionBinding,
    ) -> bool:
        """Push a binding's live adapter config into a child session.

        For a live child, applies the adapter config and updates the child
        session's stored metadata digests so ``_validate_child_runtime_state``
        accepts the child on its next interaction.

        For a non-live child (evicted from memory), updates the persisted
        metadata digests directly through storage so the child remains
        consistent with the new binding on restore.

        Returns ``True`` if the child was updated, ``False`` if it could not
        be found (neither live nor in storage).
        """
        from mistralai_vibe_local_harness.vibe._subagents._configuration import child_config

        session = self._live_session(child.session_id)
        if session is not None:
            _, adapter_config = child_config(binding, child.session_id)
            session.apply_adapter_config(adapter_config)
            runtime = session._runtime_for_host()
            await runtime.update_runtime_state(
                lambda state: state.model_copy(
                    update={
                        "session_metadata": state.session_metadata.model_copy(
                            update={
                                "subagent_template_digest": binding.template_digest,
                                "subagent_policy_ceiling_digest": binding.policy_ceiling_digest,
                            }
                        )
                    },
                    deep=True,
                )
            )
            return True
        # Child session is not live: update persisted metadata through storage.
        store = UnifiedSessionStore(self._storage_root, child.session_id)
        if not store.exists:
            return False
        stored = await asyncio.to_thread(store.load)
        updated_state = stored.runtime_state.model_copy(
            update={
                "session_metadata": stored.runtime_state.session_metadata.model_copy(
                    update={
                        "subagent_template_digest": binding.template_digest,
                        "subagent_policy_ceiling_digest": binding.policy_ceiling_digest,
                    }
                )
            },
            deep=True,
        )
        await asyncio.to_thread(
            store.write_generation,
            checkpoint=stored.checkpoint,
            runtime_state=updated_state,
            projection_state=stored.projection_state,
        )
        return True

    async def reconfigure_subagents(
        self,
        session_id: str,
        adapter_config: LocalRuntimeAdapterConfig,
    ) -> None:
        """Refresh the subagent configuration and propagate to children.

        Called when the parent's adapter config changes mid-session (e.g. an
        agent mode switch) so the subagent bindings pick up the new
        ``bypass_approval`` and ``tool_modes``, and already-running children
        are reconfigured too.

        Scoped to the calling session: only that session's controller is
        rebound, and the host-global ``_configured_adapter_config`` is not
        overwritten, so other root sessions are unaffected.
        """
        entry = self._sessions.get(session_id)
        if entry is None:
            return
        runtime = entry.session._runtime_for_host()
        controller = runtime._subagent_controller
        if controller is None:
            return
        # Resolve subagent bindings from the calling session's adapter config,
        # not the host-global one, so other sessions are unaffected.
        config = self._configured_runtime_config
        if config is None:
            return
        resolved = self._resolve_subagents(config, adapter_config)
        # Preserve declared agent types from the session's plugin binding.
        # After promotion the binding is in the runtime's plugin lock; before
        # promotion it is the pending binding on the session.
        plugins = entry.session._pending_plugin_binding
        if plugins is None:
            lock = runtime.runtime_state.plugin_lock
            if lock.plugins:
                plugins = await self._restore_plugins(session_id, lock)
        if plugins is not None:
            resolved = _extend_subagent_bindings(resolved, adapter_config, plugins)
        if resolved is None:
            return
        await controller.update_policy_ceiling(resolved.policy_ceiling)
        await controller.rebind_agent_types(
            resolved.bindings,
            resolved.policy_ceiling,
        )
        await controller.reconfigure_children()

    async def delete(self, session_id: str) -> HarnessSessionDeleteResult:
        """Delete one root and every durable child, with the root removed last."""
        self._guard_open()
        live = self._live_session(session_id)
        if live is not None and live.ephemeral:
            await self._close_session(live)
            return HarnessSessionDeleteResult(
                root_session_id=session_id,
                deleted_session_ids=(session_id,),
            )

        retained = self._tree_deletion_leases.get(session_id)
        attached_root_lease: SessionLease | None = None
        if live is not None:
            identity = live._runtime_for_host().identity
            if isinstance(identity, SubagentSessionIdentity):
                raise HarnessChildSessionRequiresParentError(session_id, "deleted")
            await self._prepare_live_tree_delete(live)
            attached_root_lease = live._detach_lease_for_tree_deletion()
            try:
                await self._close_session(live)
            except BaseException:
                await asyncio.to_thread(attached_root_lease.release)
                raise

        leases = retained
        if leases is None:
            leases = _TreeDeletionLeases(
                root=(
                    attached_root_lease
                    if attached_root_lease is not None
                    else await asyncio.to_thread(
                        SessionLease(self._storage_root, session_id).acquire
                    )
                ),
                children={},
            )
        retain_after_failure = attached_root_lease is not None or retained is not None
        leases_ready = retained is not None
        succeeded = False
        try:
            store = UnifiedSessionStore(self._storage_root, session_id)
            if not store.exists:
                raise HarnessSessionNotFoundError(session_id)
            stored = await asyncio.to_thread(store.load)
            if isinstance(stored.runtime_state.identity, SubagentSessionIdentity):
                raise HarnessChildSessionRequiresParentError(session_id, "deleted")
            stored = await asyncio.to_thread(self._prepare_stored_tree_delete, store, stored)
            lifecycle = stored.runtime_state.lifecycle
            if isinstance(lifecycle, DeletingSessionTree):
                planned = tuple(lifecycle.ordered_child_session_ids)
                remaining = planned[len(lifecycle.deleted_child_session_ids) :]
            elif _tree_child_session_ids(stored.runtime_state):
                raise RuntimeError("root Session did not enter tree deletion")
            else:
                planned, remaining = (), ()
            for child_session_id in remaining:
                if child_session_id in leases.children:
                    continue
                child = self._live_session(child_session_id)
                if child is not None:
                    await self._close_session(child)
                lease = await asyncio.to_thread(
                    SessionLease(self._storage_root, child_session_id).acquire
                )
                leases.children[child_session_id] = lease
            leases_ready = True
            if retain_after_failure:
                self._tree_deletion_leases[session_id] = leases

            for child_session_id in remaining:
                child_store = UnifiedSessionStore(self._storage_root, child_session_id)
                if child_store.exists:
                    child_stored = await asyncio.to_thread(child_store.load)
                    child_identity = child_stored.runtime_state.identity
                    if not (
                        isinstance(child_identity, SubagentSessionIdentity)
                        and child_identity.parent_session_id == session_id
                    ):
                        raise HarnessSessionDeleteError(
                            session_id,
                            f"Child Session ownership mismatch: {child_session_id}",
                        )
                    await asyncio.to_thread(child_store.delete)
                lifecycle = stored.runtime_state.lifecycle
                if not isinstance(lifecycle, DeletingSessionTree):
                    raise RuntimeError("root Session lost its tree-deletion plan")
                updated_lifecycle = lifecycle.model_copy(
                    update={
                        "deleted_child_session_ids": [
                            *lifecycle.deleted_child_session_ids,
                            child_session_id,
                        ]
                    }
                )
                stored = await asyncio.to_thread(
                    self._write_stored_lifecycle,
                    store,
                    stored,
                    updated_lifecycle,
                )

            deleted = (*planned, session_id)
            await asyncio.to_thread(store.delete)
            succeeded = True
            return HarnessSessionDeleteResult(
                root_session_id=session_id,
                deleted_session_ids=deleted,
            )
        except (HarnessChildSessionRequiresParentError, HarnessSessionNotFoundError):
            raise
        except HarnessSessionDeleteError:
            raise
        except BaseException as exc:
            raise HarnessSessionDeleteError(session_id, str(exc)) from exc
        finally:
            if succeeded or not (retain_after_failure and leases_ready):
                self._tree_deletion_leases.pop(session_id, None)
                for child_session_id in reversed(sorted(leases.children)):
                    await asyncio.to_thread(leases.children[child_session_id].release)
                await asyncio.to_thread(leases.root.release)

    async def _prepare_live_tree_delete(self, session: UnifiedHarnessSessionBackend) -> None:
        runtime = session._runtime_for_host()

        def prepare(state: RuntimeStateV3) -> RuntimeStateV3:
            plan = _tree_deletion_plan(state)
            if plan is None:
                return state
            return state.model_copy(update={"lifecycle": plan})

        await runtime.update_runtime_state(prepare)

    @staticmethod
    def _prepare_stored_tree_delete(
        store: UnifiedSessionStore, stored: StoredSession
    ) -> StoredSession:
        plan = _tree_deletion_plan(stored.runtime_state)
        if plan is None:
            return stored
        return UnifiedHarnessSessionBackendHost._write_stored_lifecycle(store, stored, plan)

    @staticmethod
    def _write_stored_lifecycle(
        store: UnifiedSessionStore,
        stored: StoredSession,
        lifecycle: DeletingSessionTree,
    ) -> StoredSession:
        state = stored.runtime_state.model_copy(update={"lifecycle": lifecycle})
        # A stored session loaded with a pending journal carries a runtime
        # sequence advanced past the last projection record, so its runtime and
        # projection snapshot sequences differ. write_generation rejects that
        # pair, so re-baseline the projection onto the runtime sequence the way
        # every other generation write does before folding the journal.
        projection_state = stored.projection_state.model_copy(
            update={"snapshot_sequence": state.snapshot_sequence}
        )
        store.write_generation(
            checkpoint=stored.checkpoint,
            runtime_state=state,
            projection_state=projection_state,
            # No Core is ever restored from a generation written on the way to
            # `rmtree`, so nothing here can be kept. Saying so prunes the ledger,
            # which is what lets an oversized Session be republished at all.
            core_action_ids=frozenset(),
        )
        return store.load()

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        orphan_diagnostic_tasks = tuple(self._orphan_diagnostic_tasks.values())
        self._orphan_diagnostic_tasks.clear()
        for task in orphan_diagnostic_tasks:
            task.cancel()
        if orphan_diagnostic_tasks:
            await asyncio.gather(*orphan_diagnostic_tasks, return_exceptions=True)
        cleanup_task = self._cleanup_task
        if cleanup_task is not None:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
        async with self._registry_lock:
            entries = list(self._sessions.values())
        errors: list[BaseException] = []
        # A root Runtime owns its child shutdown through SubagentController.
        # Closing every registered Session concurrently can therefore enter a
        # child's non-reentrant shutdown both directly and through its parent.
        # Serialize the Host drain so each controller-owned child is fully
        # unloaded before another registered Session is considered.
        for entry in entries:
            try:
                await self._close_session(entry.session)
            except BaseException as exc:
                errors.append(exc)
        async with self._registry_lock:
            remaining = list(self._sessions.values())
            self._sessions.clear()
            for entry in remaining:
                entry.closed.set()
        deletion_leases = list(self._tree_deletion_leases.values())
        self._tree_deletion_leases.clear()
        for leases in deletion_leases:
            for child_session_id in reversed(sorted(leases.children)):
                try:
                    await asyncio.to_thread(leases.children[child_session_id].release)
                except BaseException as exc:
                    errors.append(exc)
            try:
                await asyncio.to_thread(leases.root.release)
            except BaseException as exc:
                errors.append(exc)
        # After the drain: every Session released its lease above, so a surviving process
        # is one no Session accounted for.
        with self._mcp_stdio_pool_lock:
            pools = [*self._retired_mcp_stdio_pools, self._mcp_stdio_pool]
            self._retired_mcp_stdio_pools.clear()
            self._mcp_stdio_pool = None
        for pool in pools:
            if pool is None:
                continue
            try:
                await pool.aclose()
            except BaseException as exc:
                errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close Unified Harness sessions", errors)

    def _create(
        self,
        session_id: str,
        metadata: SessionMetadataV1,
        lease: SessionLease,
        plugins: SessionPluginBinding,
        hook_handlers: HookHandlers | None = None,
    ) -> tuple[UnifiedHarnessSessionBackend, StoredSession]:
        stored = self._write_initial_store(session_id, [], None, metadata, plugins=plugins)
        return self._bind(stored, lease, plugins, hook_handlers), stored

    def _write_initial_store(
        self,
        session_id: str,
        history: "list[InteropHistoryMessageV1]",
        provenance: ImportProvenanceV1 | None,
        metadata: SessionMetadataV1,
        *,
        created_at: int | None = None,
        config: RustHarnessConfig | None = None,
        identity: SessionIdentity | None = None,
        public_state: PublicSessionState | None = None,
        plugins: SessionPluginBinding | None = None,
    ) -> StoredSession:
        created_at = created_at or _now_milliseconds()
        plugins = plugins or empty_plugin_binding()
        checkpoint = _create_checkpoint(
            session_id,
            history,
            config or self._runtime_config(session_id, metadata.hook_bindings, plugins=plugins),
            attachments_root=self._attachments_root(session_id),
        )
        state = public_state or _public_state(session_id, created_at, history, metadata)
        runtime_state = empty_runtime_state(
            session_id,
            snapshot_sequence=0,
            plugin_lock=plugins.lock,
            cwd=metadata.cwd,
            pins=metadata.pin_values(),
            root_session_id=metadata.root_session_id,
            parent_session_id=metadata.parent_session_id,
            hook_bindings=metadata.hook_bindings,
            identity=identity,
            subagent_spawn_key=metadata.subagent_spawn_key,
            subagent_template_digest=metadata.subagent_template_digest,
            subagent_policy_ceiling_digest=metadata.subagent_policy_ceiling_digest,
        ).model_copy(update={"import_provenance": provenance})
        projection_state = ProjectionStateV1(
            session_id=session_id,
            snapshot_sequence=0,
            watermark=0,
            snapshot=state,
        )
        store = UnifiedSessionStore(self._storage_root, session_id)
        store.write_generation(
            checkpoint=checkpoint,
            runtime_state=runtime_state,
            projection_state=projection_state,
        )
        return store.load()

    def _create_ephemeral(
        self,
        session_id: str,
        metadata: SessionMetadataV1,
        lease: SessionLease,
        plugins: SessionPluginBinding,
        hook_handlers: HookHandlers | None = None,
        initial_public_history: Sequence[JsonObject] = (),
    ) -> UnifiedHarnessSessionBackend:
        created_at = _now_milliseconds()
        session: UnifiedHarnessSessionBackend | None = None
        adapter_config = self._adapter_config()
        runtime_config_template = self._runtime_config_template
        subagent_configuration = self._subagent_configuration
        # Captured with the template above, and for the same reason: promote runs
        # later, and an adapter the Host has since replaced carries a different ceiling.
        subagent_adapter = self._configured_adapter_config
        subagent_root_config = self._configured_runtime_config
        # Resolve the handler registry now and bake it into the adapter below:
        # promote() runs later, and a subsequent lifecycle op could replace the
        # Host-global registry before then, leaving this session with handlers that
        # do not match the bindings its Core carries. The session's foreign handlers
        # are merged with the Host-global builtins (see merge_hook_handlers) so a
        # session with user hooks still runs always-bound builtins.
        handlers = (
            merge_hook_handlers(self._hook_handlers, hook_handlers)
            if hook_handlers is not None
            else self._hook_handlers
        )
        # Only foreign hooks surface public notices (see _emits_run_notices).
        foreign_ids = (
            foreign_binding_ids(hook_handlers) if hook_handlers is not None else frozenset()
        )

        def publish_event(event: JsonObject) -> None:
            if session is None:
                raise RuntimeError("Ephemeral session is not bound")
            session._publish_event(event)

        def publish_mcp_event(signal: MCPAuthorizationRequiredSignal) -> None:
            publish_event(
                {
                    "type": "mcp_authorization_required",
                    "serverName": signal.server_name,
                    "reason": signal.reason,
                    "descriptorRevision": signal.descriptor_revision,
                    "observedConnectionRevision": signal.observed_connection_revision,
                }
            )

        def publish_connector_event(
            signal: ConnectorAuthorizationRequiredSignal,
        ) -> None:
            publish_event(
                {
                    "type": "connector_authorization_required",
                    "rawConnectorId": signal.raw_connector_id,
                    "alias": signal.alias,
                    "acceptedCatalogRevision": signal.accepted_catalog_revision,
                    "action": signal.action,
                    "reason": signal.reason,
                }
            )

        mcp_runtime = self._new_mcp_runtime(event_sink=publish_mcp_event)
        connector_runtime = self._new_connector_runtime(event_sink=publish_connector_event)
        mcp_executor = build_mcp_action_executor(mcp_runtime) if mcp_runtime is not None else None
        connector_executor = (
            build_connector_action_executor(connector_runtime)
            if connector_runtime is not None
            else None
        )

        registered_groups = _registered_group_names(
            self._provided_tool_groups, runtime_config_template.capabilities
        )
        registered_executors = _session_provided_tool_executors(
            self._provided_tool_groups, registered_groups, session_id
        )

        async def execute_provided_tool(action: RustProvidedToolCallAction):
            if action.call.group_name == "ui" and action.call.tool_name == "ask_user_question":
                if session is None:
                    raise RuntimeError("Session is not ready for user input")
                return await session._request_user_input(action)
            registered = registered_executors.get(action.call.group_name)
            if registered is not None:
                return await registered(action)
            if connector_executor is not None and action.call.group_name.startswith("connector_"):
                return await connector_executor(action)
            if mcp_executor is not None:
                return await mcp_executor(action)
            if connector_executor is not None:
                return await connector_executor(action)
            raise RuntimeError("No provided-tool executor is configured")

        provided_tool_executor = (
            execute_provided_tool
            if mcp_executor is not None
            or connector_executor is not None
            or registered_executors
            or _has_provided_tool(
                runtime_config_template.capabilities,
                group_name="ui",
                tool_name="ask_user_question",
            )
            else None
        )
        provided_tool_modes = _session_provided_tool_modes(
            self._provided_tool_groups, registered_groups
        )
        # Built before the session so the session can own the handle. An ephemeral
        # session can be re-configured before it is ever promoted, so the adapter has to
        # outlive ``promote`` rather than be built by it. The provided-tool executor
        # (MCP + connectors) and the (builtins-merged) foreign hook handlers are baked in.
        action_adapter = (
            _LocalActionState(
                adapter_config or LocalRuntimeAdapterConfig(),
                None,
                provided_tool_executor,
                handlers,
                session_id,
                filesystem_root=self._session_root(session_id),
                foreign_binding_ids=foreign_ids,
                provided_tool_modes=provided_tool_modes,
            )
            if adapter_config is not None
            or provided_tool_executor is not None
            or has_hook_handlers(handlers)
            else None
        )

        base_capabilities = self._capabilities_with_hooks(metadata.hook_bindings)

        def promote(plugins: SessionPluginBinding) -> DurableSessionRuntime:
            assert session is not None
            nonlocal runtime_config_template, subagent_configuration, subagent_adapter
            # A session can be told to run somewhere else between creation and
            # its first turn, and the ceiling captured above is the one it was
            # created under. Re-resolved as a pair, never singly: the bindings
            # a subagent opens and the config this runtime is built from have
            # to have come from the same adapter.
            moved = session.adapter_config
            if (
                moved is not None
                and moved is not subagent_adapter
                and subagent_root_config is not None
                and subagent_configuration is not None
            ):
                resolved = self._resolve_subagents(
                    session.configuration_over(subagent_root_config), moved
                )
                subagent_configuration = resolved
                subagent_adapter = moved
                runtime_config_template = resolved.root_config
            # Settings and capabilities can be pushed after promotion. System
            # instructions must be present when Core is created.
            system_instructions = session.configuration_over(
                runtime_config_template
            ).system_instructions
            runtime_config = runtime_config_template.model_copy(
                update={
                    "task_id": session_id,
                    "plugins": list(plugins.definitions),
                    "system_instructions": system_instructions,
                },
                deep=True,
            )
            mcp_snapshot = mcp_runtime.snapshot if mcp_runtime is not None else None
            connector_snapshot = (
                connector_runtime.snapshot if connector_runtime is not None else None
            )
            # Fold the session's hook bindings into the base capabilities so a later
            # MCP/connector snapshot reconfigure preserves them (see the durable ``_bind``
            # path). Connectors are NOT folded here: the merge re-adds them from the live
            # snapshot, so a connector-bearing base would double-register a tool group.
            runtime_config = runtime_config.model_copy(
                update={
                    "capabilities": _merge_integration_capabilities(
                        base_capabilities,
                        mcp_snapshot=mcp_snapshot,
                        connector_snapshot=connector_snapshot,
                    )
                },
                deep=True,
            )
            # The plugin sets folded in above were never filtered, and this path does
            # not reach ``_runtime_config``. Without this a promoted session advertises
            # agent types nothing can spawn.
            promoted_subagents = _extend_subagent_bindings(
                subagent_configuration, subagent_adapter, plugins
            )
            runtime_config = advertise_bound_agent_types(
                runtime_config,
                promoted_subagents.bindings if promoted_subagents is not None else {},
            )
            stored = self._write_initial_store(
                session_id,
                [],
                None,
                metadata,
                created_at=created_at,
                config=runtime_config,
                public_state=session._state_for_host(),
                plugins=plugins,
            )
            store = UnifiedSessionStore(self._storage_root, session_id)
            runtime_holder: list[DurableSessionRuntime] = []
            process_manager = self._new_process_manager(
                store,
                adapter_config,
                lambda snapshot: runtime_holder[0].submit_terminal_snapshot(snapshot),
            )
            runtime = DurableSessionRuntime.restore(
                config=runtime_config,
                store=store,
                stored=stored,
                event_sink=publish_event,
                process_manager=process_manager,
                process_config=adapter_config,
                request_process_approval=(
                    session._request_process_approval if process_manager is not None else None
                ),
            )
            runtime_holder.append(runtime)
            if mcp_runtime is not None:

                async def accept_mcp_snapshot(snapshot: MCPRouteSnapshot) -> None:
                    current_connector_snapshot = (
                        connector_runtime.snapshot if connector_runtime is not None else None
                    )
                    await runtime.reconfigure_capability_dimension(
                        _integration_capability_update(
                            base_capabilities,
                            mcp_snapshot=snapshot,
                            connector_snapshot=current_connector_snapshot,
                        )
                    )

                mcp_runtime.bind_snapshot_acceptor(accept_mcp_snapshot)
            if connector_runtime is not None:

                async def accept_connector_snapshot(
                    snapshot: ConnectorRouteSnapshot,
                ) -> None:
                    current_mcp_snapshot = mcp_runtime.snapshot if mcp_runtime is not None else None
                    await runtime.reconfigure_capability_dimension(
                        _integration_capability_update(
                            base_capabilities,
                            mcp_snapshot=current_mcp_snapshot,
                            connector_snapshot=snapshot,
                        )
                    )

                connector_runtime.bind_snapshot_acceptor(accept_connector_snapshot)
            if action_adapter is not None:
                runtime.configure_action_executor(action_adapter.execute)
                action_adapter.bind_notice_sink(runtime.append_public_history_entries)
                action_adapter.bind_classification_sink(publish_event)
                if streams_provisional_content(handlers, metadata.hook_bindings):
                    action_adapter.bind_completion_delta_sink(
                        runtime.append_provisional_completion_content
                    )
            if connector_runtime is not None:
                bound_connector_runtime = connector_runtime

                async def publish_connector_authorization_after_action(
                    action: object, _event: object
                ) -> None:
                    if not isinstance(action, RustProvidedToolCallAction):
                        return
                    await bound_connector_runtime.publish_pending_authorization(
                        group_name=action.call.group_name,
                        tool_name=action.call.tool_name,
                    )

                runtime.configure_action_applied_sink(publish_connector_authorization_after_action)
            return runtime

        async def initialize_subagents_on_promote(
            runtime: DurableSessionRuntime, plugins: SessionPluginBinding
        ) -> None:
            await self._initialize_subagent_runtime(
                runtime,
                _extend_subagent_bindings(subagent_configuration, subagent_adapter, plugins),
            )

        session = UnifiedHarnessSessionBackend(
            session_id,
            created_at,
            cwd=metadata.cwd,
            image_source_roots=self._image_source_roots(metadata.cwd, adapter_config),
            attachments_root=self._attachments_root(session_id),
            state=_public_state(session_id, created_at, [], metadata).model_copy(
                update={
                    "history": LatestPublicHistoryPage(
                        entries=_rehome_public_history(initial_public_history, session_id),
                        cursor=HistoryCursor(),
                    )
                }
            ),
            lease=lease,
            mcp_runtime=mcp_runtime,
            connector_runtime=connector_runtime,
            on_work_state_changed=self._session_work_state_changed,
            discard_on_shutdown=self._discard,
            promote_on_start=promote,
            initialize_subagents_on_promote=initialize_subagents_on_promote,
            pending_plugin_binding=plugins,
            session_metadata=metadata,
            action_adapter=action_adapter,
            adapter_config=adapter_config,
            release_plugins=self._release_plugins(session_id),
            read_plugin_info=self._read_plugin_info(session_id),
            on_reconfigure_subagents=lambda cfg, sid=session_id: self.reconfigure_subagents(
                sid, cfg
            ),
        )
        if action_adapter is not None:
            action_adapter.bind_approval_requester(session._request_approval)
            action_adapter.bind_retry_sink(session._set_provider_retry)
        return session

    def _import_legacy(
        self,
        session_id: str,
        reference: LegacySessionReference | None,
        hook_bindings: Sequence[RustHarnessHookBinding] | None = None,
        hook_handlers: HookHandlers | None = None,
    ) -> tuple[UnifiedHarnessSessionBackend, StoredSession]:
        loader = self._legacy_source_loader
        if loader is None:
            raise HarnessSessionNotFoundError(session_id)
        try:
            source = loader(session_id)
        except Exception as exc:
            raise HarnessInvalidMigrationSourceError(
                session_id,
                "legacy",
                f"Failed to inspect legacy session {session_id}: {exc}",
            ) from exc
        if source.state == "absent":
            raise HarnessSessionNotFoundError(session_id)
        if source.state == "recoverable":
            raise HarnessUnfinishedMigrationError(session_id, "legacy")
        if source.state == "invalid":
            raise HarnessInvalidMigrationSourceError(
                session_id,
                "legacy",
                source.error or f"Legacy session is not importable: {session_id}",
            )
        source_reference = source.reference or reference
        if source_reference is None:
            source_reference = LegacySessionReference(
                session_id=session_id,
                cwd="",
            )
        if source_reference.session_id != session_id:
            raise HarnessInvalidMigrationSourceError(
                session_id,
                "legacy",
                "Legacy source resolver and exporter returned different session IDs",
            )
        if source.store_revision is None or source.history is None or source.error is not None:
            raise HarnessInvalidMigrationSourceError(
                session_id,
                "legacy",
                f"Legacy importer returned an incomplete source: {session_id}",
            )
        try:
            history = _INTEROP_HISTORY_ADAPTER.validate_python(source.history)
        except Exception as exc:
            raise HarnessInvalidMigrationSourceError(
                session_id,
                "legacy",
                f"Legacy committed history is invalid: {session_id}: {exc}",
            ) from exc
        export = committed_history(
            LegacyInteropSourceV1(
                session_id=source_reference.session_id,
                store_revision=source.store_revision,
            ),
            history,
        )
        provenance = ImportProvenanceV1(
            source=export.source,
            history_sha256=export.history_sha256,
            imported_at=_timestamp(),
        )
        target_session_id = generate_session_id()
        target_lease = SessionLease(self._storage_root, target_session_id).acquire()
        logger.info(
            "Unified session import source validated",
            extra={
                "harness_backend": "unified",
                "session_id": target_session_id,
                "source_backend": "legacy",
                "source_session_id": session_id,
                "source_fingerprint": export.history_sha256,
                "import_outcome": "validated",
            },
        )
        try:
            stored = self._write_initial_store(
                target_session_id,
                history,
                provenance,
                SessionMetadataV1(
                    cwd=source_reference.cwd,
                    root_session_id=target_session_id,
                    # A session imported from a legacy id carries the caller's
                    # freshly compiled bindings so its Core fires hooks from the
                    # first turn, matching a native start/resume.
                    hook_bindings=list(hook_bindings) if hook_bindings else [],
                ).model_copy(update=source.pin_values()),
            )
            return self._bind(stored, target_lease, None, hook_handlers), stored
        except BaseException:
            target_lease.release()
            raise

    def _rebind_hook_bindings(
        self,
        store: UnifiedSessionStore,
        stored: StoredSession,
        hook_bindings: Sequence[RustHarnessHookBinding],
    ) -> StoredSession:
        """Re-supply fresh hook bindings on a clean-exit resume.

        Only a quiescent session with an empty journal is rebound: there is nothing to
        replay, so changing the Core's bindings cannot diverge. A crash-mid-turn session
        (non-empty journal) keeps its persisted bindings so replay stays deterministic;
        the edited hooks then apply on its next clean session. The fresh set is persisted
        (a compaction-style generation write) so a subsequent crash replays against the
        bindings the Core actually ran with.
        """
        fresh = list(hook_bindings)
        metadata = stored.runtime_state.session_metadata
        if list(metadata.hook_bindings) == fresh:
            return stored
        if not stored.runtime_state.quiescent or stored.journal:
            return stored
        updated_state = stored.runtime_state.model_copy(
            update={"session_metadata": metadata.model_copy(update={"hook_bindings": fresh})}
        )
        store.write_generation(
            checkpoint=stored.checkpoint,
            runtime_state=updated_state,
            projection_state=stored.projection_state,
        )
        return store.load()

    def _bind(
        self,
        stored: StoredSession,
        lease: SessionLease,
        plugins: SessionPluginBinding | None = None,
        hook_handlers: HookHandlers | None = None,
        *,
        runtime_config_override: RustHarnessConfig | None = None,
        adapter_config_override: LocalRuntimeAdapterConfig | None = None,
        integrations_enabled: bool = True,
    ) -> UnifiedHarnessSessionBackend:
        public = stored.projection_state.snapshot.session
        pending_events: list[JsonObject] = []
        session: UnifiedHarnessSessionBackend | None = None

        def publish_event(event: JsonObject) -> None:
            if session is None:
                pending_events.append(event)
                return
            session._publish_event(event)

        adapter_config = adapter_config_override or self._adapter_config()

        def publish_mcp_event(signal: MCPAuthorizationRequiredSignal) -> None:
            publish_event(
                {
                    "type": "mcp_authorization_required",
                    "serverName": signal.server_name,
                    "reason": signal.reason,
                    "descriptorRevision": signal.descriptor_revision,
                    "observedConnectionRevision": signal.observed_connection_revision,
                }
            )

        def publish_connector_event(
            signal: ConnectorAuthorizationRequiredSignal,
        ) -> None:
            publish_event(
                {
                    "type": "connector_authorization_required",
                    "rawConnectorId": signal.raw_connector_id,
                    "alias": signal.alias,
                    "acceptedCatalogRevision": signal.accepted_catalog_revision,
                    "action": signal.action,
                    "reason": signal.reason,
                }
            )

        mcp_runtime = (
            self._new_mcp_runtime(event_sink=publish_mcp_event) if integrations_enabled else None
        )
        connector_runtime = (
            self._new_connector_runtime(event_sink=publish_connector_event)
            if integrations_enabled
            else None
        )
        mcp_executor = build_mcp_action_executor(mcp_runtime) if mcp_runtime is not None else None
        connector_executor = (
            build_connector_action_executor(connector_runtime)
            if connector_runtime is not None
            else None
        )

        runtime_config = runtime_config_override or self._runtime_config(
            stored.manifest.session_id,
            stored.runtime_state.session_metadata.hook_bindings,
            plugins=plugins,
        )
        registered_groups = _registered_group_names(
            self._provided_tool_groups, runtime_config.capabilities
        )
        registered_executors = _session_provided_tool_executors(
            self._provided_tool_groups, registered_groups, stored.manifest.session_id
        )

        async def execute_provided_tool(action: RustProvidedToolCallAction):
            if action.call.group_name == "ui" and action.call.tool_name == "ask_user_question":
                if session is None:
                    raise RuntimeError("Session is not ready for user input")
                return await session._request_user_input(action)
            registered = registered_executors.get(action.call.group_name)
            if registered is not None:
                return await registered(action)
            if connector_executor is not None and action.call.group_name.startswith("connector_"):
                return await connector_executor(action)
            if mcp_executor is not None:
                return await mcp_executor(action)
            if connector_executor is not None:
                return await connector_executor(action)
            raise RuntimeError("No provided-tool executor is configured")

        provided_tool_executor = (
            execute_provided_tool
            if mcp_executor is not None
            or connector_executor is not None
            or registered_executors
            or _has_provided_tool(
                runtime_config.capabilities,
                group_name="ui",
                tool_name="ask_user_question",
            )
            else None
        )
        provided_tool_modes = _session_provided_tool_modes(
            self._provided_tool_groups, registered_groups
        )
        store = UnifiedSessionStore(self._storage_root, stored.manifest.session_id)
        runtime_holder: list[DurableSessionRuntime] = []
        process_manager = self._new_process_manager(
            store,
            adapter_config,
            lambda snapshot: runtime_holder[0].submit_terminal_snapshot(snapshot),
        )
        runtime = DurableSessionRuntime.restore(
            config=runtime_config,
            store=store,
            stored=stored,
            event_sink=publish_event,
            process_manager=process_manager,
            process_config=adapter_config,
        )
        runtime_holder.append(runtime)
        # Fold the session's hook bindings into the base capabilities so an MCP/connector
        # snapshot reconfigure preserves them. Connectors are NOT folded here: the merge
        # below re-adds them from the live snapshot, so a connector-bearing base would
        # double-register a tool group.
        base_capabilities = (
            runtime_config.capabilities
            if runtime_config_override is not None
            else self._capabilities_with_hooks(stored.runtime_state.session_metadata.hook_bindings)
        )
        if mcp_runtime is not None:

            async def accept_mcp_snapshot(snapshot: MCPRouteSnapshot) -> None:
                connector_snapshot = (
                    connector_runtime.snapshot if connector_runtime is not None else None
                )
                await runtime.reconfigure_capability_dimension(
                    _integration_capability_update(
                        base_capabilities,
                        mcp_snapshot=snapshot,
                        connector_snapshot=connector_snapshot,
                    )
                )

            mcp_runtime.bind_snapshot_acceptor(accept_mcp_snapshot)
        if connector_runtime is not None:

            async def accept_connector_snapshot(
                snapshot: ConnectorRouteSnapshot,
            ) -> None:
                mcp_snapshot = mcp_runtime.snapshot if mcp_runtime is not None else None
                await runtime.reconfigure_capability_dimension(
                    _integration_capability_update(
                        base_capabilities,
                        mcp_snapshot=mcp_snapshot,
                        connector_snapshot=snapshot,
                    )
                )

            connector_runtime.bind_snapshot_acceptor(accept_connector_snapshot)
        # Built before the session so the session can own the handle: swapping
        # config on a live session must reuse this adapter, never rebuild it. The
        # hook registry is baked in here so a resumed session re-attaches its
        # handlers by binding id. The session's foreign handlers are merged with the
        # Host-global builtins (see merge_hook_handlers) so a session with user hooks
        # still runs always-bound builtins; baking it here also means concurrent
        # lifecycle ops cannot race the handler this session binds.
        handlers = (
            merge_hook_handlers(self._hook_handlers, hook_handlers)
            if hook_handlers is not None
            else self._hook_handlers
        )
        # Only foreign hooks surface public notices (see _emits_run_notices).
        foreign_ids = (
            foreign_binding_ids(hook_handlers) if hook_handlers is not None else frozenset()
        )
        action_adapter = (
            _LocalActionState(
                adapter_config or LocalRuntimeAdapterConfig(),
                None,
                provided_tool_executor,
                handlers,
                stored.manifest.session_id,
                filesystem_root=store.session_root,
                foreign_binding_ids=foreign_ids,
                provided_tool_modes=provided_tool_modes,
            )
            if adapter_config is not None
            or provided_tool_executor is not None
            or has_hook_handlers(handlers)
            else None
        )
        session = UnifiedHarnessSessionBackend(
            stored.manifest.session_id,
            public.created_at,
            cwd=stored.runtime_state.session_metadata.cwd,
            image_source_roots=self._image_source_roots(
                stored.runtime_state.session_metadata.cwd, adapter_config
            ),
            attachments_root=self._attachments_root(stored.manifest.session_id),
            state=stored.projection_state.snapshot,
            watermark=stored.projection_state.watermark,
            lease=lease,
            runtime=runtime,
            mcp_runtime=mcp_runtime,
            connector_runtime=connector_runtime,
            on_work_state_changed=self._session_work_state_changed,
            action_adapter=action_adapter,
            adapter_config=adapter_config,
            release_plugins=self._release_plugins(stored.manifest.session_id),
            read_plugin_info=self._read_plugin_info(stored.manifest.session_id),
            on_reconfigure_subagents=lambda cfg, sid=stored.manifest.session_id: (
                self.reconfigure_subagents(sid, cfg)
            ),
            foreign_hook_handlers=hook_handlers,
        )
        for event in pending_events:
            session._publish_event(event)
        if action_adapter is not None:
            action_adapter.bind_approval_requester(session._request_approval)
            action_adapter.bind_retry_sink(session._set_provider_retry)
            runtime.configure_action_executor(action_adapter.execute)
            action_adapter.bind_notice_sink(runtime.append_public_history_entries)
            action_adapter.bind_classification_sink(publish_event)
            if streams_provisional_content(
                handlers, stored.runtime_state.session_metadata.hook_bindings
            ):
                action_adapter.bind_completion_delta_sink(
                    runtime.append_provisional_completion_content
                )
        if process_manager is not None and adapter_config is not None:
            runtime.configure_process_runtime(
                process_manager,
                adapter_config,
                session._request_process_approval,
            )
        if connector_runtime is not None:
            bound_connector_runtime = connector_runtime

            async def publish_connector_authorization_after_action(
                action: object, _event: object
            ) -> None:
                if not isinstance(action, RustProvidedToolCallAction):
                    return
                await bound_connector_runtime.publish_pending_authorization(
                    group_name=action.call.group_name,
                    tool_name=action.call.tool_name,
                )

            runtime.configure_action_applied_sink(publish_connector_authorization_after_action)
        return session

    async def _initialize_integrations(
        self, session: UnifiedHarnessSessionBackend, *, push_to_core: bool = True
    ) -> None:
        """Resolve MCP and connector catalogs into dispatchable route snapshots.

        With ``push_to_core`` false the routes are readied on the Runtime without
        a Core capability reconfigure. Resume uses this to make provided-tool
        routes available before recovering an in-flight turn (Core rejects
        reconfigure while a turn is running); ``_push_integration_capabilities``
        then publishes them once recovery leaves Core idle.
        """
        if self._mcp_catalog is not None and self._mcp_catalog.servers:
            logger.debug(
                "Initializing MCP integrations for %s (%d servers)",
                session.session_id,
                len(self._mcp_catalog.servers),
            )
            await session.reconfigure_mcp(
                self._mcp_catalog, force_remote_discovery=False, push_to_core=push_to_core
            )
            logger.debug("MCP integrations initialized for %s", session.session_id)
        if self._connector_catalog is not None and self._connector_selection is not None:
            logger.debug(
                "Initializing connector integrations for %s (%d connectors)",
                session.session_id,
                len(self._connector_catalog.connectors),
            )
            await session.reconfigure_connectors(
                self._connector_catalog, self._connector_selection, push_to_core=push_to_core
            )
            logger.debug("Connector integrations initialized for %s", session.session_id)

    async def _push_integration_capabilities(self, session: UnifiedHarnessSessionBackend) -> None:
        """Publish the readied MCP and connector routes to an idle Core.

        Pairs with ``_initialize_integrations(push_to_core=False)`` after an
        in-flight turn has been recovered, so Core's advertised capabilities
        catch up to the routes the Runtime already dispatches.
        """
        if self._mcp_catalog is not None and self._mcp_catalog.servers:
            await session.push_mcp_capabilities()
        if self._connector_catalog is not None and self._connector_selection is not None:
            await session.push_connector_capabilities()

    async def _initialize_subagents(
        self, session: UnifiedHarnessSessionBackend, plugins: SessionPluginBinding | None = None
    ) -> None:
        await self._initialize_subagent_runtime(
            session._runtime_for_host(), self._session_subagents(plugins)
        )

    async def _initialize_subagent_runtime(
        self,
        runtime: DurableSessionRuntime,
        configured: ResolvedSubagentConfiguration | None,
    ) -> None:
        if configured is None:
            return
        controller = await SubagentController.open(
            runtime=runtime,
            child_host=self,
            bindings=configured.bindings,
            policy_ceiling=configured.policy_ceiling,
        )
        await controller.reconcile_actions(runtime.pending_action_ids)

    def _new_mcp_runtime(
        self,
        *,
        event_sink: Callable[[MCPAuthorizationRequiredSignal], None] | None = None,
    ) -> MCPRuntime | None:
        provider = self._mcp_authorization_provider
        if provider is None:
            return None
        config = self._runtime_config_template
        claimed_groups = frozenset(
            group.name
            for capability_set in (
                config.capabilities,
                *(plugin.capabilities for plugin in config.plugins),
            )
            for group in capability_set.tool_groups
        )
        return MCPRuntime(
            cache_root=self._mcp_cache_root,
            cache_policy=self._mcp_cache_policy,
            http_transport_policy=self._mcp_http_transport_policy,
            authorization_provider=provider,
            sampling_completion=self._mcp_sampling_completion,
            transport_factory=self._mcp_transport_factory,
            stdio_pool=self._stdio_pool(),
            event_sink=event_sink,
            claimed_groups=claimed_groups,
        )

    def _stdio_pool(self) -> MCPStdioPool:
        # Created once and deliberately kept across ``configure_mcp``: reconfiguration runs
        # on every start, resume, fork and list, and rebuilding the pool there would orphan
        # the subprocesses every live Session is still holding.
        #
        # Sessions are created off the loop via ``asyncio.to_thread``, so two overlapping
        # creates can reach this check-then-act on different worker threads. Without the
        # lock each builds a pool, the second overwrites the Host pointer, and the first
        # is orphaned: never leased to a shared process, never retired, never closed.
        with self._mcp_stdio_pool_lock:
            if self._mcp_stdio_pool is None:
                self._mcp_stdio_pool = MCPStdioPool(
                    self._mcp_transport_factory,
                    sampling_completion=self._mcp_sampling_completion,
                )
            return self._mcp_stdio_pool

    def _retire_stdio_pool_if_stale(
        self,
        sampling_completion: MCPSamplingCompletion | None,
        transport_factory: MCPTransportFactory | None,
    ) -> None:
        """Stop handing out leases on a pool built from superseded transport wiring.

        The retired pool is kept rather than closed: Sessions already leasing it keep
        their processes until they close, and shutdown reaps whatever is left.
        """
        with self._mcp_stdio_pool_lock:
            pool = self._mcp_stdio_pool
            if pool is None:
                return
            if (
                sampling_completion is self._mcp_sampling_completion
                and transport_factory is self._mcp_transport_factory
            ):
                return
            self._retired_mcp_stdio_pools.append(pool)
            self._mcp_stdio_pool = None

    def _new_connector_runtime(
        self,
        *,
        event_sink: Callable[[ConnectorAuthorizationRequiredSignal], None] | None = None,
    ) -> ConnectorRuntime | None:
        gateway_factory = self._connector_gateway_factory
        if gateway_factory is None:
            return None
        config = self._runtime_config_template
        claimed_groups = frozenset(
            group.name
            for capability_set in (
                config.capabilities,
                *(plugin.capabilities for plugin in config.plugins),
            )
            for group in capability_set.tool_groups
        )
        return ConnectorRuntime(
            gateway_factory(),
            event_sink=event_sink,
            claimed_groups=claimed_groups,
        )

    def _plugin_binder(self) -> SessionPluginBinder | None:
        provider = self._plugin_provider
        if provider is None:
            return None
        return SessionPluginBinder(provider, PluginPackageStore(self._storage_root))

    async def _pin_plugins(self, session_id: str) -> SessionPluginBinding:
        """Create path: pin what the caller requested, or bind nothing at all."""
        binder = self._plugin_binder()
        if binder is None:
            return empty_plugin_binding()
        return await binder.create(self._requested_plugins, session_id=session_id)

    async def _restore_plugins(self, session_id: str, lock: PluginLockV1) -> SessionPluginBinding:
        """Restore path: rebuild the recorded pin, whatever is installed now.

        A lock naming packages with no provider configured is a dead session
        rather than a silently plugin-less one, because the recorded transcript
        was produced with tools this process cannot offer.
        """
        binder = self._plugin_binder()
        if binder is None:
            if not lock.plugins:
                return empty_plugin_binding()
            raise PluginRestoreError(
                [
                    PluginRestoreDiagnostic(
                        code=PluginRestoreDiagnosticCode.LOCK_INVALID,
                        plugin_name=entry.name,
                        content_digest=entry.content_digest,
                        message="the session pinned plugins but no plugin provider is configured",
                    )
                    for entry in lock.plugins
                ]
            )
        return await binder.restore(lock, session_id=session_id)

    def _read_plugin_info(self, session_id: str) -> Callable[[], Awaitable[PluginInfo]] | None:
        """The read half of the seam, bound to one session.

        A binder is built per call for the same reason ``_release_plugins``
        builds one: the provider and the storage root are Host state that a
        long-lived closure would pin to whatever they were at bind time.
        """
        binder = self._plugin_binder()
        if binder is None:
            return None
        return lambda: binder.info(session_id=session_id)

    def _release_plugins(self, session_id: str) -> Callable[[], Awaitable[None]] | None:
        binder = self._plugin_binder()
        if binder is None:
            return None
        return lambda: binder.release(session_id=session_id)

    async def _release_bound_plugins(self, session_id: str) -> None:
        """Release a set that bound but never reached a session to own it."""
        if release := self._release_plugins(session_id):
            await release()

    async def _rebind_recorded_plugins(self, session_id: str, lock: PluginLockV1) -> None:
        """Undo a re-pin that bound its new set but failed to record it."""
        binder = self._plugin_binder()
        if binder is None:
            return
        try:
            await binder.restore(lock, session_id=session_id)
        except Exception:
            logger.warning(
                "Plugin re-pin rollback failed; the session is left without a bound plugin set",
                extra={
                    "harness_backend": "unified",
                    "session_id": session_id,
                    "plugin_count": len(lock.plugins),
                },
                exc_info=True,
            )

    def _preflight_restore(
        self,
        store: UnifiedSessionStore,
        stored: StoredSession,
        plugins: SessionPluginBinding | None = None,
    ) -> StoredSession:
        """Prove the generation opens, repairing it to the last point that does.

        A divergent record cannot be replayed by any later process, so failing
        here strands the session for good. Recovering the verified prefix keeps
        the conversation and leaves the interrupted work for ``recover`` to
        settle, which is what a crash mid-turn already means.
        """
        config = self._runtime_config(
            stored.manifest.session_id,
            stored.runtime_state.session_metadata.hook_bindings,
            plugins=plugins,
        )
        try:
            core = stored.restore_core(config)
            core.close()
            return stored
        except HarnessReplayDivergenceError as divergence:
            sequence = (divergence.details or {}).get("sequence")
            if not isinstance(sequence, int):
                raise
            logger.warning(
                "Unified session recovered a diverged journal",
                extra={
                    "harness_backend": "unified",
                    "session_id": stored.manifest.session_id,
                    "store_format": "mistral.vibe.unified-session-store/v1",
                    "divergent_sequence": sequence,
                    "divergence": (divergence.details or {}).get("divergence"),
                    "discarded_records": sum(
                        1 for record in stored.journal if record.sequence >= sequence
                    ),
                },
            )
            store.repair_diverged_generation(config, sequence)
            repaired = store.load()
            core = repaired.restore_core(config)
            core.close()
            return repaired

    def _schedule_orphan_diagnostic(self, parent_state: RuntimeStateV3) -> None:
        if self._closed:
            return
        parent_session_id = parent_state.session_id
        current = self._orphan_diagnostic_tasks.get(parent_session_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(
            self._run_orphan_diagnostic(
                parent_session_id,
                frozenset(_tree_child_session_ids(parent_state)),
            ),
            name=f"subagent-orphan-diagnostic:{parent_session_id}",
        )
        self._orphan_diagnostic_tasks[parent_session_id] = task
        task.add_done_callback(
            lambda completed: self._orphan_diagnostic_finished(
                parent_session_id,
                completed,
            )
        )

    async def _run_orphan_diagnostic(
        self,
        parent_session_id: str,
        referenced_child_session_ids: frozenset[str],
    ) -> None:
        orphan_count = await self._report_orphan_children(
            parent_session_id,
            referenced_child_session_ids,
        )
        if orphan_count:
            logger.warning(
                "Unified subagent orphans detected",
                extra={
                    "harness_backend": "unified",
                    "orphan_count": orphan_count,
                },
            )

    def _orphan_diagnostic_finished(
        self,
        parent_session_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._orphan_diagnostic_tasks.get(parent_session_id) is task:
            del self._orphan_diagnostic_tasks[parent_session_id]
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.warning(
                "Unified subagent orphan diagnostic failed",
                extra={
                    "harness_backend": "unified",
                    "session_id": parent_session_id,
                },
                exc_info=True,
            )

    async def _report_orphan_children(
        self,
        parent_session_id: str,
        referenced_child_session_ids: frozenset[str],
    ) -> int:
        entries = await asyncio.to_thread(UnifiedSessionCatalog(self._storage_root).entries)
        orphan_count = 0
        for entry in entries:
            if entry.session_id in referenced_child_session_ids:
                continue
            identity = entry.identity
            if (
                isinstance(identity, SubagentSessionIdentity)
                and identity.parent_session_id == parent_session_id
            ):
                add_subagent_orphan_detected()
                orphan_count += 1
        return orphan_count

    async def _acquire_session_lease(self, session_id: str) -> SessionLease:
        """Acquire a lease without racing an output-maintenance pass."""

        lease = SessionLease(self._storage_root, session_id)
        try:
            return await asyncio.to_thread(lease.acquire)
        except HarnessSessionBusyError as busy:
            cleanup_lease = SessionLease(self._storage_root, "process-output-cleanup")
            while True:
                try:
                    await asyncio.to_thread(cleanup_lease.acquire)
                    break
                except HarnessSessionBusyError:
                    await asyncio.sleep(_LEASE_RETRY_DELAY_SECONDS)
            try:
                try:
                    return await asyncio.to_thread(lease.acquire)
                except HarnessSessionBusyError:
                    raise busy from None
            finally:
                await asyncio.to_thread(cleanup_lease.release)

    def _capabilities_with_hooks(
        self, hook_bindings: Sequence[RustHarnessHookBinding]
    ) -> RustHarnessCapabilitySet:
        """Template capabilities plus the session's hook bindings, without connectors/MCP.

        This is the base for dynamic capability reconfiguration: connector/MCP tool groups
        are added by ``_merge_integration_capabilities`` on top, so folding them in here as
        well would double-register a group. Hook bindings, by contrast, must persist across
        a reconfigure, so they belong in the base.
        """
        base = self._runtime_config_template.capabilities
        if not hook_bindings:
            return base
        return base.model_copy(
            update={"hook_bindings": [*base.hook_bindings, *hook_bindings]},
            deep=True,
        )

    def _session_subagents(
        self, plugins: SessionPluginBinding | None
    ) -> ResolvedSubagentConfiguration | None:
        # The ceiling stays Host-global; only the table is per session, because a
        # restored session replays its own lock rather than the current request.
        return _extend_subagent_bindings(
            self._subagent_configuration, self._configured_adapter_config, plugins
        )

    def _runtime_config(
        self,
        session_id: str,
        hook_bindings: Sequence[RustHarnessHookBinding] = (),
        *,
        plugins: SessionPluginBinding | None = None,
    ) -> RustHarnessConfig:
        definitions = plugins.definitions if plugins is not None else ()
        config = self._runtime_config_template.model_copy(
            update={"task_id": session_id, "plugins": list(definitions)}
            if definitions
            else {"task_id": session_id},
            deep=True,
        )
        capabilities = self._capabilities_with_hooks(hook_bindings)
        catalog = self._connector_catalog
        selection = self._connector_selection
        if (
            catalog is not None
            and selection is not None
            and self._connector_gateway_factory is not None
        ):
            claimed_groups = frozenset(
                group.name
                for capability_set in (
                    capabilities,
                    *(plugin.capabilities for plugin in config.plugins),
                )
                for group in capability_set.tool_groups
            )
            connector_snapshot = plan_connector_snapshot(
                catalog, selection, claimed_groups=claimed_groups
            )
            capabilities = _merge_integration_capabilities(
                capabilities,
                mcp_snapshot=None,
                connector_snapshot=connector_snapshot,
            )
        config = config.model_copy(update={"capabilities": capabilities}, deep=True)
        configured = self._session_subagents(plugins)
        return advertise_bound_agent_types(
            config,
            configured.bindings if configured is not None else {},
        )

    def _adapter_config(self) -> LocalRuntimeAdapterConfig | None:
        return self._adapter_config_template

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("The Unified Harness Host cannot move between event loops")
        self._loop = loop
        self._request_process_output_cleanup()

    def _new_process_manager(
        self,
        store: UnifiedSessionStore,
        adapter_config: LocalRuntimeAdapterConfig | None,
        terminal_callback: Callable[[ProcessTerminalSnapshot], None],
    ) -> SessionProcessManager | None:
        if (
            adapter_config is None
            or adapter_config.process_authority != "host_shell"
            or self._runtime_config_template.settings.tools.background_processes.mode != "enabled"
        ):
            return None
        if adapter_config.command_environment == "unix":
            from mistralai_vibe_local_harness.vibe._processes._posix import PosixTerminalBackend

            backend: TerminalBackend = PosixTerminalBackend()
        elif adapter_config.command_environment in {"git_bash", "powershell"}:
            from mistralai_vibe_local_harness.vibe._processes._windows import WindowsTerminalBackend

            backend = cast(
                TerminalBackend, WindowsTerminalBackend(adapter_config.command_environment)
            )
        else:
            raise ValueError("host-shell process authority requires a native shell profile")
        if self._loop is None:
            raise RuntimeError("The Unified Harness Host has no event loop")
        return SessionProcessManager(
            session_root=store.session_root,
            backend=backend,
            terminal_callback=terminal_callback,
            loop=self._loop,
        )

    def _image_source_roots(
        self,
        cwd: str | None,
        adapter_config: LocalRuntimeAdapterConfig | None,
    ) -> tuple[Path, ...]:
        workspace_roots = (
            adapter_config.workspace_roots
            if adapter_config is not None and adapter_config.workspace_roots
            else (Path(cwd or Path.cwd()),)
        )
        return tuple(root.expanduser().resolve() for root in workspace_roots)

    def _session_root(self, session_id: str) -> Path:
        return (self._storage_root / "unified" / session_id).expanduser().resolve()

    def _attachments_root(self, session_id: str) -> Path:
        return self._session_root(session_id) / "attachments"

    def _list(
        self,
        limit: int,
        cursor: str | None,
        cwd: str | None,
        root_session_id: str | None,
        parent_session_id: str | None,
        ephemeral_session_ids: frozenset[str],
        live_sessions: Mapping[str, PublicSession],
    ) -> HarnessSessionListResult:
        if limit < 1:
            raise ValueError("session list limit must be positive")
        unified_root = self._storage_root / "unified"
        if not unified_root.exists():
            return HarnessSessionListResult(items=(), continue_session_id=None)
        stored_sessions: list[tuple[int, str, PublicSession, SessionMetadataV1]] = []
        for entry in UnifiedSessionCatalog(self._storage_root).entries():
            session_id = entry.session_id
            if session_id in ephemeral_session_ids:
                continue
            # Prefer this process's live projection; otherwise settle a mid-turn
            # row only when no process holds the lease (``_list`` runs in a thread).
            public = live_sessions.get(session_id)
            if public is None:
                public = entry.session
                if isinstance(
                    public.status, RunningSessionStatus | BlockedSessionStatus
                ) and not session_is_live(self._storage_root, session_id):
                    public = public.model_copy(update={"status": IdleSessionStatus()})
            metadata = entry.metadata
            if parent_session_id is None and isinstance(entry.identity, SubagentSessionIdentity):
                continue
            if cwd is not None and metadata.cwd != cwd:
                continue
            if root_session_id is not None and metadata.root_session_id != root_session_id:
                continue
            if parent_session_id is not None and metadata.parent_session_id != parent_session_id:
                continue
            stored_sessions.append((public.updated_at, session_id, public, metadata))
        stored_sessions.sort(key=lambda item: (item[0], item[1]), reverse=True)
        start = 0
        if cursor is not None:
            start = next(
                (
                    index + 1
                    for index, (
                        _updated_at,
                        session_id,
                        _public,
                        _metadata,
                    ) in enumerate(stored_sessions)
                    if session_id == cursor
                ),
                len(stored_sessions),
            )
        page = stored_sessions[start : start + limit]
        next_cursor = page[-1][1] if start + limit < len(stored_sessions) else None
        previous_start = max(0, start - limit)
        previous_cursor = stored_sessions[previous_start - 1][1] if previous_start > 0 else None
        return HarnessSessionListResult(
            items=tuple(HarnessSessionListItem(session=item[2], cwd=item[3].cwd) for item in page),
            continue_session_id=stored_sessions[0][1] if stored_sessions else None,
            next_cursor=next_cursor,
            previous_cursor=previous_cursor,
        )

    def _resolve_unified_session_id(self, requested: str) -> str | None:
        unified_root = self._storage_root / "unified"
        exact = unified_root / requested
        if (exact / "CURRENT").is_file():
            return requested
        if not unified_root.exists() or len(requested) > _SHORT_SESSION_ID_LENGTH:
            return None
        matches = sorted(
            path.name
            for path in unified_root.iterdir()
            if path.is_dir()
            and path.name[:_SHORT_SESSION_ID_LENGTH] == requested
            and (path / "CURRENT").is_file()
        )
        if len(matches) > 1:
            raise ValueError(f"Unified session ID is ambiguous: {requested}")
        return matches[0] if matches else None

    def _resolve_legacy_import_id(self, source_session_id: str) -> str | None:
        matches: list[tuple[str, str]] = []
        for entry in UnifiedSessionCatalog(self._storage_root).entries():
            provenance = entry.import_provenance
            if (
                provenance is not None
                and isinstance(provenance.source, LegacyInteropSourceV1)
                and provenance.source.session_id == source_session_id
            ):
                matches.append((provenance.imported_at, entry.session_id))
        return min(matches)[1] if matches else None

    def _catalog_entry(self, session_id: str) -> SessionCatalogEntryV1 | None:
        return UnifiedSessionCatalog(self._storage_root).entry(session_id)

    @staticmethod
    def _importable_history(stored: StoredSession) -> "list[InteropHistoryMessageV1]":
        export = stored.interop_export
        if not stored.runtime_state.quiescent or stored.journal or export is None:
            raise ValueError("source session is not at an exported quiescent boundary")
        return list(export.history)

    async def _register(
        self, session: UnifiedHarnessSessionBackend
    ) -> UnifiedHarnessSessionBackend:
        async with self._registry_lock:
            previous = self._sessions.get(session.session_id)
            if previous is not None and previous.session is not session:
                raise RuntimeError(f"session is already registered: {session.session_id}")
            self._sessions[session.session_id] = _LoadedSessionEntry(session=session, attachments=1)
        session._configure_work_state_callback(self._session_work_state_changed)
        if not session.ephemeral:
            self._connect_child_events(session)
            identity = session._runtime_for_host().identity
            if not isinstance(identity, SubagentSessionIdentity):
                for entry in tuple(self._sessions.values()):
                    candidate = entry.session
                    if candidate.ephemeral:
                        continue
                    candidate_identity = candidate._runtime_for_host().identity
                    if (
                        isinstance(candidate_identity, SubagentSessionIdentity)
                        and candidate_identity.parent_session_id == session.session_id
                    ):
                        self._connect_child_events(candidate)
        return self._attachment(session)

    def _connect_child_events(self, child: UnifiedHarnessSessionBackend) -> None:
        if child.ephemeral:
            return
        identity = child._runtime_for_host().identity
        if not isinstance(identity, SubagentSessionIdentity):
            return
        parent = self._live_session(identity.parent_session_id)
        if parent is None:
            return
        observer_id = f"parent:{identity.parent_session_id}"
        snapshot = child.observe_events(
            observer_id,
            lambda event: parent.publish_child_event(identity.session_id, event),
        )
        parent.publish_child_registration(identity.session_id, snapshot)

    async def _attach_live_session(self, session_id: str) -> UnifiedHarnessSessionBackend | None:
        while True:
            async with self._registry_lock:
                self._guard_open()
                entry = self._sessions.get(session_id)
                if entry is None:
                    return None
                if entry.state == "open":
                    entry.attachments += 1
                    return self._attachment(entry.session)
                closed = entry.closed
            await closed.wait()

    async def _claim_session_load(self, session_id: str) -> tuple[bool, _LoadingSession]:
        identity = self._bind_identity()
        while True:
            async with self._registry_lock:
                maintenance = self._maintenance_sessions.get(session_id)
                if maintenance is None:
                    existing = self._loading_sessions.get(session_id)
                    if existing is not None:
                        if existing.identity != identity:
                            raise RuntimeError(
                                f"session is already loading with different settings: {session_id}"
                            )
                        return False, existing
                    load = _LoadingSession(identity, asyncio.get_running_loop().create_future())
                    self._loading_sessions[session_id] = load
                    return True, load
            await maintenance.wait()

    async def _finish_session_load(
        self,
        session_id: str,
        *,
        loaded_session_id: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        async with self._registry_lock:
            load = self._loading_sessions.pop(session_id, None)
            if load is not None and not load.completed.done():
                load.completed.set_result((loaded_session_id, error))

    def _bind_identity(self) -> tuple[object, ...]:
        return (
            self._runtime_config_template.model_dump_json(exclude_none=False),
            tuple(plugin.model_dump_json(exclude_none=False) for plugin in self._requested_plugins),
            self._adapter_config_template,
            id(self._legacy_source_loader),
            id(self._legacy_source_resolver),
        )

    def _live_session(self, session_id: str) -> UnifiedHarnessSessionBackend | None:
        entry = self._sessions.get(session_id)
        return entry.session if entry is not None and entry.state == "open" else None

    def _require_live_child(self, child: ChildSessionHandle) -> UnifiedHarnessSessionBackend:
        session = self._live_session(child.session_id)
        if session is None:
            raise KeyError(f"child Session is not loaded: {child.session_id}")
        return session

    def _live_session_ids(self) -> frozenset[str]:
        return frozenset(self._sessions)

    def _ephemeral_session_ids(self) -> frozenset[str]:
        return frozenset(
            session_id for session_id, entry in self._sessions.items() if entry.session.ephemeral
        )

    def _live_listed_sessions(self) -> dict[str, PublicSession]:
        return {
            session_id: entry.session._state_for_host().session
            for session_id, entry in self._sessions.items()
            if entry.state == "open" and not entry.session.ephemeral
        }

    def _attachment(self, session: UnifiedHarnessSessionBackend) -> UnifiedHarnessSessionBackend:
        return cast(
            UnifiedHarnessSessionBackend,
            _SessionAttachment(session, self._detach_session),
        )

    async def _detach_session(self, session: UnifiedHarnessSessionBackend) -> None:
        async with self._registry_lock:
            entry = self._sessions.get(session.session_id)
            if entry is None or entry.session is not session or entry.state != "open":
                return
            if entry.attachments <= 0:
                raise RuntimeError("session attachment count is already zero")
            entry.attachments -= 1
        await self._evict_if_idle(session.session_id, propagate=True)

    async def _close_session(self, session: UnifiedHarnessSessionBackend) -> None:
        """Close a Session once and retire its Host registry entry."""

        async with self._registry_lock:
            entry = self._sessions.get(session.session_id)
            if entry is None:
                owner = True
                closed = None
            elif entry.session is not session:
                raise RuntimeError(f"session registration changed: {session.session_id}")
            elif entry.state == "open":
                entry.state = "closing"
                owner = True
                closed = entry.closed
            else:
                owner = False
                closed = entry.closed
        if not owner:
            assert closed is not None
            await closed.wait()
            return
        try:
            await session.shutdown()
        finally:
            if closed is not None:
                async with self._registry_lock:
                    if self._sessions.get(session.session_id) is entry:
                        del self._sessions[session.session_id]
                    closed.set()

    async def _session_work_state_changed(self, session_id: str) -> None:
        self._request_process_output_cleanup()
        try:
            await self._evict_if_idle(session_id, propagate=False)
        except BaseException:
            logger.warning(
                "Unified session eviction failed",
                extra={"harness_backend": "unified", "session_id": session_id},
            )

    async def _evict_if_idle(self, session_id: str, *, propagate: bool) -> None:
        async with self._registry_lock:
            entry = self._sessions.get(session_id)
            if (
                entry is None
                or entry.state != "open"
                or entry.attachments
                or entry.maintenance_pins
                or entry.session._has_active_work
            ):
                return
            entry.state = "evicting"
        error: BaseException | None = None
        try:
            await entry.session.shutdown()
        except BaseException as exc:
            error = exc
        finally:
            async with self._registry_lock:
                if self._sessions.get(session_id) is entry:
                    del self._sessions[session_id]
                entry.closed.set()
        self._request_process_output_cleanup()
        if error is not None and propagate:
            raise error

    async def _wait_for_maintenance(self, session_id: str) -> None:
        while True:
            async with self._registry_lock:
                maintenance = self._maintenance_sessions.get(session_id)
            if maintenance is None:
                return
            await maintenance.wait()

    def _request_process_output_cleanup(self) -> None:
        if self._closed or self._loop is None:
            return
        self._cleanup_requested = True
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._run_process_output_cleanup(), name="process-output-cleanup"
            )

    async def _debounce_process_output_cleanup(self) -> None:
        """Wait out the cooldown after the previous sweep.

        Kept as its own seam so tests can replace the wait instead of sleeping.
        """
        finished_at = self._cleanup_finished_at
        if finished_at is None:
            return
        remaining = _PROCESS_OUTPUT_CLEANUP_DEBOUNCE_SECONDS - (time.monotonic() - finished_at)
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _run_process_output_cleanup(self) -> None:
        while self._cleanup_requested:
            # Defer, never drop: a request that arrives while a sweep is running
            # must still be served, or the last request before an idle period is
            # lost with no task left to run it.
            await self._debounce_process_output_cleanup()
            self._cleanup_requested = False
            try:
                await self._cleanup_process_output_once()
            except Exception:
                logging.getLogger("vibe.unified_harness.processes").warning(
                    "background_process.operation_failed"
                )
            finally:
                # Timed from the end of the sweep so the cooldown caps the duty
                # cycle rather than the period, and arms even when the sweep
                # returned early or raised.
                self._cleanup_finished_at = time.monotonic()

    async def _cleanup_process_output_once(self) -> None:
        cleanup_lease = SessionLease(self._storage_root, "process-output-cleanup")
        owned: list[tuple[str, asyncio.Event, SessionLease]] = []
        pinned: list[tuple[str, _LoadedSessionEntry]] = []
        try:
            await _maintenance_io(cleanup_lease.acquire)
        except HarnessSessionBusyError:
            return
        except BaseException:
            await asyncio.to_thread(cleanup_lease.release)
            raise
        candidates: list[tuple[str, str, ProcessOutputStore, int]] = []
        total = 0
        removed = 0
        try:
            unified_root = self._storage_root / "unified"
            session_ids = await _maintenance_io(_stored_session_ids, unified_root)
            # Recomputed every sweep on purpose. Memoising this across sweeps
            # would make a missed directory permanent instead of self-healing.
            with_process_output = await _maintenance_io(
                _stored_sessions_with_process_output, unified_root, session_ids
            )
            for session_id in session_ids:
                async with self._registry_lock:
                    entry = self._sessions.get(session_id)
                    if entry is not None:
                        if entry.state != "open":
                            continue
                        entry.maintenance_pins += 1
                        pinned.append((session_id, entry))
                        maintenance = None
                    elif (
                        session_id in self._loading_sessions
                        or session_id in self._maintenance_sessions
                    ):
                        continue
                    else:
                        maintenance = asyncio.Event()
                        self._maintenance_sessions[session_id] = maintenance
                store = UnifiedSessionStore(self._storage_root, session_id)
                if maintenance is not None:
                    lease = SessionLease(self._storage_root, session_id)
                    owned.append((session_id, maintenance, lease))
                    if session_id not in with_process_output:
                        # No output to reclaim, and every repair this branch would
                        # make is redone by the Runtime when the Session is next
                        # opened (_orphan_process, _record_recovered_start), so
                        # skipping defers rather than loses. Registered in `owned`
                        # first so the shielded release still frees the claim if we
                        # are cancelled; releasing an unacquired lease is a no-op.
                        continue
                    try:
                        await _maintenance_io(lease.acquire)
                    except HarnessSessionBusyError:
                        owned.pop()
                        await self._release_maintenance(session_id, maintenance)
                        continue
                    try:
                        stored = await _maintenance_io(store.load)
                        process_ids = {
                            process.process_id for process in stored.runtime_state.processes
                        }
                        protected_process_ids = process_ids | {
                            prepared.process_id
                            for action in stored.runtime_state.actions
                            if action.state in {"pending", "running"}
                            and (prepared := action.prepared_process_start) is not None
                        }
                        stale_process_ids = {
                            process.process_id
                            for process in stored.runtime_state.processes
                            if process.status == "running"
                        }
                        stale_process_ids.update(protected_process_ids - process_ids)
                        for process_id in stale_process_ids:
                            await _maintenance_io(
                                ProcessOutputStore.ensure_unavailable,
                                store.session_root,
                                process_id,
                            )
                        reconciled = await _maintenance_io(store.reconcile_orphaned_processes)
                        for _process in reconciled:
                            logging.getLogger("vibe.unified_harness.processes").warning(
                                "background_process.orphaned"
                            )
                        stored = await _maintenance_io(store.load)
                    except Exception:
                        continue
                else:
                    try:
                        stored = await _maintenance_io(store.load)
                    except Exception:
                        continue
                    protected_process_ids = {
                        process.process_id for process in stored.runtime_state.processes
                    }
                    protected_process_ids.update(
                        prepared.process_id
                        for action in stored.runtime_state.actions
                        if action.state in {"pending", "running"}
                        and (prepared := action.prepared_process_start) is not None
                    )
                removed += await _maintenance_io(
                    ProcessOutputStore.discard_unreferenced,
                    store.session_root,
                    protected_process_ids,
                )
                for process in stored.runtime_state.processes:
                    if process.status == "running":
                        continue
                    try:
                        output = await _maintenance_io(
                            ProcessOutputStore.recover,
                            store.session_root,
                            process.process_id,
                        )
                        removed += await _maintenance_io(output.discard_quarantines)
                        size = await _maintenance_io(output.stored_bytes)
                    except (OSError, ValueError):
                        continue
                    total += size
                    candidates.append(
                        (
                            process.finished_at or process.created_at,
                            process.process_id,
                            output,
                            size,
                        )
                    )
            for _finished_at, _process_id, output, size in sorted(candidates):
                if total <= _PROCESS_OUTPUT_TARGET_BYTES:
                    break
                removed_now = await _maintenance_io(output.prune)
                removed += removed_now
                total -= min(size, removed_now)
        finally:
            release = asyncio.create_task(
                self._release_cleanup_ownership(owned, pinned, cleanup_lease)
            )
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                with suppress(Exception):
                    await release
                raise
        if removed:
            logging.getLogger("vibe.unified_harness.processes").info(
                "background_process.output_pruned"
            )

    async def _release_cleanup_ownership(
        self,
        owned: builtins.list[tuple[str, asyncio.Event, SessionLease]],
        pinned: builtins.list[tuple[str, _LoadedSessionEntry]],
        cleanup_lease: SessionLease,
    ) -> None:
        errors: list[BaseException] = []
        for session_id, maintenance, lease in owned:
            try:
                # Skipped Sessions park an unacquired lease here; releasing it is a
                # no-op, so do not pay a thread hop per stored Session for it.
                if lease.held:
                    await asyncio.to_thread(lease.release)
            except BaseException as exc:
                errors.append(exc)
            finally:
                await self._release_maintenance(session_id, maintenance)
        async with self._registry_lock:
            for session_id, entry in pinned:
                if self._sessions.get(session_id) is entry:
                    entry.maintenance_pins -= 1
        try:
            await asyncio.to_thread(cleanup_lease.release)
        except BaseException as exc:
            errors.append(exc)
        for session_id, _entry in pinned:
            await self._evict_if_idle(session_id, propagate=False)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to release process output cleanup ownership", errors)

    async def _release_maintenance(self, session_id: str, maintenance: asyncio.Event) -> None:
        async with self._registry_lock:
            if self._maintenance_sessions.get(session_id) is maintenance:
                del self._maintenance_sessions[session_id]
            maintenance.set()

    def _discard(self, session_id: str) -> None:
        UnifiedSessionStore(self._storage_root, session_id).delete()

    def _guard_open(self) -> None:
        if self._closed:
            raise RuntimeError("The Unified Harness session backend host is closed")


def create_harness_host(
    storage_root: str | os.PathLike[str] | None = None,
) -> UnifiedHarnessSessionBackendHost:
    return UnifiedHarnessSessionBackendHost(
        Path(storage_root).expanduser().resolve() if storage_root is not None else None
    )


async def _maintenance_io[ResultT](call: Callable[..., ResultT], *args: object) -> ResultT:
    task = asyncio.create_task(asyncio.to_thread(call, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise


def _snapshot(projection: ProjectionStateV1, history_limit: int) -> SessionSnapshot:
    state = with_session_preview(projection.snapshot)
    history = state.history
    entries = history.entries[-history_limit:] if history_limit else []
    return SessionSnapshot(
        state=state.model_copy(
            update={"history": LatestPublicHistoryPage(entries=entries, cursor=history.cursor)}
        ),
        history_limit=history_limit,
        watermark=projection.watermark,
    )


def _validate_child_runtime_state(
    state: RuntimeStateV3,
    *,
    identity: SubagentSessionIdentity,
    binding: LocalChildSessionBinding,
    spawn_key: str,
) -> None:
    metadata = state.session_metadata
    if state.identity != identity:
        raise ValueError("child Session identity conflicts with its parent record")
    if metadata.subagent_spawn_key != spawn_key:
        raise ValueError("child Session spawn key conflicts with its parent record")
    if (
        metadata.subagent_template_digest != binding.template_digest
        or metadata.subagent_policy_ceiling_digest != binding.policy_ceiling_digest
    ):
        raise ValueError("child Session binding conflicts with its parent record")


def _tree_child_session_ids(state: RuntimeStateV3) -> list[str]:
    if state.subagents is None:
        return []
    return sorted({child.child_session_id for child in state.subagents.children.values()})


def _tree_deletion_plan(state: RuntimeStateV3) -> DeletingSessionTree | None:
    """The deletion order to record before removing this root, if it needs one.

    A root with children publishes one so a crash resumes where it stopped. A
    childless root has nothing to resume -- its store goes in a single `rmtree`
    -- and the plan's only reader is the deletion loop, so recording it would
    put the whole ledger through a generation write purely to delete it. That
    write answers to the document cap, which is the one thing a Session too
    large to republish must not have to pass on its way out.
    """
    if isinstance(state.lifecycle, DeletingSessionTree):
        return None
    children = _tree_child_session_ids(state)
    if not children:
        return None
    return DeletingSessionTree(
        ordered_child_session_ids=children,
        deleted_child_session_ids=[],
    )


def _child_turn_outcome(
    state: PublicSessionState,
    generation: int,
    turn_id: str,
    *,
    retryable_failure: bool | None = None,
) -> ChildTurnOutcome:
    turn = state.latest_turn
    if turn is None or turn.id != turn_id:
        raise RuntimeError("child Session does not retain the requested generation")
    if isinstance(turn, CompletedPublicTurn):
        output, final_answer = _assistant_output(state, turn_id)
        return CompletedChildTurnOutcome(
            generation=generation,
            turn_id=turn_id,
            completed_at_unix_ms=turn.completed_at,
            output=output,
            final_answer=final_answer,
        )
    if isinstance(turn, FailedPublicTurn):
        return FailedChildTurnOutcome(
            generation=generation,
            turn_id=turn_id,
            completed_at_unix_ms=turn.completed_at,
            failure=SubagentFailure(
                code=turn.error.code or "subagent_turn_failed",
                message=turn.error.message,
                retryable=retryable_failure or False,
            ),
        )
    if isinstance(turn, InterruptedPublicTurn):
        return InterruptedChildTurnOutcome(
            generation=generation,
            turn_id=turn_id,
            completed_at_unix_ms=turn.completed_at,
            reason=turn.reason or "interrupted by parent",
        )
    raise RuntimeError("child Turn is still running")


def _terminal_failure_retryability(checkpoint: dict[str, JsonValue], turn_id: str) -> bool | None:
    turn = checkpoint.get("turn")
    if not isinstance(turn, dict):
        return None
    if turn.get("type") != "terminal" or turn.get("turn_id") != turn_id:
        return None
    outcome = turn.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("type") != "failed":
        return None
    error = outcome.get("error")
    if not isinstance(error, dict):
        return None
    retryable = error.get("retryable")
    return retryable if isinstance(retryable, bool) else None


def _assistant_output(state: PublicSessionState, turn_id: str) -> tuple[list[JsonValue], str]:
    for entry in reversed(state.history.entries):
        if entry.get("type") != "message":
            continue
        if entry.get("turnId") != turn_id or entry.get("role") != "assistant":
            continue
        raw_content = entry.get("content")
        if not isinstance(raw_content, list):
            break
        output = cast(list[JsonValue], raw_content)
        text_parts: list[str] = []
        for block in output:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        return output, "".join(text_parts)
    return [], ""


def _public_state(
    session_id: str,
    created_at: int,
    history: list[InteropHistoryMessageV1],
    metadata: SessionMetadataV1,
) -> PublicSessionState:
    entries: list[JsonObject] = []
    for index, message in enumerate(history):
        if isinstance(message, InteropToolMessageV1):
            continue
        if isinstance(message, InteropAssistantMessageV1):
            content = [
                cast(
                    JsonValue,
                    _without_file_image_fallback(
                        cast(
                            JsonValue,
                            part.content.model_dump(mode="json", by_alias=True, exclude_none=True),
                        )
                    ),
                )
                for part in message.parts
                if isinstance(part, InteropAssistantContentPartV1)
            ]
        else:
            content = [
                cast(
                    JsonValue,
                    _without_interop_metadata(
                        cast(
                            JsonValue,
                            _without_file_image_fallback(
                                cast(
                                    JsonValue,
                                    part.model_dump(mode="json", by_alias=True, exclude_none=True),
                                )
                            ),
                        )
                    ),
                )
                for part in message.content
            ]
        if not content:
            continue
        entries.append(
            {
                "type": "message",
                "id": _imported_entry_id(index),
                "sessionId": session_id,
                "turnId": None,
                "createdAt": created_at,
                "updatedAt": created_at,
                "generationStatus": "completed",
                "relatedEntryId": None,
                "role": message.role,
                "content": content,
                "source": "harness",
                **({"outcome": {"type": "committed"}} if message.role == "assistant" else {}),
            }
        )
    return PublicSessionState(
        session=PublicSession(
            id=session_id,
            root_session_id=metadata.root_session_id,
            parent_session_id=metadata.parent_session_id,
            status=IdleSessionStatus(),
            created_at=created_at,
            updated_at=created_at,
        ),
        history=LatestPublicHistoryPage(
            entries=entries,
            cursor=HistoryCursor(),
        ),
        turn_queue=TurnQueue(items=[], paused=False, max_items=TURN_QUEUE_MAX_ITEMS),
    )


def _rehome_public_history(history: Sequence[JsonObject], session_id: str) -> list[JsonObject]:
    return [cast(JsonObject, {**entry, "sessionId": session_id}) for entry in history]


def _create_checkpoint(
    session_id: str,
    history: list[InteropHistoryMessageV1],
    config: RustHarnessConfig | None = None,
    *,
    attachments_root: Path | None = None,
    keep_interop_metadata: bool = False,
) -> dict[str, JsonValue]:
    """Build a Core around a transcript it did not record itself.

    History arriving from elsewhere is handed over without its Runtime
    metadata: the client message ids in it name entries of the session it was
    written in, and a session importing it publishes the turns under ids of its
    own. A rewind is the exception -- the transcript is the session's own, cut
    short, and the entries it keeps are still published under the ids recorded
    in it -- so it asks to keep the metadata rather than lose the ids the
    conversation it goes on being is addressed by.
    """
    core_history: list[JsonValue] = []
    for message in history:
        value = message.model_dump(mode="json", by_alias=True, exclude={"content", "parts"})
        if isinstance(message, InteropAssistantMessageV1):
            value["content"] = [
                (
                    _core_content_block(
                        part.content,
                        attachments_root=attachments_root,
                        keep_interop_metadata=keep_interop_metadata,
                    )
                    if isinstance(part, InteropAssistantContentPartV1)
                    else (
                        cast(
                            dict[str, JsonValue],
                            part.model_dump(mode="json", by_alias=True),
                        )
                        if keep_interop_metadata
                        else cast(
                            dict[str, JsonValue],
                            _without_interop_metadata(
                                cast(
                                    JsonValue,
                                    part.model_dump(mode="json", by_alias=True),
                                )
                            ),
                        )
                    )
                )
                for part in message.parts
            ]
        else:
            value["content"] = [
                _core_content_block(
                    part,
                    attachments_root=attachments_root,
                    keep_interop_metadata=keep_interop_metadata,
                )
                for part in message.content
            ]
        if not keep_interop_metadata:
            value.pop("_meta", None)
        core_history.append(cast(JsonValue, value))
    core = HarnessSession.create(
        (config or _core_config(session_id)).model_dump_json(by_alias=True),
        canonical_json(core_history).decode(),
    )
    try:
        return cast(dict[str, JsonValue], json.loads(core.checkpoint()))
    finally:
        core.close()


def _core_content_block(
    block: object,
    *,
    attachments_root: Path | None,
    keep_interop_metadata: bool,
) -> dict[str, JsonValue]:
    if isinstance(block, InteropImageContentBlockV1):
        image = RustImageContentBlock(
            data=block.data,
            mime_type=block.mime_type,
            annotations=block.annotations,
            _meta=block.meta if keep_interop_metadata else None,
        )
        if block.file_fallback is not None:
            if attachments_root is None:
                raise ValueError("Imported file image has no destination attachments directory")
            image = materialize_file_image_fallback(
                image,
                name=block.file_fallback.name,
                attachments_root=attachments_root,
                keep_meta=keep_interop_metadata,
            )
        return cast(
            dict[str, JsonValue],
            image.model_dump(mode="json", by_alias=True, exclude_none=True),
        )

    value = cast(
        JsonValue,
        cast(Any, block).model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    projected = value if keep_interop_metadata else _without_interop_metadata(value)
    return cast(dict[str, JsonValue], projected)


def _history_for_fork(
    history: list[InteropHistoryMessageV1], entry_id: str | None, *, include_entry: bool
) -> list[InteropHistoryMessageV1]:
    if entry_id is None:
        return list(history)

    anchor = _history_user_anchor(history, entry_id)
    if not include_entry:
        return list(history[:anchor])

    end = next(
        (
            index
            for index, message in enumerate(history[anchor + 1 :], start=anchor + 1)
            if message.role == "user"
        ),
        len(history),
    )
    return list(history[:end])


def _history_user_anchor(history: Sequence[InteropHistoryMessageV1], entry_id: str) -> int:
    anchor = next(
        (
            index
            for index, message in enumerate(history)
            if message.role == "user"
            and any(
                part.meta is not None and part.meta.get("vibe_client_message_id") == entry_id
                for part in message.content
            )
        ),
        None,
    )
    if anchor is None:
        anchor = _imported_history_anchor(history, entry_id)
    if anchor is None:
        raise ValueError(f"Cannot find user entry: {entry_id}")
    return anchor


def _imported_history_anchor(
    history: Sequence[InteropHistoryMessageV1], entry_id: str
) -> int | None:
    """Resolve an id minted for imported history back to its history position.

    Imported history is fed to Core without its Runtime metadata, so the client
    message ids the lookup above wants do not survive the import: `_public_state`
    numbers those entries by position instead. Reading that numbering backwards
    is what lets a forked session be rewound onto a turn it inherited rather
    than one it recorded itself.
    """
    suffix = entry_id.removeprefix(_IMPORTED_ENTRY_ID_PREFIX)
    if suffix == entry_id or not suffix.isdigit():
        return None
    index = int(suffix) - 1
    if not 0 <= index < len(history) or _imported_entry_id(index) != entry_id:
        return None
    return index if history[index].role == "user" else None


def _imported_entry_id(index: int) -> str:
    return f"{_IMPORTED_ENTRY_ID_PREFIX}{index + 1}"


def _without_interop_metadata(value: JsonValue) -> JsonValue:
    """Remove Runtime-only metadata before feeding imported history to Core."""
    if isinstance(value, list):
        return [_without_interop_metadata(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _without_interop_metadata(item) for key, item in value.items() if key != "_meta"
        }
    return value


def _without_file_image_fallback(value: JsonValue) -> JsonValue:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if key != "file_fallback"}


def _integration_tool_groups(
    base: RustHarnessCapabilitySet,
    *,
    mcp_snapshot: MCPRouteSnapshot | None,
    connector_snapshot: ConnectorRouteSnapshot | None,
) -> list[RustToolGroupDefinition]:
    mcp_groups = (
        [
            RustToolGroupDefinition(
                name=group.name,
                description=group.description,
                tools=[
                    RustProvidedToolDefinition(
                        name=tool.programmatic_name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                        output_schema=tool.output_schema,
                        exposure="programmatic",
                    )
                    for tool in group.tools
                ],
            )
            for group in mcp_snapshot.groups
        ]
        if mcp_snapshot is not None
        else []
    )
    connector_groups = (
        [
            RustToolGroupDefinition(
                name=group.name,
                description=group.description,
                tools=[
                    RustProvidedToolDefinition(
                        name=tool.programmatic_name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                        output_schema=None,
                        exposure="programmatic",
                    )
                    for tool in group.tools
                ],
            )
            for group in connector_snapshot.groups
        ]
        if connector_snapshot is not None
        else []
    )
    return [*base.tool_groups, *mcp_groups, *connector_groups]


def _merge_integration_capabilities(
    base: RustHarnessCapabilitySet,
    *,
    mcp_snapshot: MCPRouteSnapshot | None,
    connector_snapshot: ConnectorRouteSnapshot | None,
) -> RustHarnessCapabilitySet:
    return base.model_copy(
        update={
            "tool_groups": _integration_tool_groups(
                base, mcp_snapshot=mcp_snapshot, connector_snapshot=connector_snapshot
            )
        },
        deep=True,
    )


def _integration_capability_update(
    base: RustHarnessCapabilitySet,
    *,
    mcp_snapshot: MCPRouteSnapshot | None,
    connector_snapshot: ConnectorRouteSnapshot | None,
) -> Callable[[RustHarnessCapabilitySet], RustHarnessCapabilitySet]:
    groups = _integration_tool_groups(
        base, mcp_snapshot=mcp_snapshot, connector_snapshot=connector_snapshot
    )

    def update(live: RustHarnessCapabilitySet) -> RustHarnessCapabilitySet:
        return live.model_copy(update={"tool_groups": groups}, deep=True)

    return update


def _has_provided_tool(
    capabilities: RustHarnessCapabilitySet,
    *,
    group_name: str,
    tool_name: str,
) -> bool:
    return any(
        group.name == group_name and any(tool.name == tool_name for tool in group.tools)
        for group in capabilities.tool_groups
    )


@dataclass(frozen=True, slots=True)
class _ProvidedToolGroup:
    factory: ProvidedToolExecutorFactory
    # ``None`` keeps ``config.provided_tool_mode``.
    mode: ProvidedToolApproval | None


def _registered_group_names(
    groups: Mapping[str, _ProvidedToolGroup], capabilities: RustHarnessCapabilitySet
) -> frozenset[str]:
    return frozenset(group.name for group in capabilities.tool_groups if group.name in groups)


def _session_provided_tool_modes(
    groups: Mapping[str, _ProvidedToolGroup], names: frozenset[str]
) -> dict[str, ProvidedToolApproval]:
    return {name: mode for name in names if (mode := groups[name].mode) is not None}


def _session_provided_tool_executors(
    groups: Mapping[str, _ProvidedToolGroup], names: frozenset[str], session_id: str
) -> dict[str, ProvidedToolExecutor]:
    # Keyed on the factory, not the name, so groups sharing a factory share one
    # executor and so one piece of per-session state.
    built: dict[int, ProvidedToolExecutor] = {}
    executors: dict[str, ProvidedToolExecutor] = {}
    for name in sorted(names):
        factory = groups[name].factory
        executor = built.get(id(factory))
        if executor is None:
            executor = factory(session_id)
            built[id(factory)] = executor
        executors[name] = executor
    return executors


def _core_config(session_id: str) -> RustHarnessConfig:
    return RustHarnessConfig(
        task_id=session_id,
        settings=RustHarnessSettings(
            turn=RustTurnSettings(max_iterations=25),
            context=RustContextSettings(compaction=RustDisabledCompactionPolicy()),
            tools=RustToolSettings(
                programmatic=RustProgrammaticToolSettings(max_effects=128, max_operations=1024),
                subagents=RustDisabledRuntimeToolFeature(),
                background_processes=RustDisabledRuntimeToolFeature(),
                command_environment=RustUnixCommandEnvironment(),
                large_output=RustDisabledLargeOutputPolicy(),
            ),
        ),
    )


def _default_storage_root() -> Path:
    configured = os.environ.get("MISTRAL_VIBE_SESSION_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".vibe" / "logs" / "session").resolve()


def _stored_sessions_with_process_output(
    unified_root: Path, session_ids: tuple[str, ...]
) -> frozenset[str]:
    """Session IDs that have a ``processes`` entry, one ``lstat`` apiece.

    ``lexists`` rather than ``is_dir`` so a symlink or a regular file still routes
    the Session down the full path, where ``_require_real_directory`` and
    ``_require_safe_process_path`` reject it exactly as they do today.
    """
    return frozenset(
        session_id
        for session_id in session_ids
        if os.path.lexists(unified_root / session_id / "processes")
    )


def _stored_session_ids(unified_root: Path) -> tuple[str, ...]:
    if not unified_root.is_dir() or unified_root.is_symlink():
        return ()
    return tuple(
        sorted(
            path.name
            for path in unified_root.iterdir()
            if path.is_dir() and not path.is_symlink() and (path / "CURRENT").is_file()
        )
    )


def _now_milliseconds() -> int:
    return int(time.time() * 1000)


def _timestamp() -> str:
    milliseconds = _now_milliseconds()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(milliseconds / 1000)) + (
        f".{milliseconds % 1000:03d}Z"
    )


__all__ = [
    "HarnessSessionForkResult",
    "HarnessSessionListItem",
    "HarnessSessionListResult",
    "HarnessSessionReadResult",
    "LegacyImportSource",
    "LegacySessionReference",
    "LegacySourceLoader",
    "LegacySourceResolver",
    "UnifiedHarnessSessionBackendHost",
    "create_harness_host",
]
