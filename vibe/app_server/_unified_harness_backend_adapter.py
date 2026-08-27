"""Translation between the Vibe app-server port and the Unified Harness."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Never, cast

from mistralai.vibe.harness.app_server.session.models import (  # pyright: ignore[reportMissingImports]
    ResolvedPluginDefinition,
)
from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
    RustHarnessConfig,
)

# `mistralai-local-harness` is an optional extra, so an environment that never
# installs it — CI's type-check job included — cannot resolve these.
from mistralai_rust_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
    PublicSession as HarnessPublicSession,
    PublicSessionState as HarnessPublicSessionState,
    SessionReadParams as HarnessSessionReadParams,
    SessionSnapshot as HarnessSessionSnapshot,
    SessionStartParams as HarnessSessionStartParams,
)
from mistralai_rust_harness.vibe import (  # pyright: ignore[reportMissingImports]
    CompiledHooks,
    ConnectorGatewayClient as HarnessConnectorGatewayClient,
    ConnectorRouteSnapshot as HarnessConnectorRouteSnapshot,
    HarnessNotImplementedError,
    HarnessSessionError,
    HarnessSessionNotFoundError,
    HarnessSessionSubscription,
    LegacySourceLoader,
    LegacySourceResolver,
    LocalRuntimeAdapterConfig,
    MCPAuthorizationProvider as HarnessMCPAuthorizationProvider,
    MCPAuthorizationRef as HarnessMCPAuthorizationRef,
    MCPAuthorizationRequired as HarnessMCPAuthorizationRequired,
    MCPAuthorizationSnapshot as HarnessMCPAuthorizationSnapshot,
    MCPHTTPTransportPolicy as HarnessMCPHTTPTransportPolicy,
    MCPRouteSnapshot as HarnessMCPRouteSnapshot,
    ResolvedConnector as HarnessResolvedConnector,
    ResolvedConnectorCatalog as HarnessResolvedConnectorCatalog,
    ResolvedConnectorSelection as HarnessResolvedConnectorSelection,
    ResolvedConnectorSetting as HarnessResolvedConnectorSetting,
    ResolvedConnectorTool as HarnessResolvedConnectorTool,
    ResolvedMCPCatalog as HarnessResolvedMCPCatalog,
    ResolvedMCPServerConfig as HarnessResolvedMCPServerConfig,
    UnifiedHarnessSessionBackend,
    UnifiedHarnessSessionBackendHost,
)
from mistralai_rust_harness.vibe.plugins import (  # pyright: ignore[reportMissingImports]
    SessionPluginBinding,
)

from vibe.app_server._admin_config import (
    refresh_admin_layer,
    report_admin_config_outcome,
)
from vibe.app_server._config_introspect import (
    HIDDEN_SETTINGS,
    POPULAR_SETTINGS,
    build_field_wires,
    collect_layer_values,
)
from vibe.app_server._config_write import config_write_ops_to_patches
from vibe.app_server._dispatch import DispatchResult, method_not_found
from vibe.app_server._host import config_schema_response
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._plugins import (
    PluginReloadUnavailableError,
    SessionPlugins,
    UnifiedPluginProvider,
    plugin_reload_notices,
)
from vibe.app_server._projection import project_config_view
from vibe.app_server._session_backend_port import (
    ConnectorAuthRequest,
    MCPAuthorizationProvider,
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationSnapshot,
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
from vibe.app_server._state import history_page
from vibe.app_server._tool_projection import project_effect_output_value
from vibe.app_server._workspace import (
    PromptPreparationError,
    mentioned_file_content_blocks_async,
    prepare_prompt_from_context,
)
from vibe.app_server.config import ProxySettingsView
from vibe.app_server.events import (
    CallbackRequested,
    ConnectorAuthorizationRequiredEvent,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    MCPAuthorizationRequiredEvent,
    ServerWarning,
    SessionSnapshot,
    SessionUpdated,
    StatsUpdated,
    TurnCompleted,
    TurnStarted,
    reconcile_snapshot,
)
from vibe.app_server.models import (
    AccountStatus,
    AccountView,
    AgentStatsSnapshot,
    BlockedSessionStatus as VibeBlockedSessionStatus,
    CompletedEffectState,
    ConnectorCounts,
    ContentBlock,
    FailedSessionStatus as VibeFailedSessionStatus,
    IdleSessionStatus as VibeIdleSessionStatus,
    JsonPatchOperation,
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
    PluginInfo,
    PreparedPrompt,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    RunningSessionStatus as VibeRunningSessionStatus,
    SessionLogSummary,
    SkillEffectDetail,
    TextContentBlock,
    TokenUsage as VibeTokenUsage,
    TurnErrorCode,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    AccountReadResponse,
    AgentSwitchParams,
    CallbackResult,
    CallbackResultError,
    CallbackResultParams,
    CallbackResultResponse,
    ConfigFieldsReadParams,
    ConfigFieldsReadResponse,
    ConfigMutationResponse,
    ConfigProxyReadParams,
    ConfigProxyReadResponse,
    ConfigProxyWriteParams,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigReloadParams,
    ConfigSchemaReadParams,
    ConfigWriteParams,
    ConfigWriteResponse,
    ConnectorAuthRequiredParams,
    ContextInjectParams,
    ContextInjectResponse,
    EmptyResponse,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    HistoryEntryAddedParams,
    HistoryEntryUpdatedParams,
    IdentityReadResponse,
    MCPAuthRequiredParams,
    PageRequest,
    PluginInfoParams,
    PluginInfoResponse,
    PluginReloadParams,
    PluginReloadResponse,
    ProtocolErrorCode,
    RuntimeMutationResponse,
    RuntimeReadParams,
    RuntimeReadResponse,
    RuntimeSnapshot,
    RuntimeUpdatedParams,
    ServerWarningParams,
    SessionContinueParams,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionKind,
    SessionListParams,
    SessionListResponse,
    SessionLogReadParams,
    SessionLogReadResponse,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionReadyReadResponse,
    SessionReadyWaitResponse,
    SessionResumeParams,
    SessionSettingsUpdateParams,
    SessionStartParams,
    SessionStopParams,
    SessionStopResponse,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    SessionUpdatedParams,
    SkillsListParams,
    SkillsListResponse,
    StatsUpdatedParams,
    TurnCompletedParams,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnStartedParams,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
    WorkspacePromptPrepareParams,
    WorkspacePromptPrepareResponse,
)
from vibe.core.agents.manager import AgentManager
from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import MANAGED_CONFIG_TIMEOUT
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator, ConfigPatchValidationError
from vibe.core.hooks.config import load_hooks_from_fs
from vibe.core.proxy_setup import (
    SUPPORTED_PROXY_VARS,
    ProxySetupError,
    get_current_proxy_settings,
    set_proxy_var,
    unset_proxy_var,
)
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import SkillSource
from vibe.core.tools.builtins.skill import already_loaded_message, skill_content_marker
from vibe.observability.logging import logger


@dataclass(frozen=True, slots=True)
class UnifiedSessionSettings:
    """Session-local overrides that never reach a persisted config layer."""

    max_turns: int | None = None
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class UnifiedRuntimeDerivation:
    """Everything the Unified Harness needs, derived from the layered config.

    Recomputed from scratch after every config mutation, so nothing here may be
    cached across a write, a reload or an agent switch.
    """

    runtime: RuntimeSnapshot
    core_config: RustHarnessConfig
    adapter_config: LocalRuntimeAdapterConfig


type UnifiedRuntimeDeriver = Callable[
    [UnifiedSessionSettings], UnifiedRuntimeDerivation
]


@dataclass(frozen=True, slots=True)
class UnifiedSessionContext:
    """The live configuration state backing one Unified Harness session.

    The orchestrator is the session's source of truth and outlives every
    derivation: mutations land on it, then ``derive`` projects the result back
    into the Harness.
    """

    storage_root: str
    legacy_source_loader: LegacySourceLoader
    legacy_source_resolver: LegacySourceResolver
    # Resolved once by an async call at session open, so they are pinned for
    # the session and cannot be recomputed by the synchronous ``derive``.
    plugins: SessionPlugins
    """The Host's own resolve: it reports issues and supplies the environment."""

    plugin_provider: UnifiedPluginProvider
    """The seam. Only its ``bind`` reaches the Core."""

    requested_plugins: tuple[ResolvedPluginDefinition, ...]
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    harness_files: HarnessFilesManager
    agents: AgentManager
    derive: UnifiedRuntimeDeriver
    mcp_catalog: ResolvedMCPCatalog
    mcp_authorization_provider: MCPAuthorizationProvider
    mcp_cache_root: str
    mcp_enable_system_trust_store: bool
    connector_catalog: ResolvedConnectorCatalog | None = None
    connector_selection: ResolvedConnectorSelection | None = None
    connector_base_url: str = "https://api.mistral.ai"
    connector_api_key: str = ""
    # Compiled user hooks for this session: Core bindings + Runtime handlers. The
    # Host registers the handlers on every lifecycle op (so a hook's command is
    # re-read live) and the bindings are supplied on start (then persisted, so the
    # set that fires stays frozen for the session's life).
    hooks: CompiledHooks = field(default_factory=CompiledHooks)


type SessionContextBuilder = Callable[
    [SessionOptions], Awaitable[UnifiedSessionContext]
]

# The one write path pinning has: reload rescans and hands the result to the
# same `config/write` the Host uses at `session/start`. Unbound, so nothing has
# to close over a session id that does not exist until the adapter is built.
type PluginRewrite = Callable[
    [str, Sequence[ResolvedPluginDefinition]], Awaitable[SessionPluginBinding]
]


def adapt_harness_host(
    host: object, build_session_context: SessionContextBuilder
) -> UnifiedHarnessBackendHostAdapter:
    return UnifiedHarnessBackendHostAdapter(
        cast(UnifiedHarnessSessionBackendHost, host), build_session_context
    )


class UnifiedHarnessBackendHostAdapter:
    """Vibe's session Host backed by the Unified Harness Runtime."""

    def __init__(
        self,
        host: UnifiedHarnessSessionBackendHost,
        build_context: SessionContextBuilder,
    ) -> None:
        self._host = host
        self._build_context = build_context

    @property
    def harness_kind(self) -> SessionBackendKind:
        return self._host.harness_kind

    async def start(self, params: SessionStartParams) -> SessionLifecycleResult:
        options = params.agent_config
        context, derivation = await self._context(options)
        session = await _harness_call(
            self._host.start(
                HarnessSessionStartParams(history_limit=params.history_limit),
                cwd=_session_cwd(options),
                ephemeral=params.kind is SessionKind.EPHEMERAL,
                hook_bindings=context.hooks.bindings,
                hook_handlers=context.hooks.handlers,
            )
        )
        backend = UnifiedHarnessBackendAdapter(
            session,
            _session_cwd(options),
            context,
            derivation,
            rewrite_plugins=self._host.rewrite_session_plugins,
        )
        backend._update_connector_projection(await backend.read_connectors())
        return SessionLifecycleResult(backend=backend)

    async def resume(self, params: SessionResumeParams) -> SessionLifecycleResult:
        context, derivation = await self._context(params.agent_config)
        context, derivation = await self._pin_to_session_cwd(
            params.agent_config, params.session_id, context, derivation
        )
        session = await _harness_call(
            self._host.resume(
                params.session_id,
                history_limit=params.history_limit,
                hook_bindings=context.hooks.bindings,
                hook_handlers=context.hooks.handlers,
            )
        )
        backend = UnifiedHarnessBackendAdapter(
            session,
            session.cwd,
            context,
            derivation,
            rewrite_plugins=self._host.rewrite_session_plugins,
        )
        backend._update_connector_projection(await backend.read_connectors())
        return SessionLifecycleResult(backend=backend)

    async def continue_latest(
        self, params: SessionContinueParams
    ) -> SessionLifecycleResult:
        # Resolve the latest session once and resume it by id, rather than pinning
        # cwd/hooks off one listing and letting Host.continue_latest list again: a
        # session created between the two lists would otherwise be resumed with hooks
        # compiled for a different project's cwd (and a clean rebind would persist them
        # on the wrong session). The first _context configures the Host (storage/legacy),
        # which list and session_cwd need.
        context, derivation = await self._context(params.agent_config)
        listing = await _harness_call(self._host.list(limit=1))
        target_id = listing.continue_session_id
        if target_id is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, "No session to continue"
            )
        context, derivation = await self._pin_to_session_cwd(
            params.agent_config, target_id, context, derivation
        )
        session = await _harness_call(
            self._host.resume(
                target_id,
                history_limit=params.history_limit,
                hook_bindings=context.hooks.bindings,
                hook_handlers=context.hooks.handlers,
            )
        )
        backend = UnifiedHarnessBackendAdapter(
            session,
            session.cwd,
            context,
            derivation,
            rewrite_plugins=self._host.rewrite_session_plugins,
        )
        backend._update_connector_projection(await backend.read_connectors())
        return SessionLifecycleResult(backend=backend)

    async def fork(self, params: SessionForkParams) -> SessionForkResult:
        options = params.agent_config or SessionOptions()
        context, derivation = await self._context(options)
        context, derivation = await self._pin_to_session_cwd(
            options, params.source_session_id, context, derivation
        )
        result = await _harness_call(
            self._host.fork(
                params.source_session_id,
                history_limit=params.history_limit,
                hook_handlers=context.hooks.handlers,
            )
        )
        backend = UnifiedHarnessBackendAdapter(
            result.session,
            result.session.cwd,
            context,
            derivation,
            rewrite_plugins=self._host.rewrite_session_plugins,
        )
        backend._update_connector_projection(await backend.read_connectors())
        snapshot = await backend.read(
            SessionReadParams(
                session_id=backend.session_id,
                history=PageRequest(limit=params.history_limit),
            )
        )
        attached: UnifiedHarnessBackendAdapter | None = backend
        if not params.attach:
            await backend.shutdown()
            attached = None
        return SessionForkResult(
            response=SessionForkResponse(
                source_session_id=params.source_session_id,
                state=snapshot.state,
                last_event_id=snapshot.last_event_id,
            ),
            backend=attached,
        )

    async def list(self, params: SessionListParams) -> SessionListResponse:
        options = SessionOptions(cwd=params.cwd)
        await self._context(options)
        result = await _harness_call(
            self._host.list(
                limit=params.limit,
                cursor=params.cursor,
                cwd=_session_cwd(options) if params.cwd is not None else None,
                root_session_id=params.root_session_id,
                parent_session_id=params.parent_session_id,
            )
        )
        return SessionListResponse(
            items=[_public_session(item.session, item.cwd) for item in result.items],
            next_cursor=result.next_cursor,
            previous_cursor=result.previous_cursor,
            continue_session_id=result.continue_session_id,
        )

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        options = SessionOptions()
        await self._context(options)
        result = await _harness_call(self._host.read(_harness_read_params(params)))
        return _read_response(result.snapshot, result.cwd)

    async def shutdown(self) -> None:
        await self._host.shutdown()

    async def _pin_to_session_cwd(
        self,
        options: SessionOptions,
        session_id: str,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation]:
        """Rebuild the context against an existing session's stored cwd, if it differs.

        The caller must have already built ``context`` once (which configures the Host's
        storage/legacy resolver — what ``session_cwd`` needs). If the session was created
        in a different cwd than the caller's, rebuild so hooks are discovered and compiled
        against the session's own cwd; binding ids embed it, so a resume/fork otherwise
        skips every persisted hook or binds the wrong project's hooks (§7 as-built).
        """
        stored_cwd = await _harness_call(self._host.session_cwd(session_id))
        if stored_cwd is None or stored_cwd == _session_cwd(options):
            return context, derivation
        if options.trust_workspace:
            # The first build recorded an ephemeral --trust grant for the caller cwd on
            # the process-wide trust store. Its ancestor walk would still trust the pinned
            # (often descendant) session cwd, so revoke that one grant before rebuilding.
            context.harness_files.trust_store.revoke_session_trust(
                Path(_session_cwd(options))
            )
        return await self._context(_with_session_cwd(options, stored_cwd))

    async def _context(
        self, options: SessionOptions
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation]:
        context = await self._build_context(options)
        derivation = await asyncio.to_thread(context.derive, UnifiedSessionSettings())
        connector_catalog = context.connector_catalog or ResolvedConnectorCatalog(
            provider_fingerprint="", revision="", connectors=()
        )
        connector_selection = context.connector_selection or ResolvedConnectorSelection(
            selection_revision="",
            enable_connectors=False,
            implicit_source_enabled=True,
            connector_settings=(),
            enabled_tools=(),
            disabled_tools=(),
        )
        self._host.configure_storage(context.storage_root)
        self._host.configure_legacy_source_loader(context.legacy_source_loader)
        self._host.configure_legacy_source_resolver(context.legacy_source_resolver)
        self._host.configure_runtime(
            derivation.core_config, adapter_config=derivation.adapter_config
        )
        self._host.configure_plugins(context.plugin_provider, context.requested_plugins)
        # The session's compiled foreign handlers are passed per-lifecycle-op
        # (start/resume/continue/fork take hook_handlers=), so we deliberately do NOT
        # set them on the Host-global registry here. The Host merges the per-session
        # foreign handlers with any Host-global builtins at bind time
        # (merge_hook_handlers), keeping the global registry reserved for builtins.
        self._host.configure_mcp(
            _harness_mcp_catalog(context.mcp_catalog),
            _HarnessMCPAuthorizationProviderAdapter(context.mcp_authorization_provider),
            cache_root=context.mcp_cache_root,
            http_transport_policy=HarnessMCPHTTPTransportPolicy(
                enable_system_trust_store=context.mcp_enable_system_trust_store
            ),
        )
        self._host.configure_connectors(
            _harness_connector_catalog(connector_catalog),
            _harness_connector_selection(connector_selection),
            lambda: HarnessConnectorGatewayClient(
                base_url=context.connector_base_url,
                api_key=context.connector_api_key,
                enable_system_trust_store=context.mcp_enable_system_trust_store,
            ),
        )
        return context, derivation


class _UnifiedHarnessMCPAdapter:
    _session: UnifiedHarnessSessionBackend
    _runtime: RuntimeSnapshot
    _context: UnifiedSessionContext

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def mcp_config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        return self._context.config_orchestrator

    async def read_mcp(self) -> SessionMCPState:
        return _session_mcp_state(
            await _harness_call(self._session.read_mcp()), self.mcp_config_orchestrator
        )

    async def reconfigure_mcp(
        self, configuration: ResolvedMCPCatalog, *, force_remote_discovery: bool
    ) -> SessionMCPState:
        snapshot = await _harness_call(
            self._session.reconfigure_mcp(
                _harness_mcp_catalog(configuration),
                force_remote_discovery=force_remote_discovery,
            )
        )
        return _session_mcp_state(snapshot, self.mcp_config_orchestrator)

    async def authorization_changed(
        self, *, name: str, descriptor_revision: str
    ) -> SessionMCPState:
        snapshot = await _harness_call(
            self._session.authorization_changed(
                name=name, descriptor_revision=descriptor_revision
            )
        )
        return _session_mcp_state(snapshot, self.mcp_config_orchestrator)

    async def suspend_mcp(
        self, *, name: str, tool_name: str | None, reason: str
    ) -> SessionMCPState:
        if reason not in {"logout", "remove", "disable", "replace"}:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, "Invalid MCP suspension reason"
            )
        snapshot = await _harness_call(
            self._session.suspend_mcp(name=name, tool_name=tool_name)
        )
        return _session_mcp_state(snapshot, self.mcp_config_orchestrator)

    def update_mcp_projection(self, state: MCPState) -> None:
        server_sources = [
            source for source in state.sources if source.kind is MCPSourceKind.SERVER
        ]
        connector_sources = [
            source
            for source in self._runtime.mcp.sources
            if source.kind is MCPSourceKind.CONNECTOR
        ]
        connector_names = {source.name for source in connector_sources}
        connector_discovery_errors = {
            name: error
            for name, error in self._runtime.mcp.discovery_errors.items()
            if name in connector_names
        }
        self._runtime = self._runtime.model_copy(
            update={
                "mcp": MCPState(
                    sources=[*server_sources, *connector_sources],
                    discovery_errors={
                        **state.discovery_errors,
                        **connector_discovery_errors,
                    },
                    connector_error=self._runtime.mcp.connector_error,
                )
            }
        )


class _UnifiedHarnessConnectorAdapter:
    _session: UnifiedHarnessSessionBackend
    _runtime: RuntimeSnapshot
    _context: UnifiedSessionContext
    _connector_catalog: ResolvedConnectorCatalog
    _connector_selection: ResolvedConnectorSelection

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def connector_config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        return self._context.config_orchestrator

    async def read_connectors(self) -> SessionConnectorState:
        return _session_connector_state(
            await _harness_call(self._session.read_connectors())
        )

    async def reconfigure_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        force: bool,
    ) -> SessionConnectorState:
        del force
        snapshot = await _harness_call(
            self._session.reconfigure_connectors(
                _harness_connector_catalog(catalog),
                _harness_connector_selection(selection),
            )
        )
        self._connector_catalog = catalog
        self._connector_selection = selection
        state = _session_connector_state(snapshot)
        self._update_connector_projection(state)
        return state

    async def suspend_connectors(
        self, *, name: str, tool_name: str | None, reason: str
    ) -> SessionConnectorState:
        if reason not in {"disable", "replace", "gateway_rejected"}:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, "Invalid connector suspension reason"
            )
        snapshot = await _harness_call(
            self._session.suspend_connectors(alias=name, tool_name=tool_name)
        )
        state = _session_connector_state(snapshot)
        self._update_connector_projection(state)
        return state

    async def request_connector_auth(self, *, alias: str) -> ConnectorAuthRequest:
        state = await self.read_connectors()
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
        connector = next(
            (
                item
                for item in self._connector_catalog.connectors
                if item.alias == alias
            ),
            None,
        )
        if connector is None:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "The connector catalog is not accepted"
            )
        return ConnectorAuthRequest(
            session_id=self.session_id,
            raw_connector_id=connector.raw_id,
            alias=alias,
            accepted_catalog_revision=state.accepted_catalog_revision,
            action=connector.auth_action,
            reason="needs_auth" if source.status == "needs_auth" else "needs_setup",
        )

    def _update_connector_projection(self, state: SessionConnectorState) -> None:
        connected = sum(source.status == "connected" for source in state.sources)
        connector_sources = [
            MCPSourceSummary(
                name=source.alias,
                kind=MCPSourceKind.CONNECTOR,
                transport="connector",
                status=MCPSourceStatus(source.status),
                tools=[
                    MCPToolSummary(
                        name=tool.raw_name,
                        description=tool.description or "",
                        enabled=tool.enabled,
                    )
                    for tool in source.tools
                ],
                error=source.error,
            )
            for source in state.sources
        ]
        server_sources = [
            source
            for source in self._runtime.mcp.sources
            if source.kind is MCPSourceKind.SERVER
        ]
        discovery_errors = {
            key: value
            for key, value in self._runtime.mcp.discovery_errors.items()
            if key not in {source.alias for source in state.sources}
        }
        discovery_errors.update(state.discovery_errors)
        self._runtime = self._runtime.model_copy(
            update={
                "connectors": ConnectorCounts(
                    connected=connected, total=len(state.sources)
                ),
                "mcp": MCPState(
                    sources=[*server_sources, *connector_sources],
                    discovery_errors=discovery_errors,
                    connector_error=None,
                ),
            }
        )


class UnifiedHarnessBackendAdapter(
    _UnifiedHarnessMCPAdapter, _UnifiedHarnessConnectorAdapter
):
    def __init__(
        self,
        session: UnifiedHarnessSessionBackend,
        cwd: str | None,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        rewrite_plugins: PluginRewrite | None = None,
    ) -> None:
        self._session = session
        self._cwd = cwd
        self._context = context
        self._settings = UnifiedSessionSettings()
        self._runtime = derivation.runtime
        self._skills = derivation.adapter_config.skills
        self._storage_root = context.storage_root
        self._plugins = context.plugins
        self._plugin_provider = context.plugin_provider
        self._rewrite_plugins = rewrite_plugins
        self._connector_catalog = context.connector_catalog or ResolvedConnectorCatalog(
            provider_fingerprint="", revision="", connectors=()
        )
        self._connector_selection = (
            context.connector_selection
            or ResolvedConnectorSelection(
                selection_revision="",
                enable_connectors=False,
                implicit_source_enabled=True,
                connector_settings=(),
                enabled_tools=(),
                disabled_tools=(),
            )
        )
        self._event_id = 0
        self._open_callbacks: dict[str, PublicCallbackEntry] = {}
        self._events_condition = asyncio.Condition()
        self._events_subscribed = False
        self._observed_harness_watermark = 0

    def runtime_updated_params(self) -> RuntimeUpdatedParams:
        return RuntimeUpdatedParams(session_id=self.session_id, runtime=self._runtime)

    @property
    def session_plugins(self) -> SessionPlugins:
        """What this session runs: the bound set, or the resolve it started from.

        They agree until the Runtime binds. After that the bound set is the
        only one carrying route drift and the pinned catalogue.
        """
        bound = (
            None
            if self._plugin_provider is None
            else self._plugin_provider.bound(self.session_id)
        )
        return bound if bound is not None else self._plugins

    @property
    def installed_plugin_roots(self) -> Mapping[str, Path]:
        return (
            {}
            if self._plugin_provider is None
            else self._plugin_provider.installed_roots
        )

    async def _read_plugin_info(self) -> PluginInfo:
        """Ask the Runtime what this session bound, not the Host what it resolved.

        The two agree at ``session/start`` and diverge the moment anything
        moves: a restored session runs the catalogue it pinned, a reloaded one
        whatever the rescan settled on.

        The wire model crosses the seam and is re-read here. Both runtimes
        share it, so nothing is translated — but the Runtime holds no Vibe
        types, and this is where it stops holding one.
        """
        info = await _harness_call(self._session.read_plugin_info())
        return PluginInfo.model_validate(info.model_dump(mode="json", by_alias=True))

    async def _reload_plugins(self) -> PluginReloadResponse:
        """Rescan the installed roots and re-pin whatever moved.

        Rescan here, because the resolver is Host code in this process; re-pin
        through ``config/write``, because that is the only writer of the lock
        and it already knows how to fail without breaking a running session;
        report nothing, because reload allocates no identity.

        The rescan runs even when nothing changed: that is the case which
        refreshes everything materialization owns and pinning does not — MCP
        connections, connector availability, staged files, route drift.
        """
        if self._rewrite_plugins is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "This session has no plugin writer, so there is nothing to reload.",
            )
        try:
            requested = await self._plugin_provider.rescan()
        except PluginReloadUnavailableError as error:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED, str(error)
            ) from error
        await _harness_call(self._rewrite_plugins(self.session_id, requested))
        # Past the write, so this is the new set. Adopting it keeps the
        # adapter's own reads off the resolve the session started with.
        bound = self._plugin_provider.bound(self.session_id)
        if bound is not None:
            self._plugins = bound
            for notice in plugin_reload_notices(bound):
                self._session.publish_notice(notice)
        return PluginReloadResponse()

    def _require_session(self, session_id: str) -> None:
        """Reject a request addressed to some other session.

        An adapter serves exactly one session, so a mismatched id is a client
        asking the wrong backend rather than a session that is gone.
        """
        if session_id != self.session_id:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )

    async def dispatch_extension(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "runtime/read":
                validate_wire(RuntimeReadParams, raw_params)
                response = RuntimeReadResponse(
                    runtime=self._runtime,
                    session_log=await self._session_log_summary(),
                    ready=True,
                )
            case _ if method.startswith("session/"):
                response = await self._dispatch_session_extension(method, raw_params)
            case "plugin/info":
                params = validate_wire(PluginInfoParams, raw_params)
                self._require_session(params.session_id)
                response = PluginInfoResponse(info=await self._read_plugin_info())
            case "plugin/reload":
                params = validate_wire(PluginReloadParams, raw_params)
                self._require_session(params.session_id)
                response = await self._reload_plugins()
            case "account/read":
                response = AccountReadResponse(
                    account=AccountView(status=AccountStatus.READY)
                )
            case "identity/read":
                response = IdentityReadResponse(identity=None)
            case "skills/list":
                skills_params = validate_wire(SkillsListParams, raw_params)
                self._require_session(skills_params.session_id)
                response = SkillsListResponse(skills=self._runtime.skills)
            case _ if method.startswith("config/"):
                response = await self._dispatch_config_read(method, raw_params)
            case "telemetry/record":
                response = EmptyResponse()
            case "feedback/shouldShow":
                params = validate_wire(FeedbackShouldShowParams, raw_params)
                self._require_session(params.session_id)
                response = FeedbackShouldShowResponse(show=False)
            case "workspace/prompt/prepare":
                params = validate_wire(WorkspacePromptPrepareParams, raw_params)
                self._require_session(params.session_id)
                response = await self._prepare_prompt_response(params)
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _dispatch_session_extension(
        self, method: str, raw_params: dict[str, Any]
    ) -> ProtocolModel:
        match method:
            case "session/log/read":
                params = validate_wire(SessionLogReadParams, raw_params)
                self._require_session(params.session_id)
                return SessionLogReadResponse(log=await self._session_log_summary())
            case "session/ready/wait":
                return SessionReadyWaitResponse(ready=True, init_duration_ms=0)
            case "session/ready/read":
                return SessionReadyReadResponse(ready=True)
            case "session/stop":
                params = validate_wire(SessionStopParams, raw_params)
                self._require_session(params.session_id)
                return SessionStopResponse()
            case "session/history/list":
                history_params = validate_wire(SessionHistoryListParams, raw_params)
                self._require_session(history_params.session_id)
                state = await self._read_page_state(history_params.session_id)
                backward = history_params.sort_direction == "backward"
                page = history_page(
                    state.history or [],
                    turn_id=history_params.turn_id,
                    before=history_params.cursor if backward else None,
                    after=None if backward else history_params.cursor,
                    limit=history_params.limit,
                )
                return SessionHistoryListResponse(
                    items=page.entries,
                    next_cursor=page.cursor.before if backward else page.cursor.after,
                    previous_cursor=(
                        page.cursor.after if backward else page.cursor.before
                    ),
                )
            case "session/turns/list":
                turns_params = validate_wire(SessionTurnsListParams, raw_params)
                self._require_session(turns_params.session_id)
                state = await self._read_page_state(turns_params.session_id)
                return _turns_list_response(
                    _turns_from_history(state.history or [], state.session.id),
                    turns_params,
                )
            case _:
                raise method_not_found(method)

    async def _dispatch_config_read(
        self, method: str, raw_params: dict[str, Any]
    ) -> ProtocolModel:
        """Answer the attached read side of the config surface.

        ``config/write`` and ``config/reload`` are typed backend operations and
        never reach here; everything else the settings screen needs does.
        """
        match method:
            case "config/schema":
                validate_wire(ConfigSchemaReadParams, raw_params)
                return config_schema_response()
            case "config/read":
                read_params = validate_wire(ConfigReadParams, raw_params)
                if read_params.session_id is not None:
                    self._require_session(read_params.session_id)
                return await self._config_read_response()
            case "config/fields/read":
                fields_params = validate_wire(ConfigFieldsReadParams, raw_params)
                self._require_session(fields_params.session_id)
                return await self._config_fields_response()
            case "config/proxy/read":
                proxy_read = validate_wire(ConfigProxyReadParams, raw_params)
                self._require_session(proxy_read.session_id)
                values = await asyncio.to_thread(get_current_proxy_settings)
                return ConfigProxyReadResponse(
                    settings=ProxySettingsView(
                        values=values, descriptions=SUPPORTED_PROXY_VARS
                    )
                )
            case "config/proxy/write":
                proxy_write = validate_wire(ConfigProxyWriteParams, raw_params)
                self._require_session(proxy_write.session_id)
                self._require_idle()
                await self._write_proxy_settings(proxy_write)
                return EmptyResponse()
            case _:
                raise method_not_found(method)

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        result = await self._session.read(_harness_read_params(params))
        return self._read_response(result.snapshot)

    async def subscribe(self, params: SessionReadParams) -> SessionEventSubscription:
        subscription = await self._session.subscribe(_harness_read_params(params))
        snapshot = self._read_response(subscription.snapshot)
        async with self._events_condition:
            self._observed_harness_watermark = subscription.snapshot.watermark
            self._events_condition.notify_all()
        return SessionEventSubscription(
            snapshot=snapshot,
            events=self._translated_events(subscription, snapshot.state),
        )

    async def flush_events(self) -> None:
        snapshot = await self._session.read(
            HarnessSessionReadParams(session_id=self.session_id, history_limit=1)
        )
        target = snapshot.snapshot.watermark
        async with self._events_condition:
            while self._events_subscribed and self._observed_harness_watermark < target:
                await self._events_condition.wait()

    def guard_request(self) -> None:
        self._session.guard_request()

    async def switch_agent(
        self, params: AgentSwitchParams
    ) -> SessionBackendResult[RuntimeMutationResponse]:
        self._require_session(params.session_id)
        try:
            self._context.agents.switch_profile(params.agent_name)
        except ValueError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        await self._apply_derivation()
        return SessionBackendResult(
            response=RuntimeMutationResponse(runtime=self._runtime)
        )

    async def update_settings(
        self, params: SessionSettingsUpdateParams
    ) -> SessionBackendResult[EmptyResponse]:
        self._require_session(params.session_id)
        self._require_idle()
        self._settings = UnifiedSessionSettings(
            max_turns=(
                params.max_turns
                if params.max_turns is not None
                else self._settings.max_turns
            ),
            max_tokens=(
                params.max_tokens
                if params.max_tokens is not None
                else self._settings.max_tokens
            ),
        )
        await self._apply_derivation()
        return SessionBackendResult(response=EmptyResponse())

    async def write_config(
        self, params: ConfigWriteParams
    ) -> SessionBackendResult[ConfigWriteResponse]:
        self._require_session(params.session_id)
        self._require_idle()
        orchestrator = self._context.config_orchestrator
        durable_aliases = await orchestrator.durable_model_aliases()
        operations = config_write_ops_to_patches(
            orchestrator.config, params.ops, durable_model_aliases=durable_aliases
        )
        try:
            failures = await orchestrator.apply_patch(operations, reason=params.reason)
        except (ConfigPatchValidationError, ValueError):
            return SessionBackendResult(
                response=ConfigWriteResponse(runtime=self._runtime, rejected=True)
            )
        if failures:
            await self._apply_derivation()
            return SessionBackendResult(
                response=ConfigWriteResponse(
                    runtime=self._runtime,
                    failures=[str(failure) for failure in failures],
                )
            )
        await self._apply_derivation()
        return SessionBackendResult(response=ConfigWriteResponse(runtime=self._runtime))

    async def reload_config(
        self, params: ConfigReloadParams
    ) -> SessionBackendResult[ConfigMutationResponse]:
        self._require_session(params.session_id)
        self._require_idle()
        # Best-effort: an admin-fetch failure must never break the user's reload.
        # asyncio.timeout caps the full retry budget so /reload stays responsive.
        try:
            async with asyncio.timeout(MANAGED_CONFIG_TIMEOUT * 1.5):
                report_admin_config_outcome(
                    await refresh_admin_layer(self._context.config_orchestrator)
                )
        except Exception as exc:
            logger.debug("Admin config refresh failed on reload", exc_info=exc)
        await self._context.config_orchestrator.reload()
        await self._apply_derivation()
        return SessionBackendResult(
            response=ConfigMutationResponse(runtime=self._runtime)
        )

    async def _config_read_response(self) -> ConfigReadResponse:
        orchestrator = self._context.config_orchestrator
        config = orchestrator.config
        harness_files = self._context.harness_files
        skills = SkillManager(
            config_getter=lambda: config, harness_files=harness_files
        ).available_skills
        return ConfigReadResponse(
            config=project_config_view(
                config, active_model_pinned=bool(orchestrator.persisted_active_model())
            ),
            skills_count=sum(
                1
                for skill in skills.values()
                if skill.source is not SkillSource.BUILTIN
            ),
            hooks_count=len(
                (
                    await asyncio.to_thread(
                        load_hooks_from_fs, harness_files=harness_files
                    )
                ).hooks
            ),
            mcp_servers_total=len(config.mcp_servers),
            mcp_servers_enabled=sum(
                1 for server in config.mcp_servers if not server.disabled
            ),
        )

    async def _config_fields_response(self) -> ConfigFieldsReadResponse:
        orchestrator = self._context.config_orchestrator
        layer_values = await collect_layer_values(orchestrator.layers)
        # Per-tool config editing is not exposed in the settings screen yet.
        fields = [
            wire
            for wire in build_field_wires(
                orchestrator.config, layer_values, popular=POPULAR_SETTINGS
            )
            if wire.name not in HIDDEN_SETTINGS
        ]
        names = {layer.name for layer in orchestrator.layers}
        targets = [orchestrator.writable_layer_name]
        if OverridesLayer.NAME in names and OverridesLayer.NAME not in targets:
            targets.append(OverridesLayer.NAME)
        return ConfigFieldsReadResponse(fields=fields, targets=targets)

    async def _write_proxy_settings(self, params: ConfigProxyWriteParams) -> None:
        def write() -> None:
            for key, value in params.changes.items():
                if value:
                    set_proxy_var(key, value)
                else:
                    unset_proxy_var(key)

        try:
            await asyncio.to_thread(write)
        except ProxySetupError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc

    def _require_idle(self) -> None:
        """Reject a configuration mutation aimed at a session mid-turn.

        The Rust Core reads its settings when the turn starts, so applying a new
        derivation under a running turn would either be silently ignored or swap
        the provider between two iterations of the same turn.
        """
        active_turn_id = self._session.active_turn_id
        if active_turn_id is None:
            return
        raise SessionBackendError(
            ProtocolErrorCode.CONFLICT,
            f"A turn is already running: {active_turn_id}",
            {"activeTurnId": active_turn_id},
        )

    async def _apply_derivation(self) -> None:
        """Re-derive from the mutated config and push it into the live session.

        Root capabilities are pushed when the skill tool availability changes.
        Plugin contexts are not: the provider's ``bind`` is the only source of
        the bound set, and this derivation carries none, so pushing it would
        clear what the session is running. The rest of ``core_config`` —
        provider, settings, system instructions — is read when the Core is
        built, so it takes effect on the next bind.

        ``mcp`` and ``connectors`` are carried over rather than taken from the
        derivation. They describe what the session is *connected to*, which
        comes back from the Harness and is patched in as it changes; a
        derivation only projects the layered config and leaves both empty. What
        the config asks for still lands, through ``config``. Converging the two
        is ``config/reload``'s job, and it brackets this call to do it.
        """
        derivation = await asyncio.to_thread(self._context.derive, self._settings)
        self._runtime = derivation.runtime.model_copy(
            update={"mcp": self._runtime.mcp, "connectors": self._runtime.connectors}
        )
        self._skills = derivation.adapter_config.skills
        self._session.apply_adapter_config(derivation.adapter_config)
        await self._session.apply_capabilities(derivation.core_config.capabilities)

    async def start_turn(
        self, params: TurnStartParams
    ) -> SessionBackendResult[TurnStartResponse]:
        params = await self._prepared_turn_params(params, inject_skill=True)
        result = cast(Any, await _harness_call(self._session.start_turn(params)))
        response = result.response
        turn = response.turn
        return SessionBackendResult(
            response=TurnStartResponse(
                turn=PublicTurn(
                    id=turn.id,
                    session_id=turn.session_id,
                    status=PublicTurnStatus.IN_PROGRESS,
                    started_at=turn.started_at,
                ),
                last_event_id=self._event_id,
            ),
            after_response=result.after_response,
        )

    async def steer_turn(
        self, params: TurnSteerParams
    ) -> SessionBackendResult[TurnSteerResponse]:
        params = await self._prepared_turn_params(
            params, inject_skill=params.inject_invoked_skill
        )
        result = cast(Any, await _harness_call(self._session.steer_turn(params)))
        response = result.response
        return SessionBackendResult(
            response=TurnSteerResponse(
                accepted=response["accepted"], last_event_id=response["last_event_id"]
            ),
            after_response=result.after_response,
        )

    async def interrupt_turn(
        self, params: TurnInterruptParams
    ) -> SessionBackendResult[TurnInterruptResponse]:
        result = cast(Any, await _harness_call(self._session.interrupt_turn(params)))
        response = result.response
        return SessionBackendResult(
            response=TurnInterruptResponse(
                accepted=response["accepted"], last_event_id=response["last_event_id"]
            ),
            after_response=result.after_response,
        )

    async def inject_context(
        self, params: ContextInjectParams
    ) -> SessionBackendResult[ContextInjectResponse]:
        block = (
            await self._invoked_skill_block(params.input)
            if params.inject_invoked_skill
            else None
        )
        try:
            params = await self._with_mentioned_file_blocks_for_input(params)
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        if block is not None:
            params = params.model_copy(update={"input": [*params.input, block]})
        result = cast(Any, await _harness_call(self._session.inject_context(params)))
        response = result.response
        return SessionBackendResult(
            response=ContextInjectResponse(
                entries=[validate_history_entry(entry) for entry in response["entries"]]
            ),
            after_response=result.after_response,
        )

    def open_callbacks(self) -> list[PublicCallbackEntry]:
        return list(self._open_callbacks.values())

    async def reject_callback_delivery(
        self, session_id: str, callback_id: str, error: CallbackResultError
    ) -> None:
        if session_id != self.session_id:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )
        await _harness_call(
            self._session.respond_to_callback(
                CallbackResultParams(
                    session_id=session_id,
                    result=CallbackResult(callback_id=callback_id, error=error),
                )
            )
        )

    async def respond_to_callback(
        self, params: object
    ) -> SessionBackendResult[CallbackResultResponse]:
        result = cast(
            Any, await _harness_call(self._session.respond_to_callback(params))
        )
        response = result.response
        return SessionBackendResult(
            response=CallbackResultResponse(
                accepted=True, last_event_id=response["last_event_id"]
            ),
            after_response=result.after_response,
        )

    async def compact(self, params: object) -> Never:
        _reject("session/compact")

    async def shutdown(self) -> None:
        await self._session.shutdown()

    def _cwd_path(self) -> Path:
        return Path(self._cwd or Path.cwd()).expanduser().resolve()

    def _session_dir(self) -> Path:
        return Path(self._storage_root) / "unified" / self.session_id

    async def _session_log_summary(self) -> SessionLogSummary:
        result = await self._session.read(
            _harness_read_params(
                SessionReadParams(
                    session_id=self.session_id, history=PageRequest(limit=1)
                )
            )
        )
        state = result.snapshot.state
        return SessionLogSummary(
            enabled=True,
            session_id=self.session_id,
            persisted=True,
            path=str(self._session_dir()),
            title=state.session.title,
            needs_initial_auto_title=state.session.title is None,
        )

    async def _prepare_prompt(
        self, params: WorkspacePromptPrepareParams
    ) -> PreparedPrompt:
        summary = await self._session_log_summary()
        return prepare_prompt_from_context(
            params.message,
            cwd=self._cwd_path(),
            session_dir=Path(summary.path) if summary.path is not None else None,
            model_alias=self._runtime.config.active_model.alias,
            model_supports_images=self._runtime.config.active_model.supports_images,
            needs_initial_auto_title=summary.needs_initial_auto_title,
            title_content=params.title_content,
        )

    async def _prepare_prompt_response(
        self, params: WorkspacePromptPrepareParams
    ) -> WorkspacePromptPrepareResponse:
        try:
            prompt = await self._prepare_prompt(params)
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        return WorkspacePromptPrepareResponse(prompt=prompt)

    async def _invoked_skill_block(
        self, blocks: list[ContentBlock]
    ) -> ContentBlock | None:
        text = _text_from_blocks(blocks).strip()
        if not text.startswith("/"):
            return None
        parts = text[1:].split(None, 1)
        if not parts:
            return None
        typed = parts[0].casefold()
        skill = next(
            (s for s in self._runtime.skills if s.name.casefold() == typed), None
        )
        if skill is None or not skill.user_invocable:
            return None
        name = skill.name
        # A skill Core rejected is still in the client-facing list, and it has
        # no payload. Injecting nothing beats injecting a name with no body.
        body = self._skills.get(name)
        if body is None:
            return None
        if await self._skill_already_loaded(name):
            return TextContentBlock(text=already_loaded_message(name))
        return TextContentBlock(text=body)

    async def _skill_already_loaded(self, name: str) -> bool:
        """Report whether this conversation already carries the skill's body.

        Core owns the model-visible history, so the check runs over the public
        projection: a body this adapter injected shows up as the marker its
        renderer opens with, and one the model loaded itself shows up as a
        ``skill`` effect. Only a resolved ``/name`` gets this far, so the read
        costs a page on the turns legacy also scans for.
        """
        marker = skill_content_marker(name)
        state = await self._read_page_state(self.session_id)
        return any(
            _entry_loaded_skill(entry, name=name, marker=marker)
            for entry in state.history or ()
        )

    async def _prepared_turn_params[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT, *, inject_skill: bool
    ) -> ParamsT:
        block = (
            await self._invoked_skill_block(params.message) if inject_skill else None
        )
        try:
            params = await self._with_mentioned_file_blocks(params)
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        if block is None:
            return params
        return params.model_copy(update={"message": [*params.message, block]})

    async def _with_mentioned_file_blocks[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT
    ) -> ParamsT:
        text = _text_from_blocks(params.message)
        blocks = await mentioned_file_content_blocks_async(
            text, base_dir=self._cwd_path()
        )
        if not blocks:
            return params
        return params.model_copy(update={"message": [*params.message, *blocks]})

    async def _with_mentioned_file_blocks_for_input(
        self, params: ContextInjectParams
    ) -> ContextInjectParams:
        text = _text_from_blocks(params.input)
        blocks = await mentioned_file_content_blocks_async(
            text, base_dir=self._cwd_path()
        )
        if not blocks:
            return params
        return params.model_copy(update={"input": [*params.input, *blocks]})

    async def _read_page_state(self, session_id: str) -> PublicSessionState:
        result = await self._session.read(
            _harness_read_params(
                SessionReadParams(
                    session_id=session_id,
                    history=PageRequest(limit=500),
                    turns=PageRequest(limit=500),
                )
            )
        )
        return _read_response(result.snapshot, self._cwd).state

    def _read_response(self, snapshot: HarnessSessionSnapshot) -> SessionReadResponse:
        response = _read_response(snapshot, self._cwd, event_id=self._event_id)
        self._event_id = response.last_event_id
        return response

    async def _translated_events(
        self, subscription: HarnessSessionSubscription, previous: PublicSessionState
    ) -> AsyncIterator[SessionBackendEvent]:
        async with self._events_condition:
            self._events_subscribed = True
            self._events_condition.notify_all()
        try:
            async for event in subscription.events:
                signal = await self._signal_event(cast(dict[str, object], event))
                if signal is not None:
                    translated, watermark = signal
                    yield translated
                    await self._mark_harness_event_observed(watermark)
                    continue
                callback_events = self._callback_events(event)
                if callback_events is not None:
                    for callback_event in callback_events:
                        yield callback_event
                    await self._mark_harness_event_observed(
                        _required_event_id(event, "callback")
                    )
                    continue
                if event.get("type") != "session_state_updated":
                    _reject(f"the Harness session event {event.get('type', event)!r}")
                raw_state = event.get("state")
                if not isinstance(raw_state, dict):
                    _reject("a Harness session update without state")
                watermark = event.get("eventId")
                if not isinstance(watermark, int):
                    _reject("a Harness session update without an event id")
                state = HarnessPublicSessionState.model_validate(raw_state)
                current = _read_response(
                    HarnessSessionSnapshot(
                        state=state,
                        history_limit=subscription.snapshot.history_limit,
                        watermark=watermark,
                    ),
                    self._cwd,
                    event_id=self._event_id,
                ).state
                stats_updated = (
                    previous.session.token_usage != current.session.token_usage
                )
                for app_event in reconcile_snapshot(previous, current):
                    if isinstance(app_event, SessionSnapshot):
                        continue
                    if isinstance(app_event, TurnCompleted) and stats_updated:
                        usage = current.session.token_usage
                        stats = AgentStatsSnapshot(
                            session_prompt_tokens=usage.input_tokens if usage else 0,
                            session_completion_tokens=usage.output_tokens
                            if usage
                            else 0,
                            context_tokens=usage.input_tokens if usage else 0,
                        )
                        self._event_id += 1
                        yield _event_envelope(
                            StatsUpdated(
                                StatsUpdatedParams(
                                    event_id=self._event_id,
                                    session_id=current.session.id,
                                    emitted_at=int(time.time() * 1000),
                                    stats=stats,
                                    context_window=self._runtime.context_window,
                                )
                            ),
                            self._event_id,
                        )
                        stats_updated = False
                    self._event_id += 1
                    yield _event_envelope(app_event, self._event_id)
                if stats_updated:
                    usage = current.session.token_usage
                    stats = AgentStatsSnapshot(
                        session_prompt_tokens=usage.input_tokens if usage else 0,
                        session_completion_tokens=usage.output_tokens if usage else 0,
                        context_tokens=usage.input_tokens if usage else 0,
                    )
                    self._event_id += 1
                    yield _event_envelope(
                        StatsUpdated(
                            StatsUpdatedParams(
                                event_id=self._event_id,
                                session_id=current.session.id,
                                emitted_at=int(time.time() * 1000),
                                stats=stats,
                                context_window=self._runtime.context_window,
                            )
                        ),
                        self._event_id,
                    )
                previous = current.model_copy(
                    update={"event_id": self._event_id}, deep=True
                )
                await self._mark_harness_event_observed(watermark)
        finally:
            async with self._events_condition:
                self._events_subscribed = False
                self._events_condition.notify_all()

    async def _signal_event(
        self, event: dict[str, object]
    ) -> tuple[SessionBackendEvent, int] | None:
        """Translate the events that carry a signal rather than a state change.

        Each maps to a notification of its own and none touch the session
        snapshot, so none reach the reconciliation below, which has nothing to
        diff for them. The event id rides back with the translation because a
        signal still spends one, and ``flush_events`` waits on the id the
        session reports, so skipping one hangs the request that published it.
        """
        match event.get("type"):
            case "connector_authorization_required":
                watermark = _required_event_id(event, "connector")
                return await self._connector_authorization_event(event), watermark
            case "mcp_authorization_required":
                watermark = _required_event_id(event, "MCP")
                return self._mcp_authorization_event(event), watermark
            case "notice":
                watermark = _required_event_id(event, "notice")
                return self._notice_event(event), watermark
            case _:
                return None

    def _mcp_authorization_event(self, event: dict[str, object]) -> SessionBackendEvent:
        params = MCPAuthRequiredParams(
            session_id=self.session_id,
            name=_required_event_str(event, "serverName"),
            descriptor_revision=_required_event_str(event, "descriptorRevision"),
            observed_connection_revision=_optional_event_str(
                event, "observedConnectionRevision"
            ),
        )
        return SessionBackendEvent(
            event=MCPAuthorizationRequiredEvent(params),
            method="mcp_catalog/authRequired",
            params=params,
            session_id=self.session_id,
        )

    async def _connector_authorization_event(
        self, event: Mapping[str, object]
    ) -> SessionBackendEvent:
        self._update_connector_projection(await self.read_connectors())
        params = ConnectorAuthRequiredParams(
            session_id=self.session_id,
            alias=_required_event_str(event, "alias"),
            accepted_catalog_revision=_required_event_str(
                event, "acceptedCatalogRevision"
            ),
            reason=cast(Any, _required_event_str(event, "reason")),
        )
        return SessionBackendEvent(
            event=ConnectorAuthorizationRequiredEvent(
                params=params,
                raw_connector_id=_required_event_str(event, "rawConnectorId"),
                action=_required_event_str(event, "action"),
            ),
            method="connector_catalog/authRequired",
            params=params,
            session_id=self.session_id,
        )

    def _notice_event(self, event: dict[str, object]) -> SessionBackendEvent:
        """Carry an out-of-band remark out on the notification the Client has.

        The closed event union has no plugin event and a reload changes no
        session state, so the remark rides ``warning`` rather than becoming a
        history entry: Core owns history in a Unified session, and a Host
        writing into it would author a turn out of an operational aside.
        """
        params = ServerWarningParams(
            warning=PublicError(
                code=_optional_event_str(event, "level") or "warning",
                message=_required_event_str(event, "message"),
            )
        )
        return SessionBackendEvent(
            event=ServerWarning(params),
            method="warning",
            params=params,
            session_id=self.session_id,
        )

    async def _mark_harness_event_observed(self, watermark: int) -> None:
        async with self._events_condition:
            self._observed_harness_watermark = max(
                self._observed_harness_watermark, watermark
            )
            self._events_condition.notify_all()

    def _callback_events(
        self, event: Mapping[str, object]
    ) -> list[SessionBackendEvent] | None:
        event_type = event.get("type")
        if event_type not in {"callback_requested", "callback_resolved"}:
            return None
        raw_callback = event.get("callback")
        if not isinstance(raw_callback, dict):
            _reject("a Harness callback event without callback data")
        callback = validate_history_entry(raw_callback)
        if not isinstance(callback, PublicCallbackEntry):
            _reject("a Harness callback event with a non-callback entry")
        if event_type == "callback_requested":
            self._open_callbacks[callback.id] = callback
            self._event_id += 1
            return [
                _event_envelope(HistoryEntryAdded(callback), self._event_id),
                SessionBackendEvent(event=CallbackRequested(callback)),
            ]
        previous_callback = self._open_callbacks.pop(callback.id, callback)
        self._event_id += 1
        return [
            _event_envelope(
                HistoryEntryUpdated(
                    previous=previous_callback,
                    entry=callback,
                    patch=[
                        JsonPatchOperation(
                            op="replace", path="/generationStatus", value="completed"
                        ),
                        JsonPatchOperation(
                            op="replace", path="/state", value=raw_callback["state"]
                        ),
                    ],
                ),
                self._event_id,
            )
        ]


def _required_event_str(event: Mapping[str, object], name: str) -> str:
    value = event.get(name)
    if not isinstance(value, str) or not value:
        _reject(f"a Harness MCP event without {name}")
    return value


def _required_event_id(event: Mapping[str, object], kind: str) -> int:
    value = event.get("eventId")
    if not isinstance(value, int):
        _reject(f"a Harness {kind} event without an event id")
    return value


def _optional_event_str(event: Mapping[str, object], name: str) -> str | None:
    value = event.get(name)
    if value is not None and not isinstance(value, str):
        _reject(f"a Harness MCP event with an invalid {name}")
    return value


def _session_cwd(options: SessionOptions) -> str:
    return str(Path(options.cwd or Path.cwd()).expanduser().resolve())


def _with_session_cwd(options: SessionOptions, cwd: str | None) -> SessionOptions:
    """Pin the context build to a session's stored cwd on resume/continue/fork.

    Binding ids embed the source cwd (``f"{cwd}:{name}"``), so hooks must be discovered
    against the stored cwd, not the caller's invocation cwd, or a resume/fork rebinds to
    the wrong project. Falls back to the request cwd when the stored cwd is unresolvable.

    ``--trust`` is scoped to the caller's invocation cwd, so it is dropped when the pinned
    cwd differs -- a cross-directory ``--trust`` resume must not auto-execute another
    project's ``hooks.toml``.
    """
    if cwd is None:
        return options
    update: dict[str, object] = {"cwd": cwd}
    if options.trust_workspace and cwd != _session_cwd(options):
        update["trust_workspace"] = False
    return options.model_copy(update=update)


def _harness_read_params(params: SessionReadParams) -> HarnessSessionReadParams:
    return HarnessSessionReadParams(
        session_id=params.session_id, history_limit=params.history_limit
    )


def _text_from_blocks(blocks: list[ContentBlock]) -> str:
    return "\n\n".join(
        block.text for block in blocks if isinstance(block, TextContentBlock)
    )


def _entry_loaded_skill(entry: PublicHistoryEntry, *, name: str, marker: str) -> bool:
    if isinstance(entry, PublicMessageEntry):
        return marker in _text_from_blocks(entry.content)
    return (
        isinstance(entry, PublicEffectEntry)
        and isinstance(entry.detail, SkillEffectDetail)
        and entry.detail.input is not None
        and entry.detail.input.name == name
        and entry.state.status == "completed"
        and marker in entry.state.output_text
    )


def _limit_harness_history(
    state: HarnessPublicSessionState, history_limit: int
) -> HarnessPublicSessionState:
    entries = state.history.entries[-history_limit:] if history_limit else []
    return state.model_copy(
        update={"history": state.history.model_copy(update={"entries": entries})}
    )


def _normalize_effect_output(entry: PublicHistoryEntry) -> PublicHistoryEntry:
    """Re-project a completed effect's output through the shared effect projection.

    A post_tool hook can leave a tool effect's ``output`` as the raw RustToolResult wire
    shape, which fits no client's output model. Routing it through
    ``project_effect_output_value`` degrades such a result to None; valid native outputs
    are unchanged (the projection is idempotent for them).
    """
    if not isinstance(entry, PublicEffectEntry):
        return entry
    state = entry.state
    if not isinstance(state, CompletedEffectState) or state.output is None:
        return entry
    reprojected = project_effect_output_value(entry.detail.kind, state.output)
    if reprojected == state.output:
        return entry
    return entry.model_copy(
        update={"state": state.model_copy(update={"output": reprojected})}
    )


def _read_response(
    snapshot: HarnessSessionSnapshot, cwd: str | None, *, event_id: int | None = None
) -> SessionReadResponse:
    history = []
    for raw_entry in snapshot.state.history.entries:
        normalized = dict(raw_entry)
        normalized.pop("outcome", None)
        history.append(_normalize_effect_output(validate_history_entry(normalized)))
    last_event_id = (
        snapshot.watermark if event_id is None else max(event_id, snapshot.watermark)
    )
    return SessionReadResponse(
        state=PublicSessionState(
            event_id=last_event_id,
            session=_public_session(snapshot.state.session, cwd),
            history=history,
            turns=(
                [_public_turn(snapshot.state.latest_turn)]
                if snapshot.state.latest_turn is not None
                else []
            ),
        ),
        last_event_id=last_event_id,
    )


def _public_session(session: HarnessPublicSession, cwd: str | None) -> PublicSession:
    status = cast(Any, session.status)
    if getattr(status, "type", None) == "running":
        public_status = VibeRunningSessionStatus(active_turn_id=status.active_turn_id)
    elif getattr(status, "type", None) == "blocked":
        public_status = VibeBlockedSessionStatus(
            active_turn_id=status.active_turn_id,
            callback_id=status.callback_id,
            reason=status.callback_kind,
        )
    elif getattr(status, "type", None) == "failed":
        public_status = VibeFailedSessionStatus(message=status.message)
    else:
        public_status = VibeIdleSessionStatus()
    token_usage = (
        VibeTokenUsage.model_validate(
            session.token_usage.model_dump(mode="json", by_alias=True)
        )
        if session.token_usage is not None
        else None
    )
    return PublicSession(
        id=session.id,
        root_session_id=session.root_session_id,
        parent_session_id=session.parent_session_id,
        title=session.title,
        preview=session.preview,
        status=public_status,
        created_at=session.created_at,
        updated_at=session.updated_at,
        cwd=cwd,
        token_usage=token_usage,
    )


def _public_turn(turn: object) -> PublicTurn:
    raw = cast(Any, turn)
    error = getattr(raw, "error", None)
    return PublicTurn(
        id=raw.id,
        session_id=raw.session_id,
        status=PublicTurnStatus(str(raw.status)),
        started_at=raw.started_at,
        completed_at=getattr(raw, "completed_at", None),
        error=_public_turn_error(error),
        stop_reason=getattr(raw, "stop_reason", None),
    )


def _public_turn_error(error: object | None) -> PublicError | None:
    if error is None:
        return None
    raw_error = cast(Any, error)
    public_error = PublicError.model_validate(
        raw_error.model_dump(mode="json", by_alias=True)
    )
    if public_error.code == "model_stream_failed":
        public_error = public_error.model_copy(
            update={"code": TurnErrorCode.BACKEND_ERROR}
        )
    return public_error


def _turns_list_response(
    turns: list[PublicTurn], params: SessionTurnsListParams
) -> SessionTurnsListResponse:
    if params.sort_direction == "backward":
        if params.cursor is None:
            page = turns[-params.limit :]
            first_index = max(0, len(turns) - len(page))
        else:
            end = next(
                (index for index, turn in enumerate(turns) if turn.id == params.cursor),
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
    previous_cursor = page[-1].id if page and last_index < len(turns) - 1 else None
    if params.sort_direction == "forward":
        next_cursor, previous_cursor = previous_cursor, next_cursor
    return SessionTurnsListResponse(
        items=page, next_cursor=next_cursor, previous_cursor=previous_cursor
    )


def _turns_from_history(
    history: list[PublicHistoryEntry], session_id: str
) -> list[PublicTurn]:
    turns: dict[str, PublicTurn] = {}
    for entry in history:
        if entry.turn_id is None:
            continue
        previous = turns.get(entry.turn_id)
        turns[entry.turn_id] = PublicTurn(
            id=entry.turn_id,
            session_id=session_id,
            status=PublicTurnStatus.COMPLETED,
            started_at=entry.created_at if previous is None else previous.started_at,
            completed_at=entry.updated_at,
        )
    return list(turns.values())


def _event_envelope(event: object, event_id: int) -> SessionBackendEvent:
    emitted_at = int(time.time() * 1000)
    if isinstance(event, HistoryEntryAdded):
        params = HistoryEntryAddedParams(
            event_id=event_id,
            session_id=event.entry.session_id,
            emitted_at=emitted_at,
            turn_id=event.entry.turn_id,
            entry=event.entry,
        )
        return SessionBackendEvent(
            event=event,
            method="history/entryAdded",
            params=params,
            session_id=event.entry.session_id,
            event_id=event_id,
        )
    if isinstance(event, HistoryEntryUpdated):
        params = HistoryEntryUpdatedParams(
            event_id=event_id,
            session_id=event.entry.session_id,
            emitted_at=emitted_at,
            turn_id=event.entry.turn_id,
            entry_id=event.entry.id,
            patch=event.patch,
        )
        return SessionBackendEvent(
            event=event,
            method="history/entryUpdated",
            params=params,
            session_id=event.entry.session_id,
            event_id=event_id,
        )
    if isinstance(event, SessionUpdated):
        params = SessionUpdatedParams(
            event_id=event_id,
            session_id=event.session.id,
            emitted_at=emitted_at,
            patch=event.patch,
        )
        return SessionBackendEvent(
            event=event,
            method="session/updated",
            params=params,
            session_id=event.session.id,
            event_id=event_id,
        )
    if isinstance(event, TurnStarted):
        params = TurnStartedParams(
            event_id=event_id,
            session_id=event.turn.session_id,
            emitted_at=emitted_at,
            turn=event.turn,
        )
        return SessionBackendEvent(
            event=event,
            method="turn/started",
            params=params,
            session_id=event.turn.session_id,
            event_id=event_id,
        )
    if isinstance(event, TurnCompleted):
        params = TurnCompletedParams(
            event_id=event_id,
            session_id=event.turn.session_id,
            emitted_at=emitted_at,
            turn=event.turn,
        )
        return SessionBackendEvent(
            event=event,
            method="turn/completed",
            params=params,
            session_id=event.turn.session_id,
            event_id=event_id,
        )
    if isinstance(event, StatsUpdated):
        params = event.params.model_copy(update={"event_id": event_id})
        return SessionBackendEvent(
            event=event,
            method="session/statsUpdated",
            params=params,
            session_id=params.session_id,
            event_id=event_id,
        )
    raise TypeError(f"Unsupported app-server event: {event!r}")


def _harness_mcp_catalog(catalog: ResolvedMCPCatalog) -> HarnessResolvedMCPCatalog:
    return HarnessResolvedMCPCatalog(
        revision=catalog.revision,
        servers=tuple(
            HarnessResolvedMCPServerConfig(
                name=server.name,
                transport=server.transport,
                url=server.url,
                command=server.command,
                args=server.args,
                cwd=server.cwd,
                env=server.env,
                authorization=HarnessMCPAuthorizationRef(
                    server_name=server.authorization.server_name,
                    server_fingerprint=server.authorization.server_fingerprint,
                    kind=server.authorization.kind,
                    descriptor_revision=server.authorization.descriptor_revision,
                ),
                prompt=server.prompt,
                startup_timeout_s=server.startup_timeout_s,
                tool_timeout_s=server.tool_timeout_s,
                sampling_enabled=server.sampling_enabled,
                disabled=server.disabled,
                disabled_tools=server.disabled_tools,
            )
            for server in catalog.servers
        ),
    )


def _harness_connector_catalog(
    catalog: ResolvedConnectorCatalog,
) -> HarnessResolvedConnectorCatalog:
    return HarnessResolvedConnectorCatalog(
        revision=catalog.revision,
        connectors=tuple(
            HarnessResolvedConnector(
                raw_id=connector.raw_id,
                alias=connector.alias,
                display_name=connector.display_name,
                ready=connector.ready,
                auth_action=connector.auth_action,
                tools=tuple(
                    HarnessResolvedConnectorTool(
                        raw_name=tool.raw_name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                    )
                    for tool in connector.tools
                ),
                diagnostics=connector.diagnostics,
            )
            for connector in catalog.connectors
        ),
    )


def _harness_connector_selection(
    selection: ResolvedConnectorSelection,
) -> HarnessResolvedConnectorSelection:
    return HarnessResolvedConnectorSelection(
        selection_revision=selection.selection_revision,
        enable_connectors=selection.enable_connectors,
        implicit_source_enabled=selection.implicit_source_enabled,
        connector_settings=tuple(
            HarnessResolvedConnectorSetting(
                alias=setting.alias,
                disabled=setting.disabled,
                disabled_tools=setting.disabled_tools,
            )
            for setting in selection.connector_settings
        ),
        enabled_tools=selection.enabled_tools,
        disabled_tools=selection.disabled_tools,
    )


class _HarnessMCPAuthorizationProviderAdapter(HarnessMCPAuthorizationProvider):
    def __init__(self, provider: MCPAuthorizationProvider) -> None:
        self._provider = provider

    async def resolve(
        self, reference: HarnessMCPAuthorizationRef
    ) -> HarnessMCPAuthorizationSnapshot | HarnessMCPAuthorizationRequired:
        result = await self._provider.resolve(_app_authorization_ref(reference))
        return _harness_authorization_result(result)

    async def reject(
        self,
        reference: HarnessMCPAuthorizationRef,
        *,
        observed_connection_revision: str,
        reason: str,
    ) -> HarnessMCPAuthorizationSnapshot | HarnessMCPAuthorizationRequired:
        if reason not in {"http_unauthorized", "mcp_unauthorized"}:
            raise ValueError("Unsupported MCP authorization rejection reason")
        result = await self._provider.reject(
            _app_authorization_ref(reference),
            observed_connection_revision=observed_connection_revision,
            reason=cast(Any, reason),
        )
        return _harness_authorization_result(result)


def _app_authorization_ref(
    reference: HarnessMCPAuthorizationRef,
) -> MCPAuthorizationRef:
    return MCPAuthorizationRef(
        server_name=reference.server_name,
        server_fingerprint=reference.server_fingerprint,
        kind=reference.kind,
        descriptor_revision=reference.descriptor_revision,
    )


def _harness_authorization_result(
    result: MCPAuthorizationSnapshot | MCPAuthorizationRequired,
) -> HarnessMCPAuthorizationSnapshot | HarnessMCPAuthorizationRequired:
    if isinstance(result, MCPAuthorizationRequired):
        return HarnessMCPAuthorizationRequired(
            reason=result.reason,
            descriptor_revision=result.descriptor_revision,
            observed_connection_revision=result.observed_connection_revision,
        )
    return HarnessMCPAuthorizationSnapshot(
        headers=result.headers,
        connection_revision=result.connection_revision,
        descriptor_revision=result.descriptor_revision,
        expires_at=result.expires_at,
    )


def _session_mcp_state(
    snapshot: HarnessMCPRouteSnapshot,
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> SessionMCPState:
    transports = {
        server.name: server.transport for server in orchestrator.config.mcp_servers
    }
    display_names = {
        (tool.server_name, tool.remote_name): tool.display_name
        for group in snapshot.groups
        for tool in group.tools
    }
    sources = tuple(
        SessionMCPSourceState(
            name=source.name,
            transport=cast(Any, transports[source.name]),
            status=source.status,
            tools=tuple(
                SessionMCPToolDescriptor(
                    remote_name=descriptor.remote_name,
                    description=descriptor.description,
                    enabled=descriptor.remote_name
                    not in next(
                        server.disabled_tools
                        for server in orchestrator.config.mcp_servers
                        if server.name == source.name
                    ),
                    display_name=display_names.get(
                        (source.name, descriptor.remote_name),
                        f"{source.name}_{descriptor.remote_name}",
                    ),
                )
                for descriptor in source.descriptors
            ),
            descriptor_revision=source.descriptor_revision,
            error=source.error,
        )
        for source in snapshot.sources
        if source.name in transports
    )
    return SessionMCPState(
        catalog_revision=snapshot.catalog_revision,
        route_revision=snapshot.route_revision,
        sources=sources,
        discovery_errors={
            source.name: source.error
            for source in snapshot.sources
            if source.error is not None
        },
    )


def _session_connector_state(
    snapshot: HarnessConnectorRouteSnapshot,
) -> SessionConnectorState:
    sources = tuple(
        SessionConnectorSourceState(
            raw_id=source.raw_id,
            alias=source.alias,
            display_name=source.display_name,
            status=source.status,
            tools=tuple(
                SessionConnectorToolDescriptor(
                    raw_name=tool.remote_name,
                    description=tool.description,
                    enabled=tool.enabled,
                    display_name=tool.display_name,
                )
                for tool in source.tools
            ),
            error=source.error,
        )
        for source in snapshot.sources
    )
    return SessionConnectorState(
        accepted_catalog_revision=snapshot.catalog_revision,
        accepted_selection_revision=snapshot.selection_revision,
        route_revision=snapshot.route_revision,
        sources=sources,
        discovery_errors={
            source.alias: source.error for source in sources if source.error is not None
        },
    )


async def _harness_call[ResultT](operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except HarnessSessionNotFoundError as exc:
        raise SessionBackendError(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
    except HarnessNotImplementedError as exc:
        raise SessionBackendError(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc
    except HarnessSessionError as exc:
        if exc.code == "callback_closed":
            raise SessionBackendError(
                ProtocolErrorCode.CALLBACK_CLOSED, str(exc)
            ) from exc
        if exc.code == "callback_not_found":
            raise SessionBackendError(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
        if exc.code == "stale_turn":
            data: dict[str, Any] = {}
            active_turn_id = exc.details.get("active_turn_id") if exc.details else None
            if active_turn_id is None:
                raise SessionBackendError(
                    ProtocolErrorCode.CONFLICT, "No active turn"
                ) from exc
            data["activeTurnId"] = active_turn_id
            raise SessionBackendError(
                ProtocolErrorCode.STALE_TURN, str(exc), data or None
            ) from exc
        code = (
            ProtocolErrorCode.CONFLICT
            if exc.code
            in {
                "session_busy",
                "client_command_conflict",
                "turn_conflict",
                "unfinished_work_migration",
            }
            else ProtocolErrorCode.INTERNAL_ERROR
        )
        data: dict[str, Any] = {"harnessCode": exc.code}
        if exc.details is not None:
            data["details"] = exc.details
        raise SessionBackendError(code, str(exc), data) from exc
    except ValueError as exc:
        raise SessionBackendError(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc


def _reject(operation: str) -> Never:
    raise SessionBackendError(
        ProtocolErrorCode.INTERNAL_ERROR,
        f"The Unified Harness backend does not implement {operation} yet.",
    )


__all__ = [
    "UnifiedHarnessBackendAdapter",
    "UnifiedHarnessBackendHostAdapter",
    "UnifiedSessionContext",
    "adapt_harness_host",
]
