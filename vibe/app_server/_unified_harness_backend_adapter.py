"""Translation between the Vibe app-server port and the Unified Harness."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
import contextlib
from dataclasses import dataclass, field, replace
from pathlib import Path
import time
from typing import Any, Final, Literal, Never, Protocol, cast

from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
    RustHarnessConfig,
)

# `mistralai-vibe-local-harness` is an optional extra, so an environment that never
# installs it — CI's type-check job included — cannot resolve these.
from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
    Event as HarnessEvent,
    PublicSession as HarnessPublicSession,
    PublicSessionState as HarnessPublicSessionState,
    ResolvedPluginDefinition,
    SessionReadParams as HarnessSessionReadParams,
    SessionSnapshot as HarnessSessionSnapshot,
    SessionStartParams as HarnessSessionStartParams,
    TurnEnqueueParams as HarnessTurnEnqueueParams,
    TurnQueueReadParams as HarnessTurnQueueReadParams,
    TurnQueueRemoveParams as HarnessTurnQueueRemoveParams,
    TurnQueueReplaceParams as HarnessTurnQueueReplaceParams,
    TurnQueueResumeParams as HarnessTurnQueueResumeParams,
    TurnQueueUpdatedEvent as HarnessTurnQueueUpdatedEvent,
)
from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
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
    RequestSentTelemetry,
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
from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
    sha256_json,
)
from mistralai_vibe_local_harness.vibe.plugins import (  # pyright: ignore[reportMissingImports]
    SessionPluginBinding,
)
from pydantic import ValidationError

from vibe import __version__
from vibe.app_server._account import AccountController, AccountGateway
from vibe.app_server._admin_config import (
    refresh_admin_layer,
    report_admin_config_outcome,
)
from vibe.app_server._completion_attribution import (
    CompletionAttributionHolder,
    CompletionAttributionSource,
    build_completion_attribution,
    request_call_type,
)
from vibe.app_server._config_introspect import (
    HIDDEN_SETTINGS,
    POPULAR_SETTINGS,
    build_field_wires,
    collect_layer_values,
)
from vibe.app_server._config_write import (
    config_write_ops_to_patches,
    config_write_targets,
)
from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._host import _time_ms, config_schema_response
from vibe.app_server._identity import IdentityController, IdentityGateway
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._narration import NarrationContext, NarrationService
from vibe.app_server._plugin_mcp import PluginMCPCatalog
from vibe.app_server._plugins import (
    PluginReloadUnavailableError,
    SessionPlugins,
    UnifiedPluginProvider,
    plugin_reload_notices,
)
from vibe.app_server._projection import (
    project_config_view,
    project_debug_logs,
    project_message_history,
)
from vibe.app_server._root_session import rebind_history_with_checkpoint
from vibe.app_server._session_backend_port import (
    ConnectorAuthRequest,
    MCPAuthorizationProvider,
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationSnapshot,
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
    ResolvedMCPCatalog,
    SessionBackend,
    SessionBackendError,
    SessionBackendEvent,
    SessionBackendKind,
    SessionBackendResult,
    SessionConnectorSourceState,
    SessionConnectorState,
    SessionConnectorToolDescriptor,
    SessionEventSubscription,
    SessionForkResult,
    SessionHistoryClearResult,
    SessionLifecycleResult,
    SessionMCPSourceState,
    SessionMCPState,
    SessionMCPToolDescriptor,
    SessionRewindForkResult,
)
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server._session_model import (
    active_model_is_pinned,
    active_model_override_write_requested,
    clear_session_active_model_override,
    set_session_active_model_override,
    with_session_active_model_write,
)
from vibe.app_server._state import history_page
from vibe.app_server._tool_projection import project_effect_output_value
from vibe.app_server._turn_input import session_content_blocks_from_vibe
from vibe.app_server._unified_permissions import UnifiedPermissionResolver
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    UnifiedScheduledLoops,
)
from vibe.app_server._unified_tool_observability import add_unified_tool_projection
from vibe.app_server._unified_tool_projection import (
    project_unified_history_entry,
    unified_tool_category,
)
from vibe.app_server._workspace import (
    PromptPreparationError,
    mentioned_file_content_blocks_async,
    prepare_prompt_from_context,
)
from vibe.app_server._worktree_session import SessionWorktrees
from vibe.app_server.config import ProxySettingsView
from vibe.app_server.connector_catalog import (
    ConnectorCatalogError,
    ConnectorCatalogService,
)
from vibe.app_server.events import (
    AppServerEvent,
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
    TurnQueueUpdated,
    TurnStarted,
    reconcile_snapshot,
)
from vibe.app_server.mcp_catalog import project_mcp_sources
from vibe.app_server.models import (
    AccountView,
    AgentStatsSnapshot,
    ApprovalCallbackDetail,
    ApprovalCallbackOutput,
    ApprovalDecisionType,
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
    PublicTurnQueue,
    PublicTurnStatus,
    RunningSessionStatus as VibeRunningSessionStatus,
    ScheduledLoop,
    SessionLogSummary,
    SkillEffectDetail,
    TextContentBlock,
    TokenUsage as VibeTokenUsage,
    TurnErrorCode,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    AccountReadParams,
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
    DiagnosticsLogsReadParams,
    DiagnosticsLogsReadResponse,
    EmptyResponse,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    HistoryEntryAddedParams,
    HistoryEntryUpdatedParams,
    IdentityReadParams,
    IdentityReadResponse,
    LoopsClearParams,
    LoopsClearResponse,
    LoopsCreateParams,
    LoopsCreateResponse,
    LoopsDeleteParams,
    LoopsDeleteResponse,
    LoopsListParams,
    LoopsListResponse,
    MCPAuthRequiredParams,
    NarrationSummarizeParams,
    NarrationSummarizeResponse,
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
    SessionCompactParams,
    SessionCompactResponse,
    SessionContentBlock,
    SessionContinueParams,
    SessionDeleteParams,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryClearParams,
    SessionHistoryListParams,
    SessionHistoryListResponse,
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
    SessionRewindParams,
    SessionRewindReadParams,
    SessionRewindReadResponse,
    SessionRewindResponse,
    SessionSettingsUpdateParams,
    SessionStartParams,
    SessionStopParams,
    SessionStopResponse,
    SessionTextContentBlock,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    SessionUpdatedParams,
    SkillsListParams,
    SkillsListResponse,
    StatsUpdatedParams,
    TelemetryRecordParams,
    TurnCompletedParams,
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
    TurnQueueUpdatedParams,
    TurnStartedParams,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
    TurnUserInputEntry,
    WorkspacePromptPrepareParams,
    WorkspacePromptPrepareResponse,
)
from vibe.core.agents.manager import AgentManager
from vibe.core.config import MissingAPIKeyError, VibeConfigSchema
from vibe.core.config.admin_config import MANAGED_CONFIG_TIMEOUT
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.orchestrator import ConfigOrchestrator, ConfigPatchValidationError
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse
from vibe.core.experiments.session import (
    initialize_experiments as session_initialize_experiments,
)
from vibe.core.git.errors import GitError
from vibe.core.hooks.config import load_hooks_from_fs
from vibe.core.identity_cache import IdentityCache
from vibe.core.log_reader import LogReader
from vibe.core.loop import LoopError
from vibe.core.proxy_setup import (
    SUPPORTED_PROXY_VARS,
    ProxySetupError,
    get_current_proxy_settings,
    set_proxy_var,
    unset_proxy_var,
)
from vibe.core.session.resume_sessions import (
    ResumeSessionInfo,
    list_local_resume_sessions,
    resume_directories,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.session.worktrees import ResumableDirectories
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import SkillSource
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.telemetry.send import (
    SubagentOperation,
    SubagentOutcome,
    SubagentProfileSource,
    TelemetryClient,
)
from vibe.core.telemetry.session import SessionTelemetry
from vibe.core.telemetry.types import LaunchContext
from vibe.core.tools.builtins.skill import already_loaded_message, skill_content_marker
from vibe.core.trusted_folders import has_agents_md_file
from vibe.core.types import ScheduledLoop as CoreScheduledLoop, SessionMetadata
from vibe.observability.logging import logger
from vibe.setup.auth.whoami import WhoAmICache, WhoAmIResult, resolve_user_plan
from vibe.utils import AgentEntrypoint
from vibe.utils.mcp import format_tool_display_description


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
    # The Vibe attribution the runtime reads. Scoped to the derivation, not the
    # context, so a forked session cannot rebind the source session's. The
    # adapter binds it when it adopts the derivation.
    completion_attribution: CompletionAttributionHolder = field(
        default_factory=lambda: CompletionAttributionHolder()
    )

    def __post_init__(self) -> None:
        # The runtime reaches attribution through ``adapter_config``, so point
        # the config at this derivation's holder here rather than asking every
        # construction site to pair them. Paired anywhere else, the two could
        # silently drift and the config would read a holder nobody binds.
        object.__setattr__(
            self,
            "adapter_config",
            replace(
                self.adapter_config,
                completion_metadata=self.completion_attribution.metadata,
            ),
        )


type UnifiedRuntimeDeriver = Callable[
    [UnifiedSessionSettings], UnifiedRuntimeDerivation
]
type UnifiedConfigPreflight = Callable[
    [VibeConfigSchema, UnifiedSessionSettings], Awaitable[None]
]


class CorrelationIdHolder:
    """Session-scoped holder for the last provider correlation id.

    The SDK Mistral adapter reports ``mistral-correlation-id`` through the
    runtime's ``correlation_id_sink``; telemetry forwarding reads ``value`` to
    join a ``correlate_last_request`` client event (e.g. a rating) to the exact
    provider request, at parity with the legacy backend.
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: str | None = None

    def record(self, correlation_id: str | None) -> None:
        # Mirror legacy: only advance on a real id, never clear on a header-less
        # response, so a later correlate_last_request still resolves.
        if correlation_id is not None:
            self.value = correlation_id


class RequestSentQueue:
    """Session-scoped buffer for the runtime's per-completion request telemetry.

    The runtime's ``request_sent_sink`` appends off whatever context runs the
    completion; the adapter drains it on the app loop (the only place a
    ``TelemetryClient`` can schedule) and forwards each payload to
    ``vibe.request_sent``. Buffering keeps emission on the correct loop, mirroring
    the subagent-telemetry path, at parity with the legacy loop's per-call event.
    """

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        self._pending: list[RequestSentTelemetry] = []

    def record(self, payload: RequestSentTelemetry) -> None:
        self._pending.append(payload)

    def drain(self) -> list[RequestSentTelemetry]:
        # Swap under the GIL so a concurrent ``record`` cannot lose an append.
        pending = self._pending
        self._pending = []
        return pending


class ExperimentsInitGate:
    """Serializes background experiment init against live ``/whoami`` reconciles.

    The init task can read a stale disk-cache whoami and finish *after* a live
    reconcile, clobbering the freshly reconciled ``experiment_attributes``. The
    account host awaits any in-flight init before applying its own
    ``set_attributes``, so the reconcile always lands last and wins. Adapter-held
    task, awaited through the shared context so ``_UnifiedAccountHost`` (which only
    holds the context) can coordinate without reaching into the adapter.
    """

    __slots__ = ("_task",)

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    def track(self, task: asyncio.Task[None]) -> None:
        self._task = task

    async def wait(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        # Wait for init to settle without adopting its failure or cancellation,
        # but let a cancellation of *this* coroutine propagate: ``asyncio.wait``
        # never re-raises the awaited task's outcome, only a CancelledError
        # targeting the waiter. ``suppress(BaseException)`` would instead swallow
        # the waiter's own cancellation and let a torn-down reconcile keep writing.
        await asyncio.wait({task})


class UserPlanFallback:
    """Session-scoped user_plan fallback for telemetry.

    Mirrors the legacy AgentLoop's ``_user_plan`` field: a successful account
    ``/whoami`` can populate ``user_plan`` even when experiments never stamped an
    attribute snapshot (e.g. no experiments run, or a mid-session sign-in). The
    experiment snapshot stays authoritative when present.
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: str | None = None


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

    plugin_mcp: PluginMCPCatalog

    requested_plugins: tuple[ResolvedPluginDefinition, ...]
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    harness_files: HarnessFilesManager
    agents: AgentManager
    derive: UnifiedRuntimeDeriver
    permissions: UnifiedPermissionResolver
    """Scopes an approval to the call, and remembers the ones already granted."""

    mcp_catalog: ResolvedMCPCatalog
    mcp_authorization_provider: MCPAuthorizationProvider
    mcp_cache_root: str
    mcp_enable_system_trust_store: bool
    connector_catalog: ResolvedConnectorCatalog | None = None
    connector_selection: ResolvedConnectorSelection | None = None
    connector_catalog_service: ConnectorCatalogService | None = None
    connector_base_url: str = "https://api.mistral.ai"
    connector_api_key: str = ""
    preflight: UnifiedConfigPreflight | None = None
    # Compiled user hooks for this session: Core bindings + Runtime handlers. The
    # Host registers the handlers on every lifecycle op (so a hook's command is
    # re-read live) and the bindings are supplied on start (then persisted, so the
    # set that fires stays frozen for the session's life).
    hooks: CompiledHooks = field(default_factory=CompiledHooks)
    account_gateway: AccountGateway | None = None
    identity_gateway: IdentityGateway | None = None
    # The pair ``AgentLoop`` owns on the legacy path, so the account and
    # identity hosts can name a cache without naming a backend. Experiment
    # initialization warms both, exactly as it does on legacy.
    identity_cache: IdentityCache = field(default_factory=IdentityCache)
    whoami_cache: WhoAmICache = field(default_factory=WhoAmICache)
    # The default is deliberately UNCONFIGURED — ``RemoteEvalClient()`` with no
    # URL makes ``evaluate`` a no-op — so a context built without config (tests,
    # and the throwaway contexts ``session/list`` builds) can never fire a real
    # eval. The live one is built from ``config.experiments`` in ``_runtime``.
    experiment_manager: ExperimentManager = field(default_factory=ExperimentManager)
    # Mutable holder the runtime's Mistral adapter writes the provider
    # correlation id into (via ``correlation_id_sink``); telemetry forwarding
    # reads it. Frozen field, mutable object.
    correlation: CorrelationIdHolder = field(default_factory=CorrelationIdHolder)
    # Buffer the runtime's completion adapter appends each request's shape into
    # (via ``request_sent_sink``); the adapter drains it on the app loop and
    # forwards ``vibe.request_sent``. Frozen field, mutable object.
    request_sent: RequestSentQueue = field(default_factory=RequestSentQueue)
    # Fallback user_plan from the account ladder, used when the experiment manager
    # has no attribute snapshot (mirrors the legacy ``_user_plan`` field).
    user_plan_fallback: UserPlanFallback = field(default_factory=UserPlanFallback)
    # Lets a live /whoami reconcile await the in-flight experiments-init task
    # before overwriting the attribute snapshot, so init can never clobber it.
    experiments_init_gate: ExperimentsInitGate = field(
        default_factory=ExperimentsInitGate
    )


class SessionContextBuilder(Protocol):
    def __call__(
        self,
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: AgentEntrypoint = "cli",
    ) -> Awaitable[UnifiedSessionContext]: ...


# Legacy writes the resolved response into the session's ``meta.json``. Unified's
# persisted metadata has no experiments field, so continuity comes from the global
# eval cache that ``_apply_cached_experiment_variants`` reads at build time.
class _NullExperimentSink:
    async def persist_experiments(self, response: EvalResponse | None) -> None:
        del response


# One page of the sweep's session listing. Large because the listing is
# read whole and once per repository, not shown to anyone.
_SWEEP_LISTING_PAGE = 500


_NULL_EXPERIMENT_SINK: Final = _NullExperimentSink()


def _user_plan_from_manager(manager: ExperimentManager) -> str | None:
    attributes = manager.attributes()
    if attributes is None:
        return None
    return resolve_user_plan(attributes.planType, attributes.planName)


def _user_plan_for_telemetry(
    manager: ExperimentManager, fallback: UserPlanFallback
) -> str | None:
    """Resolve user_plan for telemetry: snapshot first, account fallback second.

    The experiment attribute snapshot is authoritative when present (it can never
    disagree with the emitted ``experiment_attributes``). Otherwise fall back to
    the account ladder's plan, so a session without an experiment snapshot still
    reports a plan — parity with the legacy backend.
    """
    if manager.attributes() is not None:
        return _user_plan_from_manager(manager)
    return fallback.value


@dataclass(frozen=True, slots=True)
class _UnifiedAccountHost:
    """Presents a ``UnifiedSessionContext`` as an ``AccountHost``."""

    context: UnifiedSessionContext

    @property
    def config(self) -> VibeConfigSchema:
        return self.context.config_orchestrator.config

    @property
    def config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        return self.context.config_orchestrator

    def set_user_plan(self, user_plan: str | None) -> None:
        # The fallback the telemetry getter reads when experiments never stamped a
        # snapshot (mirrors the legacy ``_user_plan`` field).
        self.context.user_plan_fallback.value = user_plan

    async def apply_account_whoami(
        self, *, console_base_url: str, api_key: str, whoami: WhoAmIResult
    ) -> None:
        self.context.whoami_cache.populate(
            base_url=console_base_url,
            api_key=api_key,  # gitleaks:allow -- forwards a parameter, not a credential
            result=whoami,
        )
        # Keep the account fallback in sync so user_plan is reported even without
        # an experiment snapshot (e.g. a mid-session sign-in that init missed).
        self.context.user_plan_fallback.value = resolve_user_plan(
            whoami.plan_type.value, whoami.plan_name
        )
        # Reconcile the manager's snapshot with the live /whoami so telemetry
        # segmentation never diverges from the account panel (mirrors AgentLoop);
        # identity-derived fields (user/org/workspace) are preserved. Await any
        # in-flight experiments init first so this live snapshot lands last and a
        # stale-cache init can never clobber it.
        await self.context.experiments_init_gate.wait()
        current = self.context.experiment_manager.attributes()
        if current is not None:
            self.context.experiment_manager.set_attributes(
                current.model_copy(
                    update={
                        "planType": whoami.plan_type.value,
                        "planName": whoami.plan_name,
                        "customerId": whoami.customer_id,
                        "organizationKind": whoami.organization_kind,
                    }
                )
            )

    async def clear_account_whoami(self, *, api_key: str) -> None:
        self.context.whoami_cache.invalidate(api_key)
        self.context.user_plan_fallback.value = None
        # Same ordering guarantee as the reconcile: a sign-out must not be undone
        # by an init task that started before it.
        await self.context.experiments_init_gate.wait()
        current = self.context.experiment_manager.attributes()
        if current is not None:
            self.context.experiment_manager.set_attributes(
                current.model_copy(
                    update={
                        "planType": None,
                        "planName": None,
                        "customerId": None,
                        "organizationKind": None,
                    }
                )
            )


@dataclass(frozen=True, slots=True)
class _UnifiedIdentityHost:
    """Presents a ``UnifiedSessionContext`` as an ``IdentityHost``."""

    context: UnifiedSessionContext

    @property
    def config(self) -> VibeConfigSchema:
        return self.context.config_orchestrator.config

    @property
    def identity_cache(self) -> IdentityCache:
        return self.context.identity_cache


type LaunchContextGetter = Callable[[], LaunchContext | None]
type SessionReplacementCallback = Callable[
    [str, str, "UnifiedHarnessBackendAdapter"], None
]

# The one write path pinning has: reload rescans and hands the result to the
# same `config/write` the Host uses at `session/start`. Unbound, so nothing has
# to close over a session id that does not exist until the adapter is built.
type PluginRewrite = Callable[
    [str, Sequence[ResolvedPluginDefinition]], Awaitable[SessionPluginBinding]
]


def adapt_harness_host(
    host: object,
    build_session_context: SessionContextBuilder,
    services: SessionBackendServices | None = None,
    *,
    launch_context_getter: LaunchContextGetter | None = None,
) -> UnifiedHarnessBackendHostAdapter:
    return UnifiedHarnessBackendHostAdapter(
        cast(UnifiedHarnessSessionBackendHost, host),
        build_session_context,
        services,
        launch_context_getter=launch_context_getter,
    )


class UnifiedHarnessBackendHostAdapter:
    """Vibe's session Host backed by the Unified Harness Runtime."""

    def __init__(
        self,
        host: UnifiedHarnessSessionBackendHost,
        build_context: SessionContextBuilder,
        services: SessionBackendServices | None = None,
        *,
        launch_context_getter: LaunchContextGetter | None = None,
    ) -> None:
        self._host = host
        self._build_context = build_context
        self._services = services
        self._launch_context_getter = launch_context_getter
        self._telemetry_clients: set[TelemetryClient] = set()
        self._adapters: dict[str, UnifiedHarnessBackendAdapter] = {}
        # Host-lived, because which repositories have been swept should outlast
        # any one session: two opened in the same one must not both sweep it.
        self._worktrees = SessionWorktrees()

    @property
    def harness_kind(self) -> SessionBackendKind:
        return self._host.harness_kind

    async def start(self, params: SessionStartParams) -> SessionLifecycleResult:
        # Before the context, which is rooted at the working directory this
        # decides. The resolved directory travels as rewritten options, so
        # everything downstream reads it the way it always has.
        try:
            resolution = await self._worktrees.resolve_for_start(params.agent_config)
        except GitError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        options = resolution.options
        try:
            return await self._start_resolved(params, options)
        except BaseException:
            # A worktree raised for a session that never started is a directory
            # nobody will come back for.
            await self._worktrees.cleanup(resolution)
            raise

    async def _start_resolved(
        self, params: SessionStartParams, options: SessionOptions
    ) -> SessionLifecycleResult:
        context, derivation = await self._context(options, require_api_key=True)
        session = await _harness_call(
            self._host.start(
                HarnessSessionStartParams(history_limit=params.history_limit),
                cwd=_session_cwd(options),
                # A public session identity is live immediately, but it only becomes
                # durable once its first turn starts.  The Harness's ephemeral path
                # already owns that promotion boundary and discards an unused session
                # on shutdown.
                ephemeral=True,
                hook_bindings=context.hooks.bindings,
                hook_handlers=context.hooks.handlers,
            )
        )
        backend = self._adapter(
            session,
            _session_cwd(options),
            context,
            derivation,
            scheduled_loops_enabled=not options.headless,
        )
        await self._prepare_opened_backend(backend, backend.restore_scheduled_loops())
        # Fresh start emits new_session/ready once the background experiment eval
        # resolves, so those events carry user_plan/experiment_attributes — the
        # only path that emits both, mirroring the legacy AgentLoop.
        self._start_experiments(backend, after=backend._emit_fresh_start_telemetry)
        return SessionLifecycleResult(
            backend=backend, after_response=backend.start_scheduled_loops
        )

    async def resume(self, params: SessionResumeParams) -> SessionLifecycleResult:
        self._worktrees.reject_input(params.agent_config)
        context, derivation = await self._context(
            params.agent_config, require_api_key=True
        )
        context, derivation = await self._pin_to_session_cwd(
            params.agent_config,
            params.session_id,
            context,
            derivation,
            require_api_key=True,
        )
        session = await _harness_call(
            self._host.resume(
                params.session_id,
                history_limit=params.history_limit,
                hook_bindings=context.hooks.bindings,
                hook_handlers=context.hooks.handlers,
            )
        )
        backend = self._adapter(
            session,
            session.cwd,
            context,
            derivation,
            scheduled_loops_enabled=not params.agent_config.headless,
        )
        await self._prepare_opened_backend(backend, backend.restore_scheduled_loops())
        self._start_experiments(backend)
        return SessionLifecycleResult(
            backend=backend, after_response=backend.start_scheduled_loops
        )

    async def continue_latest(
        self, params: SessionContinueParams
    ) -> SessionLifecycleResult:
        # Resolve the latest session once and resume it by id, rather than pinning
        # cwd/hooks off one listing and letting Host.continue_latest list again: a
        # session created between the two lists would otherwise be resumed with hooks
        # compiled for a different project's cwd (and a clean rebind would persist them
        # on the wrong session). The first _context configures the Host (storage/legacy),
        # which list and session_cwd need.
        self._worktrees.reject_input(params.agent_config)
        context, derivation = await self._context(
            params.agent_config, require_api_key=True
        )
        listing = await _harness_call(self._host.list(limit=1))
        target_id = listing.continue_session_id
        if target_id is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, "No session to continue"
            )
        context, derivation = await self._pin_to_session_cwd(
            params.agent_config, target_id, context, derivation, require_api_key=True
        )
        session = await _harness_call(
            self._host.resume(
                target_id,
                history_limit=params.history_limit,
                hook_bindings=context.hooks.bindings,
                hook_handlers=context.hooks.handlers,
            )
        )
        backend = self._adapter(
            session,
            session.cwd,
            context,
            derivation,
            scheduled_loops_enabled=not params.agent_config.headless,
        )
        await self._prepare_opened_backend(backend, backend.restore_scheduled_loops())
        self._start_experiments(backend)
        return SessionLifecycleResult(
            backend=backend, after_response=backend.start_scheduled_loops
        )

    async def fork(self, params: SessionForkParams) -> SessionForkResult:
        options = params.agent_config or SessionOptions()
        source = self._adapters.get(params.source_session_id)
        if source is not None and source._closed:
            source = None
        context, derivation = await self._context(options, require_api_key=True)
        context, derivation = await self._pin_to_session_cwd(
            options, params.source_session_id, context, derivation, require_api_key=True
        )
        result = await _harness_call(
            self._host.fork(
                params.source_session_id,
                entry_id=params.entry_id,
                history_limit=params.history_limit,
                hook_handlers=context.hooks.handlers,
            )
        )
        backend = self._adapter(
            result.session,
            result.session.cwd,
            context,
            derivation,
            scheduled_loops_enabled=not options.headless,
        )
        await self._prepare_opened_backend(
            backend,
            (
                backend.replace_scheduled_loops(await source.scheduled_loops())
                if source is not None
                else backend.copy_scheduled_loops(params.source_session_id)
            ),
        )
        self._start_experiments(backend)
        snapshot = await backend.read(
            SessionReadParams(
                session_id=backend.session_id,
                history=PageRequest(limit=params.history_limit),
            )
        )
        attached: UnifiedHarnessBackendAdapter | None = backend
        if not params.attach:
            # This fork was never announced (no new_session), so tear it down
            # quietly rather than emitting a lone session_closed.
            backend._telemetry_closed = True
            await backend.shutdown()
            attached = None
        return SessionForkResult(
            response=SessionForkResponse(
                source_session_id=params.source_session_id,
                state=snapshot.state,
                last_event_id=snapshot.last_event_id,
            ),
            backend=attached,
            after_response=(
                backend.start_scheduled_loops if attached is not None else None
            ),
        )

    async def list(self, params: SessionListParams) -> SessionListResponse:
        options = SessionOptions(cwd=params.cwd)
        # Listing sessions has never needed a credential; the legacy path
        # answers it through structurally loaded configuration without credential
        # validation.
        context, _ = await self._context(options, require_api_key=False)

        # Fetch ALL unified sessions by paginating through the unified host.
        # The cursor the client passes is opaque to the client and consumed
        # here; the merged list's own cursor is what the client sees.
        unified_items: list[PublicSession] = []
        unified_cursor: str | None = None
        unified_continue_session_id: str | None = None
        while True:
            result = await _harness_call(
                self._host.list(
                    limit=_SWEEP_LISTING_PAGE,
                    cursor=unified_cursor,
                    cwd=_session_cwd(options) if params.cwd is not None else None,
                    root_session_id=params.root_session_id,
                    parent_session_id=params.parent_session_id,
                )
            )
            unified_items.extend(
                _public_session(item.session, item.cwd, harness="unified")
                for item in result.items
            )
            if unified_continue_session_id is None:
                unified_continue_session_id = result.continue_session_id
            if result.next_cursor is None or not result.items:
                break
            unified_cursor = result.next_cursor

        # Fetch ALL legacy sessions in one filesystem read. The legacy store
        # is a secondary source: if the read fails the picker degrades to
        # unified-only rather than blocking the primary listing. Lineage
        # filters (root/parent) are unified-only: legacy sessions carry no
        # root_session_id, so they are excluded from those queries entirely.
        config = context.config_orchestrator.config
        legacy_items: list[PublicSession] = []
        if params.root_session_id is None and params.parent_session_id is None:
            try:
                legacy_sessions = await asyncio.to_thread(
                    list_local_resume_sessions, config, params.cwd
                )
            except OSError:
                logger.debug("Legacy session listing failed; returning unified-only")
            else:
                legacy_items = [
                    _legacy_public_session(session, config, harness="legacy")
                    for session in legacy_sessions
                ]

        # Merge, sort by (updated_at, session_id) descending, and apply
        # client-side pagination on the merged result. Session IDs are UUIDs,
        # so the (updated_at, id) key is unique across both stores.
        merged = sorted(
            [*unified_items, *legacy_items],
            key=lambda s: (s.updated_at, s.id),
            reverse=True,
        )
        start = _merged_cursor_index(merged, params.cursor)
        page = merged[start : start + params.limit]
        return SessionListResponse(
            items=page,
            next_cursor=(
                _encode_merged_cursor(page[-1])
                if start + len(page) < len(merged) and page
                else None
            ),
            previous_cursor=None,
            continue_session_id=unified_continue_session_id,
        )

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        options = SessionOptions()
        context, _ = await self._context(options, require_api_key=False)
        try:
            result = await _harness_call(self._host.read(_harness_read_params(params)))
            return _read_response(result.snapshot, result.cwd)
        except SessionBackendError as exc:
            if exc.code is not ProtocolErrorCode.NOT_FOUND:
                raise
        # The unified store has no record of this session. If it is a legacy
        # session (listed by the merged ``list`` but not yet imported), read it
        # directly from the legacy store so the picker preview works.
        legacy_response = await asyncio.to_thread(
            _read_legacy_session, params, context.config_orchestrator.config
        )
        if legacy_response is not None:
            return legacy_response
        raise SessionBackendError(
            ProtocolErrorCode.NOT_FOUND, f"Session not found: {params.session_id}"
        )

    async def rename(
        self, params: SessionTitleUpdateParams
    ) -> SessionTitleUpdateResponse:
        await self._context(SessionOptions(), require_api_key=False)
        snapshot = await _harness_call(
            self._host.rename(params.session_id, params.title)
        )
        title = snapshot.state.session.title
        if title is None:
            raise RuntimeError("The session title was not updated")
        return SessionTitleUpdateResponse(title=title, last_event_id=snapshot.watermark)

    async def delete(self, params: SessionDeleteParams) -> EmptyResponse:
        await self._context(SessionOptions(), require_api_key=False)
        await _harness_call(self._host.delete(params.session_id))
        adapter = self._adapters.pop(params.session_id, None)
        if adapter is not None:
            self._telemetry_clients.discard(adapter._telemetry)
            await adapter.shutdown()
        return EmptyResponse()

    async def rewind_fork(
        self, source: SessionBackend, params: SessionRewindParams
    ) -> SessionRewindForkResult:
        """Rewind into a fresh session, leaving the source conversation whole.

        The Harness already forks a stored session at a history anchor, which
        is this operation once the anchor turn itself is excluded: the child
        inherits the transcript from before the rewound message and names the
        source as its parent, and the source keeps every turn that was rewound
        past. Built over the source's own context — the rewound conversation
        continues under the settings it was recorded with, not whatever a fresh
        derivation would resolve now.
        """
        if not isinstance(source, UnifiedHarnessBackendAdapter):
            raise TypeError("Unified Harness rewind requires a Unified Harness session")
        source._require_session(params.session_id)
        await source._require_rewindable(params)
        history_limit = 200
        message = await source._rewound_message(params.entry_id)
        derivation = source._context.derive(source._settings)
        self._host.configure_runtime(
            derivation.core_config, adapter_config=derivation.adapter_config
        )
        result = await _harness_call(
            self._host.fork(
                params.session_id,
                entry_id=params.entry_id,
                include_entry=False,
                history_limit=history_limit,
                hook_handlers=source._context.hooks.handlers,
            )
        )
        backend = self._adapter(
            result.session,
            result.session.cwd,
            source._context,
            derivation,
            scheduled_loops_enabled=source._scheduled_loops_enabled,
        )
        await self._prepare_opened_backend(
            backend, backend.replace_scheduled_loops(await source.scheduled_loops())
        )
        self._start_experiments(backend)
        state = (
            await backend.read(
                SessionReadParams(
                    session_id=backend.session_id,
                    history=PageRequest(limit=history_limit),
                )
            )
        ).state
        # The child's own entries, not the source's: a later rewind anchors on
        # what this session's projection actually holds.
        history = rebind_history_with_checkpoint(
            state.history or [],
            backend.session_id,
            kind="rewind",
            message="Conversation rewound",
            details={
                "entryId": params.entry_id,
                "restoreFiles": params.restore_files,
                "inplace": params.inplace,
            },
        )
        return SessionRewindForkResult(
            backend=backend,
            response=SessionRewindResponse(
                message=message,
                restore_errors=[],
                restored_paths=[],
                state=state.model_copy(update={"history": history}),
                session_log=await backend._session_log_summary(),
            ),
            after_response=backend.start_scheduled_loops,
        )

    async def clear_history(
        self, source: SessionBackend, params: SessionHistoryClearParams
    ) -> SessionHistoryClearResult:
        if not isinstance(source, UnifiedHarnessBackendAdapter):
            raise TypeError("Unified Harness clear requires a Unified Harness session")
        source._require_session(params.session_id)
        source._require_idle()
        if source._experiments_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await source._experiments_task
        history_limit = 200
        source_state = (
            await source.read(
                SessionReadParams(
                    session_id=params.session_id,
                    history=PageRequest(limit=history_limit),
                )
            )
        ).state
        if source._session.active_model is not None:
            failures = await clear_session_active_model_override(
                source._context.config_orchestrator, reason="clear session active model"
            )
            if failures:
                raise SessionBackendError(
                    ProtocolErrorCode.INTERNAL_ERROR,
                    f"Failed to clear session active model: {failures[0]}",
                )
        derivation = source._context.derive(source._settings)
        self._host.configure_runtime(
            derivation.core_config, adapter_config=derivation.adapter_config
        )
        session = await _harness_call(
            self._host.start(
                HarnessSessionStartParams(history_limit=history_limit),
                cwd=source._cwd,
                ephemeral=True,
                hook_bindings=source._context.hooks.bindings,
                hook_handlers=source._context.hooks.handlers,
            )
        )
        backend = self._adapter(
            session,
            source._cwd,
            source._context,
            derivation,
            scheduled_loops_enabled=source._scheduled_loops_enabled,
            launch_context=source._launch_context,
            user_plan=source._user_plan,
        )
        await self._prepare_opened_backend(
            backend, backend.replace_scheduled_loops(await source.scheduled_loops())
        )
        replacement_state = (
            await backend.read(
                SessionReadParams(
                    session_id=backend.session_id,
                    history=PageRequest(limit=history_limit),
                )
            )
        ).state
        history = rebind_history_with_checkpoint(
            source_state.history or [],
            backend.session_id,
            kind="clear",
            message="New conversation started",
        )
        history_before_cursor = source_state.history_before_cursor
        if len(history) > history_limit:
            history = history[-history_limit:]
            history_before_cursor = history[0].id
        # Clearing history starts a brand-new harness session that replaces the
        # root, so it emits new_session like the legacy /clear reset (ready stays
        # a once-per-process init event). The replacement shares the source's
        # experiment manager; await any in-flight eval first so a /clear issued
        # before init finished still emits new_session with a resolved snapshot
        # rather than empty segmentation.
        source_task = source._experiments_task
        if source_task is not None:
            with contextlib.suppress(BaseException):
                await source_task
        # `/clear` starts a new root: legacy `_reset_session(keep_parent=False)`
        # leaves parent_session_id unset (unlike rewind, which chains to the old
        # session). The replacement's cached parent is already None from the open.
        backend._emit_new_session_telemetry()
        return SessionHistoryClearResult(
            backend=backend,
            state=replacement_state.model_copy(
                update={
                    "history": history,
                    "history_before_cursor": history_before_cursor,
                }
            ),
            session_log=await backend._session_log_summary(),
            after_response=backend.start_scheduled_loops,
        )

    async def shutdown(self) -> None:
        # `session/stop` arrives here rather than at the session adapter, so a
        # release left only to the adapter would never run on the ordinary way
        # out. Before the Harness close, which can be slow, and while the
        # adapters are still around to say where they were standing.
        self._release_worktrees()
        try:
            await self._host.shutdown()
        finally:
            # Announce the close of any session still live at process exit. This
            # path (unlike session replacement) never calls the adapter's own
            # shutdown, so cancel each in-flight eval before emitting close — its
            # ``after`` callback must not fire lifecycle events after this point.
            # The flag keeps session_closed to once per session.
            for adapter in self._adapters.values():
                adapter._cancel_experiments_task()
                adapter._emit_session_closed_telemetry()
            self._adapters.clear()
            clients = tuple(self._telemetry_clients)
            self._telemetry_clients.clear()
            await asyncio.gather(
                *(client.aclose() for client in clients), return_exceptions=True
            )

    def _release_worktrees(self) -> None:
        for backend in self._adapters.values():
            if backend._cwd is None:
                continue
            self._worktrees.release(Path(backend._cwd), backend.session_id)

    def _adapter(
        self,
        session: UnifiedHarnessSessionBackend,
        cwd: str | None,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        scheduled_loops_enabled: bool,
        launch_context: LaunchContext | None = None,
        user_plan: str | None = None,
    ) -> UnifiedHarnessBackendAdapter:
        existing = self._adapters.get(session.session_id)
        if existing is not None and existing._session is session:
            return existing
        # Segmentation comes from the session's experiment manager (the same
        # source the legacy AgentLoop reads), so every emitted event carries the
        # user's plan, attribute snapshot, and GrowthBook assignments. The
        # backend identity is stamped statically via harness_backend so it rides
        # every event even when experiments are off; it also appears inside
        # experiment_attributes.harness when a snapshot is present.
        manager = context.experiment_manager
        telemetry_launch_context = (
            self._launch_context_getter()
            if self._launch_context_getter is not None
            else None
        )
        telemetry = TelemetryClient(
            config_getter=lambda: context.config_orchestrator.config,
            session_id_getter=lambda: session.session_id,
            launch_context=telemetry_launch_context,
            experiments_getter=manager.assignments,
            user_plan_getter=lambda: _user_plan_for_telemetry(
                manager, context.user_plan_fallback
            ),
            experiment_attributes_getter=manager.attributes,
            harness_backend=ExperimentSurface.UNIFIED,
        )
        self._telemetry_clients.add(telemetry)
        adapter = UnifiedHarnessBackendAdapter(
            session,
            cwd,
            context,
            derivation,
            host=self._host,
            telemetry_client=telemetry,
            session_replaced=self._session_replaced,
            rewrite_plugins=self._host.rewrite_session_plugins,
            scheduled_loops_enabled=scheduled_loops_enabled,
            launch_context=launch_context,
            user_plan=user_plan,
            # This session's own attribution. The adapter binds it into every
            # derivation it adopts, so a fork never reaches back into this one.
            completion_attribution=build_completion_attribution(
                telemetry, telemetry_launch_context
            ),
        )
        self._adapters[session.session_id] = adapter
        return adapter

    def _session_replaced(
        self,
        previous_session_id: str,
        session_id: str,
        adapter: UnifiedHarnessBackendAdapter,
    ) -> None:
        if self._adapters.get(previous_session_id) is adapter:
            self._adapters.pop(previous_session_id)
        self._adapters[session_id] = adapter

    # Called per lifecycle entry point rather than from
    # ``UnifiedHarnessBackendAdapter.__init__``, because ``clear_history``
    # builds a replacement adapter over the *source's* context: firing there
    # would run a second eval against an already-resolved manager.
    def _start_experiments(
        self,
        backend: UnifiedHarnessBackendAdapter,
        *,
        after: Callable[[], None] | None = None,
    ) -> None:
        services = self._services
        if services is None:
            # No client identity to resolve experiments against; still let the
            # caller emit its lifecycle telemetry (with an empty snapshot).
            if after is not None:
                after()
            return
        client_info = services.client_info()

        async def run() -> None:
            await backend.initialize_experiments(
                build_launch_context(
                    agent_entrypoint=client_info.entrypoint,
                    agent_version=__version__,
                    client_name=client_info.name,
                    client_version=client_info.version,
                    terminal_emulator=client_info.terminal_emulator,
                )
            )
            # Emit after the eval so new_session/ready carry the resolved
            # user_plan and experiment_attributes.
            if after is not None:
                after()

        task = asyncio.create_task(run())
        # ``task_finished`` is a *done* callback, so it must be registered, not
        # called. The backend holds the reference so the task is not collected
        # mid-flight, and cancels it on close.
        backend._experiments_task = task
        # Let a live /whoami reconcile await this init before it overwrites the
        # attribute snapshot, closing the stale-cache clobber window.
        backend._context.experiments_init_gate.track(task)
        task.add_done_callback(services.task_finished)

    async def _prepare_opened_backend(
        self, backend: UnifiedHarnessBackendAdapter, prepare: Awaitable[None]
    ) -> None:
        try:
            await prepare
            await backend.initialize_integrations()
            await backend._cache_parent_session_id()
        except BaseException:
            try:
                # The open failed before ``_start_experiments`` announced the
                # session, so suppress session_closed — otherwise analytics get a
                # lone close with no matching new_session (same as an unattached
                # fork).
                backend._telemetry_closed = True
                await backend.shutdown()
            except BaseException as exc:
                logger.warning(
                    "Failed to close Unified session after opening failed", exc_info=exc
                )
            raise
        self._occupy_worktree(backend)

    # Where start, resume and continue all end up, which is why the marking
    # happens here rather than three times over. Only after the open succeeded:
    # a session that failed to open is not standing anywhere.
    def _occupy_worktree(self, backend: UnifiedHarnessBackendAdapter) -> None:
        if backend._cwd is None:
            return
        cwd = Path(backend._cwd)
        self._worktrees.hold(cwd, backend.session_id)
        self._worktrees.start_sweep(
            cwd,
            self._resumable_directories(backend._context.config_orchestrator.config),
        )

    def _resumable_directories(self, config: VibeConfigSchema) -> ResumableDirectories:
        """Where a saved session would resume into, for the sweep.

        Both stores, because the worktrees on disk are not split the way the
        sessions are. The legacy session index cannot see a harness session --
        it globs one level under the save dir for a session prefix, and a
        harness session lives another level down under `unified/` -- and the
        harness listing cannot see a legacy one. A sweep told only one of them
        reclaims the checkouts of the other, which is what turns trying the
        experimental harness and going back into a row of sessions pointed at
        directories that are gone.

        Read to the end and with no cwd filter. A worktree of this repository
        can hold a session started from anywhere, and one omitted here is one
        the sweep is free to delete.
        """

        async def _resumable() -> tuple[Path, ...]:
            legacy = await asyncio.to_thread(resume_directories, config)
            return (*legacy, *await self._harness_resumable_directories())

        return _resumable

    async def _harness_resumable_directories(self) -> tuple[Path, ...]:
        directories: list[Path] = []
        cursor: str | None = None
        while True:
            page = await _harness_call(
                self._host.list(
                    limit=_SWEEP_LISTING_PAGE,
                    cursor=cursor,
                    cwd=None,
                    root_session_id=None,
                    parent_session_id=None,
                )
            )
            directories.extend(Path(item.cwd) for item in page.items if item.cwd)
            # A cursor that does not advance would page forever. The store
            # answers None at the end; an empty page is the belt to that brace.
            if page.next_cursor is None or not page.items:
                return tuple(directories)
            cursor = page.next_cursor

    async def _pin_to_session_cwd(
        self,
        options: SessionOptions,
        session_id: str,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        require_api_key: bool,
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation]:
        """Rebuild the context against an existing session's stored cwd, if it differs.

        The caller must have already built ``context`` once (which configures the Host's
        storage/legacy resolver — what ``session_cwd`` needs). If the session was created
        in a different cwd than the caller's, rebuild so hooks are discovered and compiled
        against the session's own cwd; binding ids embed it, so a resume/fork otherwise
        skips every persisted hook or binds the wrong project's hooks (§7 as-built).
        """
        stored_cwd = await _harness_call(self._host.session_cwd(session_id))
        if stored_cwd is not None and stored_cwd != _session_cwd(options):
            if options.trust_workspace:
                # The first build recorded an ephemeral --trust grant for the caller cwd on
                # the process-wide trust store. Its ancestor walk would still trust the pinned
                # (often descendant) session cwd, so revoke that one grant before rebuilding.
                context.harness_files.trust_store.revoke_session_trust(
                    Path(_session_cwd(options))
                )
            context, derivation = await self._context(
                _with_session_cwd(options, stored_cwd), require_api_key=require_api_key
            )
        active_model = await _harness_call(self._host.session_active_model(session_id))
        if active_model is None:
            return context, derivation
        failures = await set_session_active_model_override(
            context.config_orchestrator,
            active_model,
            reason="restore session active model",
        )
        if failures:
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR,
                f"Failed to restore session active model: {failures[0]}",
            )
        derivation = await asyncio.to_thread(context.derive, UnifiedSessionSettings())
        self._host.configure_runtime(
            derivation.core_config, adapter_config=derivation.adapter_config
        )
        return context, derivation

    def _entrypoint(self) -> AgentEntrypoint:
        if self._services is None:
            return "cli"
        return self._services.client_info().entrypoint

    async def _context(
        self, options: SessionOptions, *, require_api_key: bool
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation]:
        try:
            context = await self._build_context(
                options, require_api_key=require_api_key, entrypoint=self._entrypoint()
            )
        except MissingAPIKeyError as exc:
            # The shape ACP reads to offer a sign-in instead of reporting a
            # configuration error. Identical to what the legacy backend sends.
            raise SessionBackendError(
                ProtocolErrorCode.UNAUTHORIZED,
                str(exc),
                {"provider": exc.provider_name},
            ) from exc
        if self._services is not None:
            # The gateway seam already exists on ``SessionBackendServices``; the
            # legacy backend has always been handed both. Attaching them here
            # keeps the context builder free of the request-scoped services.
            context = replace(
                context,
                account_gateway=self._services.account_gateway(),
                identity_gateway=self._services.identity_gateway(),
            )
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
            gateway_authority_digest=sha256_json({
                "base_url": context.connector_base_url,
                "enable_system_trust_store": context.mcp_enable_system_trust_store,
            }),
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

    @property
    def plugin_mcp_catalog(self) -> PluginMCPCatalog:
        # Plugin sources are connected on this side and absent from the Harness
        # snapshot, so their status is read here rather than off ``read_mcp``.
        return self._context.plugin_mcp

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
        # Carry connector discovery errors forward, but let the incoming server state be
        # authoritative for any name it also covers. Without the `not in server_names`
        # guard, a server error kept under a name that also aliases a connector (e.g.
        # both an MCP server and a connector named "buildkite") would be re-applied as a
        # connector error and freeze a stale warning after the server recovered.
        server_names = {source.name for source in server_sources}
        connector_names = {source.name for source in connector_sources}
        connector_discovery_errors = {
            name: error
            for name, error in self._runtime.mcp.discovery_errors.items()
            if name in connector_names and name not in server_names
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
        # Keep MCP-server discovery errors (keyed by server name) and refresh the
        # connector ones from the incoming state. Filtering by server name -- rather
        # than "not a connector alias" -- preserves a server's error even when a
        # connector shares its name (e.g. an MCP server and a connector both named
        # "buildkite"), which previously dropped the server error entirely.
        server_names = {source.name for source in server_sources}
        discovery_errors = {
            key: value
            for key, value in self._runtime.mcp.discovery_errors.items()
            if key in server_names
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


class _UnifiedScheduledLoopsAdapter:
    _session: UnifiedHarnessSessionBackend
    _storage_root: str
    _scheduled_loops: UnifiedScheduledLoops
    _scheduled_loops_enabled: bool
    _scheduled_loops_task: asyncio.Task[None] | None
    _pending_startup_notices: list[str]
    _defer_event_flush: bool

    @property
    def session_id(self) -> str:
        return self._session.session_id

    async def restore_scheduled_loops(self) -> None:
        try:
            await self._scheduled_loops.restore()
        except ScheduledLoopStoreError as exc:
            try:
                quarantine_path = await self._scheduled_loops.quarantine_corrupt_store()
            except ScheduledLoopStoreError as recovery_exc:
                raise SessionBackendError(
                    ProtocolErrorCode.INTERNAL_ERROR, f"{exc}. {recovery_exc}"
                ) from recovery_exc
            logger.warning("Quarantined corrupt scheduled loops", exc_info=exc)
            self._pending_startup_notices.append(
                "Saved scheduled loops could not be restored. "
                f"They were disabled and preserved at {quarantine_path}."
            )

    async def copy_scheduled_loops(self, source_session_id: str) -> None:
        source = UnifiedScheduledLoops(
            Path(self._storage_root)
            / "unified"
            / source_session_id
            / "scheduled-loops.json",
            persistent=lambda: False,
        )
        try:
            await source.restore()
        except ScheduledLoopStoreError as exc:
            try:
                quarantine_path = await source.quarantine_corrupt_store()
            except ScheduledLoopStoreError as recovery_exc:
                raise SessionBackendError(
                    ProtocolErrorCode.INTERNAL_ERROR, f"{exc}. {recovery_exc}"
                ) from recovery_exc
            logger.warning(
                "Quarantined corrupt scheduled loops while forking", exc_info=exc
            )
            self._pending_startup_notices.append(
                "Saved scheduled loops from the source session could not be copied. "
                f"They were disabled and preserved at {quarantine_path}."
            )
        try:
            await self._scheduled_loops.replace(await source.list())
        except ScheduledLoopStoreError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR, str(exc)
            ) from exc

    async def replace_scheduled_loops(self, loops: list[CoreScheduledLoop]) -> None:
        try:
            await self._scheduled_loops.replace(loops)
        except ScheduledLoopStoreError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR, str(exc)
            ) from exc

    async def scheduled_loops(self) -> list[CoreScheduledLoop]:
        return await self._scheduled_loops.list()

    async def _persist_scheduled_loops_after_promotion(
        self, *, was_ephemeral: bool
    ) -> None:
        if not was_ephemeral or bool(getattr(self._session, "ephemeral", True)):
            return
        try:
            await self._scheduled_loops.persist()
        except ScheduledLoopStoreError as exc:
            logger.warning(
                "Failed to persist scheduled loops after session promotion",
                exc_info=exc,
            )
            self._session.publish_notice(
                "The session was saved, but its scheduled loops could not be saved. "
                "They remain active until this session closes."
            )

    def start_scheduled_loops(self) -> None:
        if not self._scheduled_loops_enabled:
            return
        task = self._scheduled_loops_task
        if task is not None and not task.done():
            return
        self._scheduled_loops_task = asyncio.create_task(
            self._run_scheduled_loops(), name="vibe-unified-scheduled-loops"
        )

    async def _run_scheduled_loops(self) -> None:
        while True:
            try:
                delay = await self._scheduled_loops.next_due_in()
                await asyncio.sleep(max(0.05, min(delay, 1.0)))
                if self._session.active_turn_id is not None or self._defer_event_flush:
                    continue
                scheduled = await self._scheduled_loops.due()
                if scheduled is None:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduled loop failed", exc_info=exc)
                self._session.publish_notice(
                    f"Scheduled loop failed: {exc}", level="error"
                )
                continue

            try:
                await self._start_scheduled_loop(scheduled)
            except asyncio.CancelledError:
                raise
            except SessionBackendError as exc:
                if exc.code is ProtocolErrorCode.CONFLICT:
                    continue
                self._session.publish_notice(
                    f"Scheduled loop failed: {exc}", level="error"
                )
            except Exception as exc:
                logger.exception("Scheduled loop failed", exc_info=exc)
                self._session.publish_notice(
                    f"Scheduled loop failed: {exc}", level="error"
                )
            await self._mark_scheduled_loop_attempted(scheduled)

    async def _mark_scheduled_loop_attempted(
        self, scheduled: CoreScheduledLoop
    ) -> None:
        try:
            await self._scheduled_loops.mark_fired(scheduled.id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Failed to reschedule scheduled loop", exc_info=exc)
            self._session.publish_notice(
                f"Scheduled loop could not be rescheduled: {exc}", level="error"
            )
            # The durable mutation is atomic, so a failed write deliberately leaves the
            # loop due in memory. Back off by its interval to avoid immediately starting
            # duplicate turns while its schedule cannot be persisted.
            await asyncio.sleep(scheduled.interval_seconds)

    async def _start_scheduled_loop(self, scheduled: CoreScheduledLoop) -> None:
        await self._flush_pending_derivation()
        params = await self._prepared_turn_params(
            TurnStartParams(
                session_id=self.session_id,
                message=[TextContentBlock(text=scheduled.prompt)],
            ),
            inject_skill=True,
        )
        result = cast(
            Any,
            await _harness_call(
                self._session.start_scheduled_turn(params, scheduled.id)
            ),
        )
        if result.after_response is not None:
            result.after_response()

    async def _dispatch_scheduled_loops(
        self, method: str, raw_params: dict[str, Any]
    ) -> ProtocolModel:
        try:
            match method:
                case "loops/list":
                    params = validate_wire(LoopsListParams, raw_params)
                    self._require_session(params.session_id)
                    return LoopsListResponse(
                        loops=[
                            _project_scheduled_loop(loop)
                            for loop in await self._scheduled_loops.list()
                        ]
                    )
                case "loops/create":
                    params = validate_wire(LoopsCreateParams, raw_params)
                    self._require_session(params.session_id)
                    self._require_idle()
                    loop = await self._scheduled_loops.create(
                        params.interval, params.prompt
                    )
                    return LoopsCreateResponse(loop=_project_scheduled_loop(loop))
                case "loops/delete":
                    params = validate_wire(LoopsDeleteParams, raw_params)
                    self._require_session(params.session_id)
                    self._require_idle()
                    loop = await self._scheduled_loops.delete(params.loop_id)
                    return LoopsDeleteResponse(loop=_project_scheduled_loop(loop))
                case "loops/clear":
                    params = validate_wire(LoopsClearParams, raw_params)
                    self._require_session(params.session_id)
                    self._require_idle()
                    return LoopsClearResponse(count=await self._scheduled_loops.clear())
                case _:
                    raise method_not_found(method)
        except LoopError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        except ScheduledLoopStoreError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR, str(exc)
            ) from exc

    def _require_session(self, session_id: str) -> None: ...

    def _require_idle(self) -> None: ...

    async def _flush_pending_derivation(self) -> None: ...

    async def _prepared_turn_params[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT, *, inject_skill: bool
    ) -> ParamsT: ...


class UnifiedHarnessBackendAdapter(  # noqa: PLR0904 - implements app-server session operations
    _UnifiedHarnessMCPAdapter,
    _UnifiedHarnessConnectorAdapter,
    _UnifiedScheduledLoopsAdapter,
):
    async def initialize_integrations(self) -> None:
        # Plugin servers are bound during ``prepare``, awaited before this, so
        # the startup notice about a waiting login can see their statuses.
        sources, discovery_errors = project_mcp_sources(
            self.mcp_config_orchestrator,
            await self.read_mcp(),
            plugin_sources=self._context.plugin_mcp.sources(),
        )
        self.update_mcp_projection(
            MCPState(sources=sources, discovery_errors=discovery_errors)
        )
        self._update_connector_projection(await self.read_connectors())
        self._start_connector_resolve()

    def _start_connector_resolve(self) -> None:
        """Kick off deferred connector catalog discovery if not yet resolved.

        The catalog is intentionally absent from the session's initial
        configuration (see build_unified_session_context): the session opens
        instantly with an empty connector set, and the background resolve
        populates it via reconfigure_connectors when the catalog arrives. The
        task is awaited by session/ready/wait so the "Initializing" loader
        covers the network round-trip on a cold cache.
        """
        if self._connector_resolve_task is not None:
            return
        service = self._context.connector_catalog_service
        if service is None:
            return
        if self._context.connector_catalog is not None:
            return  # Already resolved synchronously (shouldn't happen in the
            # deferred path, but guards against future callers).
        self._connector_resolve_task = asyncio.create_task(
            self._resolve_connectors_background(service), name="vibe-connector-resolve"
        )

    async def _resolve_connectors_background(
        self, service: ConnectorCatalogService
    ) -> None:
        """Resolve the connector catalog and reconfigure the session in-place."""
        try:
            catalog = await service.resolve_catalog(self._context.config_orchestrator)
        except ConnectorCatalogError:
            logger.warning("Connector catalog is unavailable during background resolve")
            return
        except Exception:
            logger.exception("Background connector catalog resolve failed")
            return
        if catalog is None or self._closed:
            return
        selection = service.resolve_selection(
            self._context.config_orchestrator, catalog
        )
        try:
            await self.reconfigure_connectors(catalog, selection, force=False)
        except Exception:
            logger.exception("Failed to apply deferred connector catalog")

    def __init__(  # noqa: PLR0913
        self,
        session: UnifiedHarnessSessionBackend,
        cwd: str | None,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        host: UnifiedHarnessSessionBackendHost | None = None,
        telemetry_client: TelemetryClient | None = None,
        session_replaced: SessionReplacementCallback | None = None,
        rewrite_plugins: PluginRewrite | None = None,
        scheduled_loops_enabled: bool = True,
        launch_context: LaunchContext | None = None,
        user_plan: str | None = None,
        completion_attribution: CompletionAttributionSource | None = None,
    ) -> None:
        self._session = session
        self._closed = False
        self._completion_attribution = completion_attribution
        self._host = host
        self._cwd = cwd
        self._context = context
        self._settings = UnifiedSessionSettings()
        self._runtime = derivation.runtime
        self._adapter_config = derivation.adapter_config
        self._model = derivation.adapter_config.model
        self._skills = derivation.adapter_config.skills
        self._bind_completion_attribution(derivation)
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
        self._open_callbacks: dict[tuple[str, str], PublicCallbackEntry] = {}
        self._events_condition = asyncio.Condition()
        self._events_subscribed = False
        self._observed_harness_watermark = 0
        self._child_states: dict[str, PublicSessionState] = {}
        self._telemetry = telemetry_client or TelemetryClient(
            config_getter=lambda: context.config_orchestrator.config,
            harness_backend=ExperimentSurface.UNIFIED,
        )
        # Resolved once per open (parent is in async session state, not on the
        # session handle); the telemetry getter reads the cached value so every
        # event on a fork/subagent session carries its parent.
        self._parent_session_id: str | None = None
        self._telemetry._parent_session_id_getter = lambda: self._parent_session_id
        # One surface for the events Core produces (per-request, tool calls,
        # subagent ops, compaction); owns the terminal-effect dedupe. Lifecycle
        # and client-forwarded events stay on ``self._telemetry``.
        self._session_telemetry = SessionTelemetry(self._telemetry, model=self._model)
        # The context size the last snapshot reported, read as
        # ``nb_context_tokens_before`` when a compaction (which resets the gauge to
        # zero in its own snapshot) is reconciled.
        self._context_tokens_before = 0
        self._telemetry_closed = False
        self._launch_context = launch_context
        self._user_plan = user_plan
        self._narration = NarrationService(
            lambda: NarrationContext(
                config=self._context.config_orchestrator.config,
                launch_context=self._launch_context,
                parent_session_id=self._session.parent_session_id,
                user_plan=self._user_plan,
            )
        )
        self._session_replaced = session_replaced
        # One implementation of the account ladder and the identity projection,
        # reached through host Protocols that ``AgentLoop`` also satisfies. The
        # account host reconciles the experiment manager's attribute snapshot,
        # which is the source telemetry reads for plan/segmentation.
        self._account_controller = AccountController(
            _UnifiedAccountHost(context), context.account_gateway
        )
        self._identity_controller = IdentityController(
            _UnifiedIdentityHost(context), context.identity_gateway
        )
        self._experiments_task: asyncio.Task[None] | None = None
        self._connector_resolve_task: asyncio.Task[None] | None = None
        self._derivation_pending = False
        self._defer_event_flush = False
        self._skip_deferred_flush_once = False
        self._release_deferred_events: Callable[[], None] | None = None
        self._translated_state: PublicSessionState | None = None
        self._pretranslated_events: dict[
            int, tuple[tuple[SessionBackendEvent, ...], PublicSessionState]
        ] = {}
        self._scheduled_loops = UnifiedScheduledLoops(
            self._session_dir() / "scheduled-loops.json",
            persistent=lambda: not bool(getattr(self._session, "ephemeral", True)),
        )
        self._scheduled_loops_enabled = scheduled_loops_enabled
        self._scheduled_loops_task: asyncio.Task[None] | None = None
        self._pending_startup_notices: list[str] = []

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

    def _require_model_owned_root(self, session_id: str) -> None:
        if session_id == self.session_id:
            return
        if self.references_child(session_id):
            raise SessionBackendError(
                ProtocolErrorCode.FORBIDDEN,
                f"A subagent Session is controlled by its parent: {session_id}",
                {"reason": "child_model_owned", "sessionId": session_id},
            )
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
                    ready=self._experiments_settled(),
                )
            case _ if method.startswith("session/"):
                response = await self._dispatch_session_extension(method, raw_params)
            case _ if method.startswith("plugin/"):
                response = await self._dispatch_plugin(method, raw_params)
            case "account/read":
                params = validate_wire(AccountReadParams, raw_params)
                self._require_session(params.session_id)
                response = AccountReadResponse(account=await self._read_account())
            case "identity/read":
                params = validate_wire(IdentityReadParams, raw_params)
                self._require_session(params.session_id)
                response = IdentityReadResponse(
                    identity=await self._identity_controller.read()
                )
            case _ if method.startswith("loops/"):
                response = await self._dispatch_scheduled_loops(method, raw_params)
            case "skills/list":
                skills_params = validate_wire(SkillsListParams, raw_params)
                self._require_session(skills_params.session_id)
                response = SkillsListResponse(skills=self._runtime.skills)
            case "diagnostics/logs/read":
                params = validate_wire(DiagnosticsLogsReadParams, raw_params)
                self._require_session(params.session_id)
                response = DiagnosticsLogsReadResponse(
                    logs=project_debug_logs(
                        LogReader().get_logs(limit=params.limit, offset=params.offset)
                    )
                )
            case _ if method.startswith("config/"):
                response = await self._dispatch_config_read(method, raw_params)
            case "telemetry/record":
                telemetry_params = validate_wire(TelemetryRecordParams, raw_params)
                self._require_session(telemetry_params.session_id)
                self._telemetry.send_telemetry_event(
                    telemetry_params.name,
                    telemetry_params.properties,
                    correlation_id=(
                        self._context.correlation.value
                        if telemetry_params.correlate_last_request
                        else None
                    ),
                )
                response = EmptyResponse()
            case "narration/summarize":
                response = await self._dispatch_narration(raw_params)
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

    async def _dispatch_plugin(
        self, method: str, raw_params: dict[str, Any]
    ) -> PluginInfoResponse | PluginReloadResponse:
        if method == "plugin/info":
            params = validate_wire(PluginInfoParams, raw_params)
            self._require_session(params.session_id)
            return PluginInfoResponse(info=await self._read_plugin_info())
        if method == "plugin/reload":
            params = validate_wire(PluginReloadParams, raw_params)
            self._require_session(params.session_id)
            return await self._reload_plugins()
        raise method_not_found(method)

    async def _dispatch_narration(
        self, raw_params: dict[str, Any]
    ) -> NarrationSummarizeResponse:
        params = validate_wire(NarrationSummarizeParams, raw_params)
        self._require_session(params.session_id)
        summary = await self._narration.summarize(params)
        return NarrationSummarizeResponse(summary=summary)

    async def _dispatch_session_extension(
        self, method: str, raw_params: dict[str, Any]
    ) -> ProtocolModel:
        match method:
            case "session/rewind":
                response = await self._rewind(
                    validate_wire(SessionRewindParams, raw_params)
                )
            case "session/log/read":
                params = validate_wire(SessionLogReadParams, raw_params)
                self._require_session(params.session_id)
                response = SessionLogReadResponse(log=await self._session_log_summary())
            case "session/ready/wait":
                # Block on the background experiment eval so new_session/ready have
                # fired and the plan snapshot is resolved before the client records
                # startup-class events (vibe.startup, etc.) — parity with the legacy
                # AgentLoop, which awaits experiments in wait_until_ready. If that
                # eval was cancelled (rapid start/stop) the fresh-start callback
                # never ran, so report not-ready to keep the client from emitting
                # unpaired startup telemetry, mirroring the legacy skip.
                # Also await the deferred connector catalog resolve so the
                # "Initializing" loader covers the network round-trip.
                ready = not self._closed
                for task in (self._experiments_task, self._connector_resolve_task):
                    if task is not None:
                        with contextlib.suppress(BaseException):
                            await task
                        if task.cancelled():
                            ready = False
                response = SessionReadyWaitResponse(ready=ready, init_duration_ms=0)
            case "session/ready/read":
                response = SessionReadyReadResponse(ready=self._experiments_settled())
            case "session/stop":
                params = validate_wire(SessionStopParams, raw_params)
                self._require_session(params.session_id)
                response = SessionStopResponse()
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
                response = SessionHistoryListResponse(
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
                response = _turns_list_response(
                    _turns_from_history(state.history or [], state.session.id),
                    turns_params,
                )
            case "session/rewind/read":
                params = validate_wire(SessionRewindReadParams, raw_params)
                self._require_session(params.session_id)
                response = SessionRewindReadResponse(has_file_changes=False, paths=[])
            case _:
                raise method_not_found(method)
        return response

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
        pending_notices, self._pending_startup_notices = (
            self._pending_startup_notices,
            [],
        )
        for message in pending_notices:
            self._session.publish_notice(message)
        self._seed_tool_telemetry(subscription.snapshot.state)
        snapshot = self._read_response(subscription.snapshot)
        self._seed_stats(snapshot.state)
        # A resumed session's next compaction reports the size it replaced; keep
        # the __init__ default of 0 when the snapshot carries no measurement.
        seeded_context_tokens = _context_tokens(snapshot.state.session.context_usage)
        if seeded_context_tokens is not None:
            self._context_tokens_before = seeded_context_tokens
        self._translated_state = snapshot.state
        async with self._events_condition:
            self._observed_harness_watermark = subscription.snapshot.watermark
            self._events_condition.notify_all()
        return SessionEventSubscription(
            snapshot=snapshot,
            events=self._translated_events(subscription, snapshot.state),
        )

    async def flush_events(self) -> None:
        if self._skip_deferred_flush_once:
            self._skip_deferred_flush_once = False
            return
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
        agents = self._context.agents
        previous = agents.active_profile.name
        try:
            agents.switch_profile(params.agent_name)
        except ValueError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        try:
            await self._apply_agent_derivation()
        except Exception as exc:
            await self._restore_profile(previous)
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR,
                f"Failed to apply agent '{params.agent_name}': {exc}",
            ) from exc
        return SessionBackendResult(
            response=RuntimeMutationResponse(runtime=self._runtime)
        )

    async def _restore_profile(self, name: str) -> None:
        """Put a failed switch back on the agent the session was already running."""
        try:
            self._context.agents.switch_profile(name)
            await self._apply_agent_derivation()
        except Exception:
            logger.exception(
                "Failed to restore the agent profile after a rejected switch agent=%s",
                name,
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
        session_model_pinned = self._session.active_model is not None
        update_session_override = (
            session_model_pinned and active_model_override_write_requested(params.ops)
        )
        ops = (
            with_session_active_model_write(params.ops)
            if update_session_override
            else params.ops
        )
        durable_aliases = await orchestrator.durable_model_aliases()
        operations = config_write_ops_to_patches(
            orchestrator.config, ops, durable_model_aliases=durable_aliases
        )
        try:
            failures = await orchestrator.apply_patch(
                operations, reason=params.reason, preflight=self._preflight_config
            )
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
        if update_session_override:
            active_model = orchestrator.config.get_active_model().alias
            failures = await set_session_active_model_override(
                orchestrator, active_model, reason="normalize session active model"
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
        await self._context.config_orchestrator.reload(preflight=self._preflight_config)
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
                config, active_model_pinned=active_model_is_pinned(orchestrator)
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
        return ConfigFieldsReadResponse(
            fields=fields, targets=config_write_targets(orchestrator)
        )

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
        if self._defer_event_flush:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "Context compaction is already running"
            )
        active_turn_id = self._session.active_turn_id
        if active_turn_id is None:
            return
        raise SessionBackendError(
            ProtocolErrorCode.CONFLICT,
            f"A turn is already running: {active_turn_id}",
            {"activeTurnId": active_turn_id},
        )

    async def _require_settled(self) -> None:
        """Reject a lifecycle mutation aimed at a session that is still working.

        ``active_turn_id`` names the task driving ``runtime.command``, which is
        only reaped once that coroutine returns — after the Core has published
        the turn's completion and the client has been told the turn is done. A
        client acting on what it was just told would otherwise be refused for a
        turn nobody is running, so the Core's own status decides, and the
        Harness waits out the trailing task itself.
        """
        if self._defer_event_flush:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "Context compaction is already running"
            )
        active_turn_id = self._session.active_turn_id
        if active_turn_id is None:
            return
        snapshot = await self._session.read(
            HarnessSessionReadParams(session_id=self.session_id, history_limit=1)
        )
        status = cast(Any, snapshot.snapshot.state.session.status)
        if getattr(status, "type", None) not in {"running", "blocked"}:
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

        ``mcp``, ``connectors`` and ``stats`` are carried over rather than
        taken from the derivation. They describe what the session is *connected
        to* and what it has spent, which come back from the Harness and are
        patched in as they change; a derivation only projects the layered
        config and leaves them empty. What the config asks for still lands,
        through ``config`` and through the stats' pricing. Converging the two
        is ``config/reload``'s job, and it brackets this call to do it.
        """
        self._derivation_pending = False
        derivation = await asyncio.to_thread(self._context.derive, self._settings)
        await self._session.apply_runtime_configuration(
            derivation.core_config.settings,
            derivation.adapter_config,
            derivation.core_config.capabilities,
        )
        self._adopt_derivation(derivation)

    def _adopt_derivation(self, derivation: UnifiedRuntimeDerivation) -> None:
        """Publish the derivation the session is now running."""
        self._runtime = derivation.runtime.model_copy(
            update={
                "mcp": self._runtime.mcp,
                "connectors": self._runtime.connectors,
                "stats": _repriced_stats(self._runtime.stats, derivation.runtime.stats),
            }
        )
        self._adapter_config = derivation.adapter_config
        self._model = derivation.adapter_config.model
        self._session_telemetry.set_model(self._model)
        self._skills = derivation.adapter_config.skills
        self._bind_completion_attribution(derivation)

    def _bind_completion_attribution(
        self, derivation: UnifiedRuntimeDerivation
    ) -> None:
        """Point this derivation's runtime config back at this session.

        Every derivation brings its own holder, so a re-derivation would
        otherwise leave the new config attributing nothing.
        """
        if self._completion_attribution is not None:
            derivation.completion_attribution.bind(self._completion_attribution)

    async def _apply_agent_derivation(self) -> None:
        """Land an agent switch, mid-turn included.

        A mode switch is the one config mutation whose point can be to change
        the call the session is *already* making -- ``auto-approve`` to stop
        being asked, ``ask`` to stop being auto-approved -- so it cannot take
        ``_require_idle``'s answer of rejecting the switch and rolling the
        profile back. What it needs is the approval policy: ``bypass_approval``,
        ``tool_modes`` and the permission resolver are read per tool action, so
        grafting those three onto the config the session is already running
        lands on the running turn's next tool call.

        Only those three move. The rest of the derivation is paired with state
        the Core holds across the turn -- it reads its settings when the turn
        starts and reconfigures through its own command queue -- so moving one
        half alone would split them: skill payloads are the adapter's half of
        the definitions the Core advertises, and ``active_model`` is what the
        adapter serves the turn's completions with. They go together on the
        next turn, which ``start_turn`` flushes.
        """
        derivation = await asyncio.to_thread(self._context.derive, self._settings)
        if self._session.active_turn_id is None:
            await self._session.apply_runtime_configuration(
                derivation.core_config.settings,
                derivation.adapter_config,
                derivation.core_config.capabilities,
            )
            self._derivation_pending = False
            self._adopt_derivation(derivation)
            return
        approval_only = replace(
            self._adapter_config,
            bypass_approval=derivation.adapter_config.bypass_approval,
            tool_modes=derivation.adapter_config.tool_modes,
            permission_resolver=derivation.adapter_config.permission_resolver,
        )
        self._session.apply_adapter_config(approval_only)
        self._derivation_pending = True
        self._adopt_derivation(replace(derivation, adapter_config=approval_only))

    async def _preflight_config(self, candidate: VibeConfigSchema) -> None:
        preflight = self._context.preflight
        if preflight is None:
            return
        await preflight(candidate, self._settings)

    async def _apply_derivation_when_idle(self) -> None:
        """Push a derivation that originated outside a config mutation.

        Tenant reconciliation can land during a turn, and ``_require_idle``
        exists because the Rust Core reads its settings at turn start: pushing
        between two iterations would swap the provider underneath a running
        turn. Persist always, push only when Core is between turns;
        ``start_turn`` flushes what is pending.
        """
        if self._session.active_turn_id is not None:
            self._derivation_pending = True
            return
        await self._apply_derivation()

    async def initialize_experiments(self, launch_context: LaunchContext) -> None:
        self._launch_context = launch_context
        context = self._context
        try:
            updated, self._user_plan = await session_initialize_experiments(
                config=context.config_orchestrator.config,
                manager=context.experiment_manager,
                session_logger=_NULL_EXPERIMENT_SINK,
                launch_context=launch_context,
                harness=ExperimentSurface.UNIFIED,
                resolve_identity=context.identity_cache.resolve,
                resolve_whoami=context.whoami_cache.resolve,
            )
            if updated:
                await self._apply_experiment_variants()
        except Exception:
            logger.exception("Failed to initialize experiments")

    # Only what ``adapter_config`` carries takes effect in this session: the
    # model variants apply now, while ``system_prompt_id`` rides ``core_config``,
    # which is not pushed live, so it governs from the next session open.
    async def _apply_experiment_variants(self) -> None:
        context = self._context
        try:
            layer = context.config_orchestrator.get_layer(GrowthbookLayer.NAME)
        except KeyError:
            return
        if not isinstance(layer, GrowthbookLayer):
            return
        layer.set_variants(context.experiment_manager.config_variants())
        await context.config_orchestrator.reload()
        await self._apply_derivation_when_idle()

    async def _flush_pending_derivation(self) -> None:
        if self._derivation_pending:
            await self._apply_derivation()

    async def _read_account(self) -> AccountView:
        """Answer ``account/read`` and push whatever reconciliation healed.

        The read itself is never blocked on the idle guard: the CLI renders its
        banner from this during a turn, and a ``CONFLICT`` there would break the
        banner rather than protect anything.
        """
        before = self._context.config_orchestrator.config
        account = await self._account_controller.read()
        after = self._context.config_orchestrator.config
        if after is not before:
            await self._apply_derivation_when_idle()
        return account

    async def start_turn(
        self, params: TurnStartParams
    ) -> SessionBackendResult[TurnStartResponse]:
        self._require_model_owned_root(params.session_id)
        await self._flush_pending_derivation()
        runtime_updated = await self._pin_session_active_model()
        params = await self._prepared_turn_params(params, inject_skill=True)
        was_ephemeral = bool(getattr(self._session, "ephemeral", True))
        result = cast(Any, await _harness_call(self._session.start_turn(params)))
        await self._persist_scheduled_loops_after_promotion(was_ephemeral=was_ephemeral)
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
            runtime_updated=runtime_updated,
        )

    async def enqueue_turn(
        self, params: TurnEnqueueParams
    ) -> SessionBackendResult[TurnEnqueueResponse]:
        runtime_updated = await self._pin_session_active_model()
        try:
            params = await self._prepared_queue_params(params)
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        harness_params = _harness_enqueue_params(params)
        result = cast(
            Any, await _harness_call(self._session.enqueue_turn(harness_params))
        )
        return SessionBackendResult(
            response=TurnEnqueueResponse.model_validate(
                result.response.model_dump(mode="json", by_alias=True)
            ),
            after_response=result.after_response,
            runtime_updated=runtime_updated,
        )

    async def _pin_session_active_model(self) -> bool:
        active_model = self._context.config_orchestrator.config.get_active_model().alias
        failures = await set_session_active_model_override(
            self._context.config_orchestrator,
            active_model,
            reason="pin session active model",
        )
        if failures:
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR,
                f"Failed to pin session active model: {failures[0]}",
            )
        changed = await _harness_call(self._session.persist_active_model(active_model))
        if not changed:
            return False
        derivation = await asyncio.to_thread(self._context.derive, self._settings)
        self._adopt_derivation(derivation)
        return True

    async def read_turn_queue(
        self, params: TurnQueueReadParams
    ) -> SessionBackendResult[TurnQueueReadResponse]:
        result = cast(
            Any,
            await _harness_call(
                self._session.read_turn_queue(
                    HarnessTurnQueueReadParams.model_validate(
                        params.model_dump(mode="json", by_alias=True)
                    )
                )
            ),
        )
        return SessionBackendResult(
            response=TurnQueueReadResponse.model_validate(
                result.response.model_dump(mode="json", by_alias=True)
            ),
            after_response=result.after_response,
        )

    async def remove_queued_turn(
        self, params: TurnQueueRemoveParams
    ) -> SessionBackendResult[TurnQueueRemoveResponse]:
        result = cast(
            Any,
            await _harness_call(
                self._session.remove_queued_turn(
                    HarnessTurnQueueRemoveParams.model_validate(
                        params.model_dump(mode="json", by_alias=True)
                    )
                )
            ),
        )
        return SessionBackendResult(
            response=TurnQueueRemoveResponse.model_validate(
                result.response.model_dump(mode="json", by_alias=True)
            ),
            after_response=result.after_response,
        )

    async def replace_queued_turn(
        self, params: TurnQueueReplaceParams
    ) -> SessionBackendResult[TurnQueueReplaceResponse]:
        try:
            params = await self._prepared_queue_params(params)
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        result = cast(
            Any,
            await _harness_call(
                self._session.replace_queued_turn(_harness_replace_params(params))
            ),
        )
        return SessionBackendResult(
            response=TurnQueueReplaceResponse.model_validate(
                result.response.model_dump(mode="json", by_alias=True)
            ),
            after_response=result.after_response,
        )

    async def resume_turn_queue(
        self, params: TurnQueueResumeParams
    ) -> SessionBackendResult[TurnQueueResumeResponse]:
        result = cast(
            Any,
            await _harness_call(
                self._session.resume_turn_queue(
                    HarnessTurnQueueResumeParams.model_validate(
                        params.model_dump(mode="json", by_alias=True)
                    )
                )
            ),
        )
        return SessionBackendResult(
            response=TurnQueueResumeResponse.model_validate(
                result.response.model_dump(mode="json", by_alias=True)
            ),
            after_response=result.after_response,
        )

    async def steer_turn(
        self, params: TurnSteerParams
    ) -> SessionBackendResult[TurnSteerResponse]:
        self._require_model_owned_root(params.session_id)
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
        self._require_model_owned_root(params.session_id)
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
        self._require_model_owned_root(params.session_id)
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
        was_ephemeral = bool(getattr(self._session, "ephemeral", True))
        result = cast(Any, await _harness_call(self._session.inject_context(params)))
        await self._persist_scheduled_loops_after_promotion(was_ephemeral=was_ephemeral)
        response = result.response
        return SessionBackendResult(
            response=ContextInjectResponse(
                entries=[_project_history_entry(entry) for entry in response["entries"]]
            ),
            after_response=result.after_response,
        )

    def open_callbacks(self) -> list[PublicCallbackEntry]:
        if self._host is not None:
            return [
                callback
                for raw in self._host.open_callbacks(self.session_id)
                if isinstance(
                    callback := validate_history_entry(raw), PublicCallbackEntry
                )
            ]
        return list(self._open_callbacks.values())

    def references_child(self, session_id: str) -> bool:
        return self._host is not None and self._host.references_child(
            self.session_id, session_id
        )

    async def reject_callback_delivery(
        self, session_id: str, callback_id: str, error: CallbackResultError
    ) -> None:
        if session_id != self.session_id and not self.references_child(session_id):
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )
        params = CallbackResultParams(
            session_id=session_id,
            result=CallbackResult(callback_id=callback_id, error=error),
        )
        if self._host is not None:
            await _harness_call(self._host.respond_to_callback(self.session_id, params))
            return
        await _harness_call(self._session.respond_to_callback(params))

    async def respond_to_callback(
        self, params: object
    ) -> SessionBackendResult[CallbackResultResponse]:
        await self._record_approval_grant(params)
        if self._host is not None:
            operation = self._host.respond_to_callback(self.session_id, params)
        else:
            operation = self._session.respond_to_callback(params)
        result = cast(Any, await _harness_call(operation))
        response = result.response
        return SessionBackendResult(
            response=CallbackResultResponse(
                accepted=True, last_event_id=response["last_event_id"]
            ),
            after_response=result.after_response,
        )

    async def _record_approval_grant(self, params: object) -> None:
        """Remember an approval that reaches past the call, before it is acted on."""
        if not isinstance(params, CallbackResultParams):
            return
        result = params.result
        if result.error is not None:
            return
        callback = self._open_callbacks.get((params.session_id, result.callback_id))
        if callback is None or not isinstance(callback.detail, ApprovalCallbackDetail):
            return
        try:
            output = validate_wire(ApprovalCallbackOutput, result.output)
        except ValidationError:
            # A malformed or non-approval answer. Leave it to the Runtime to
            # reject; a grant recorded off it would outlive the rejection.
            return
        if output.decision.type not in {
            ApprovalDecisionType.APPROVE_FOR_SESSION,
            ApprovalDecisionType.APPROVE_PERMANENTLY,
        }:
            return
        await self._context.permissions.grant(
            callback.detail.effect.tool_name,
            callback.detail.required_permissions,
            permanent=output.decision.type is ApprovalDecisionType.APPROVE_PERMANENTLY,
        )

    async def compact(
        self, params: SessionCompactParams
    ) -> SessionBackendResult[SessionCompactResponse]:
        self._require_session(params.session_id)
        previous = self._translated_state
        if previous is None:
            baseline = await self._session.read(
                HarnessSessionReadParams(session_id=self.session_id, history_limit=500)
            )
            previous = self._read_response(baseline.snapshot).state
        self._defer_event_flush = True
        try:
            result = cast(Any, await _harness_call(self._session.compact(params)))
        except BaseException:
            self._defer_event_flush = False
            raise
        response = result.response
        for raw_event in result.buffered_events:
            if raw_event.get("type") != "session_state_updated":
                _reject(
                    f"a buffered compaction event {raw_event.get('type', raw_event)!r}"
                )
            raw_state = raw_event.get("state")
            if not isinstance(raw_state, dict):
                _reject("a buffered compaction update without state")
            watermark = raw_event.get("eventId")
            if not isinstance(watermark, int):
                _reject("a buffered compaction update without an event id")
            harness_event_state = HarnessPublicSessionState.model_validate(raw_state)
            current = _read_response(
                HarnessSessionSnapshot(
                    state=harness_event_state,
                    history_limit=len(harness_event_state.history.entries),
                    watermark=watermark,
                ),
                self._cwd,
                event_id=self._event_id,
            ).state
            translated, previous = self._translate_snapshot_update(previous, current)
            self._pretranslated_events[watermark] = (tuple(translated), previous)
        self._translated_state = previous
        harness_state = HarnessPublicSessionState.model_validate(response["state"])
        state = self._read_response(
            HarnessSessionSnapshot(
                state=harness_state,
                history_limit=len(harness_state.history.entries),
                watermark=response["last_event_id"],
            )
        ).state

        def finish_response(callback: Callable[[], None] | None) -> None:
            if self._release_deferred_events is None:
                return
            self._release_deferred_events = None
            self._skip_deferred_flush_once = False
            self._defer_event_flush = False
            if callback is not None:
                callback()

        def after_response() -> None:
            finish_response(result.after_response)

        def on_response_abandoned() -> None:
            finish_response(result.on_response_abandoned)

        self._release_deferred_events = after_response
        self._skip_deferred_flush_once = True
        return SessionBackendResult(
            response=SessionCompactResponse(
                summary=response["summary"],
                state=state,
                session_log=await self._session_log_summary(),
            ),
            after_response=after_response,
            on_response_abandoned=on_response_abandoned,
        )

    async def _rewind(self, params: SessionRewindParams) -> SessionRewindResponse:
        self._require_session(params.session_id)
        await self._require_rewindable(params)
        if not params.inplace:
            _reject("non-in-place session/rewind")
        if self._host is None:
            _reject("session/rewind")
        # Read the message being rewound to before it is dropped: the client puts
        # it back in the composer so the user can edit and re-send it.
        message = await self._rewound_message(params.entry_id)
        await _harness_call(self._host.rewind(params.session_id, params.entry_id))
        # The truncation lands in the Core before its events reach the Client, so
        # the response would otherwise carry the rewind checkpoint under an event
        # id that still predates it and the Client would apply the entry twice.
        await self.flush_events()
        snapshot = await self.read(
            SessionReadParams(
                session_id=self.session_id,
                history=PageRequest(limit=500),
                turns=PageRequest(limit=500),
            )
        )
        return SessionRewindResponse(
            message=message,
            restore_errors=[],
            restored_paths=[],
            state=snapshot.state,
            session_log=await self._session_log_summary(),
        )

    async def _require_rewindable(self, params: SessionRewindParams) -> None:
        """Reject a rewind this backend cannot serve, before anything is dropped."""
        if params.restore_files:
            _reject("session/rewind file restoration")
        await self._require_settled()

    async def _rewound_message(self, entry_id: str) -> str:
        """The text of the user message a rewind is anchored on.

        Also the entry-id guard: the Harness raises on an anchor missing from its
        own projection, but the failure has to name the entry rather than read as
        a broken session, and it has to happen before the truncation runs.
        """
        state = await self._read_page_state(self.session_id)
        entry = next(
            (entry for entry in state.history or [] if entry.id == entry_id), None
        )
        if not isinstance(entry, PublicMessageEntry) or entry.role != "user":
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND,
                f"Rewindable history entry not found: {entry_id}",
            )
        return entry.text

    def _emit_fresh_start_telemetry(self) -> None:
        """Emit the fresh-start lifecycle pair once experiments have resolved.

        Called from ``start`` after the background experiment eval, so
        new_session/ready carry the resolved user_plan and experiment_attributes.
        Skipped if the session already closed: the host shutdown path emits
        session_closed without cancelling the eval task, so this callback can
        still run afterward — it must not emit new_session/ready after close.
        """
        if self._telemetry_closed:
            return
        self._emit_new_session_telemetry()
        self._emit_ready_telemetry()

    def _emit_new_session_telemetry(self) -> None:
        """Emit ``vibe.new_session`` for a freshly started harness session.

        The legacy AgentLoop emits this during init on the fresh-start path only
        (never on resume); the harness host mirrors that by calling this from
        ``start`` and history-clear, not ``resume``/``continue``.
        """
        config = self._context.config_orchestrator.config
        self._telemetry.send_new_session(
            has_agents_md=has_agents_md_file(self._cwd_path()),
            nb_skills=len(self._runtime.skills),
            nb_mcp_servers=len(config.mcp_servers),
            nb_models=len(config.models),
        )

    def _emit_ready_telemetry(self) -> None:
        """Emit ``vibe.ready`` once the session is usable (fresh start only).

        The harness reports readiness synchronously, so there is no measured
        init duration to attach; stamp 0 for schema parity with the legacy path.
        """
        self._telemetry.send_ready(init_duration_ms=0)

    def _emit_session_closed_telemetry(self) -> None:
        """Emit ``vibe.session_closed`` once for this session.

        Idempotent: session replacement routes through ``shutdown`` while process
        exit routes through the host adapter's shutdown, and only the first wins.
        """
        if self._telemetry_closed:
            return
        self._telemetry_closed = True
        self._telemetry.send_session_closed()

    async def _cache_parent_session_id(self) -> None:
        """Cache the parent session id for telemetry segmentation.

        The harness session handle does not expose it — it lives in async state —
        so resolve it once per open, before lifecycle events fire. Best-effort: a
        failed read leaves it None, which is correct for a root session.
        """
        with contextlib.suppress(Exception):
            result = await self._session.read(
                _harness_read_params(
                    SessionReadParams(
                        session_id=self.session_id, history=PageRequest(limit=1)
                    )
                )
            )
            self._parent_session_id = result.snapshot.state.session.parent_session_id

    def _experiments_settled(self) -> bool:
        """Whether background init (experiments + connector resolve) has settled.

        Mirrors the legacy ``AgentLoop.is_initialized``: ``False`` while the
        deferred work is in flight, ``True`` once it completes (or when there is
        nothing to wait on). Readiness endpoints report this so clients can show
        an "Initializing" loader until ``wait_until_ready`` resolves.
        """
        for task in (self._experiments_task, self._connector_resolve_task):
            if task is not None and not task.done():
                return False
        return True

    def _cancel_experiments_task(self) -> asyncio.Task[None] | None:
        """Cancel the in-flight experiment eval, if any, and return it to await.

        Returns the cancelled task so callers that want it settled can await it;
        the host shutdown sweep cancels without awaiting.
        """
        task = self._experiments_task
        self._experiments_task = None
        if task is not None and not task.done():
            task.cancel()
            return task
        return None

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Flush any completion whose snapshot never drove a final ``_updated_state``
        # before teardown closes the telemetry client.
        self._forward_request_sent()
        # Before the slower parts of the close, so a sweep running concurrently
        # cannot read this worktree as still occupied by a session that is on
        # its way out.
        if self._cwd is not None:
            SessionWorktrees.release(Path(self._cwd), self.session_id)
        self._release_deferred_events = None
        self._skip_deferred_flush_once = False
        self._defer_event_flush = False
        scheduler = self._scheduled_loops_task
        self._scheduled_loops_task = None
        if scheduler is not None:
            scheduler.cancel()
            try:
                await scheduler
            except asyncio.CancelledError:
                pass
        # Cancel the in-flight eval before announcing the close, so its ``after``
        # callback can never emit new_session/ready after session_closed. Awaited
        # here so the task is settled before teardown continues.
        task = self._cancel_experiments_task()
        if task is not None:
            with contextlib.suppress(BaseException):
                await task
        # Cancel the deferred connector catalog resolve so it can never
        # reconfigure a session that is being torn down.
        connector_task = self._connector_resolve_task
        self._connector_resolve_task = None
        if connector_task is not None and not connector_task.done():
            connector_task.cancel()
            with contextlib.suppress(BaseException):
                await connector_task
        self._emit_session_closed_telemetry()
        with contextlib.suppress(Exception):
            await self._context.experiment_manager.aclose()
        try:
            try:
                await self._scheduled_loops.persist()
            except ScheduledLoopStoreError as exc:
                logger.warning("Failed to persist scheduled loops", exc_info=exc)
            await self._session.shutdown()
        finally:
            await self._telemetry.aclose()

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
        persisted = not self._session.ephemeral
        return SessionLogSummary(
            enabled=True,
            session_id=self.session_id,
            persisted=persisted,
            path=str(self._session_dir()) if persisted else None,
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
        resolved = self._resolved_invoked_skill(_text_from_blocks(blocks))
        if resolved is None:
            return None
        name, body = resolved
        if await self._skill_already_loaded(name):
            return TextContentBlock(text=already_loaded_message(name))
        return TextContentBlock(text=body)

    def _resolved_invoked_skill(self, text: str) -> tuple[str, str] | None:
        text = text.strip()
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
        return name, body

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

    async def _prepared_queue_params[
        ParamsT: TurnEnqueueParams | TurnQueueReplaceParams
    ](self, params: ParamsT) -> ParamsT:
        user_index = next(
            (
                index
                for index, entry in enumerate(params.entries)
                if isinstance(entry, TurnUserInputEntry)
            ),
            None,
        )
        skill_body = None
        if user_index is not None:
            user_entry = params.entries[user_index]
            # Queue input must stay stable while earlier turns change history,
            # so store the full body rather than the immediate-turn dedup hint.
            resolved = self._resolved_invoked_skill(
                _text_from_session_blocks(user_entry.content)
            )
            if resolved is not None:
                _, skill_body = resolved

        params = await self._with_mentioned_file_entries(params)
        if user_index is None or skill_body is None:
            return params

        entries = list(params.entries)
        user_entry = entries[user_index]
        if not isinstance(user_entry, TurnUserInputEntry):
            raise RuntimeError("Queued user entry changed while preparing input")
        entries[user_index] = user_entry.model_copy(
            update={
                "content": [
                    *user_entry.content,
                    SessionTextContentBlock(text=skill_body),
                ]
            }
        )
        return params.model_copy(update={"entries": entries})

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

    async def _with_mentioned_file_entries[
        ParamsT: TurnEnqueueParams | TurnQueueReplaceParams
    ](self, params: ParamsT) -> ParamsT:
        user_index = next(
            (
                index
                for index, entry in enumerate(params.entries)
                if isinstance(entry, TurnUserInputEntry)
            ),
            None,
        )
        if user_index is None:
            return params
        user_entry = params.entries[user_index]
        text = _text_from_session_blocks(user_entry.content)
        blocks = await mentioned_file_content_blocks_async(
            text, base_dir=self._cwd_path()
        )
        if not blocks:
            return params
        entries = list(params.entries)
        entries[user_index] = user_entry.model_copy(
            update={
                "content": [
                    *user_entry.content,
                    *session_content_blocks_from_vibe(blocks),
                ]
            }
        )
        return params.model_copy(update={"entries": entries})

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
                event_type = event.get("type")
                if event_type == "child_session_registered":
                    watermark = self._register_child_event(event)
                    await self._mark_harness_event_observed(watermark)
                    continue
                if event_type == "child_session_event":
                    translated, watermark = await self._translate_child_event(
                        event, subscription.snapshot.history_limit
                    )
                    for child_event in translated:
                        yield child_event
                    await self._mark_harness_event_observed(watermark)
                    continue
                signal = await self._signal_event(
                    cast(dict[str, object], event), session_id=self.session_id
                )
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
                watermark = _harness_event_watermark(event)
                queue_update = self._translated_turn_queue_event(event)
                if queue_update is not None:
                    queue_event, queue = queue_update
                    yield queue_event
                    previous = previous.model_copy(
                        update={"event_id": self._event_id, "turn_queue": queue},
                        deep=True,
                    )
                    self._translated_state = previous
                    await self._mark_harness_event_observed(watermark)
                    continue
                translated, previous, watermark = self._root_state_update_events(
                    event, previous, subscription.snapshot.history_limit
                )
                for app_event in translated:
                    yield app_event
                self._translated_state = previous
                await self._mark_harness_event_observed(watermark)
        finally:
            async with self._events_condition:
                self._events_subscribed = False
                self._events_condition.notify_all()

    def _root_state_update_events(
        self,
        event: Mapping[str, object],
        previous: PublicSessionState,
        history_limit: int,
    ) -> tuple[list[SessionBackendEvent], PublicSessionState, int]:
        watermark = _required_event_id(event, "session")
        pretranslated = self._pretranslated_events.pop(watermark, None)
        if pretranslated is not None:
            translated, current = pretranslated
            return list(translated), current, watermark
        return self._state_update_events(event, previous, history_limit)

    def _translate_snapshot_update(
        self, previous: PublicSessionState, current: PublicSessionState
    ) -> tuple[list[SessionBackendEvent], PublicSessionState]:
        app_events = [
            app_event
            for app_event in reconcile_snapshot(previous, current)
            if not isinstance(app_event, SessionSnapshot)
        ]
        stats = self._advance_stats(previous, current, _approximate_steps(app_events))
        updated = stats != self._runtime.stats
        self._runtime = self._runtime.model_copy(update={"stats": stats})
        return self._interleave_stats(app_events, current, stats, updated=updated)

    def _register_child_event(self, event: Mapping[str, object]) -> int:
        child_session_id = _required_event_str(event, "sessionId")
        raw_snapshot = event.get("snapshot")
        if not isinstance(raw_snapshot, dict):
            _reject("a child registration without a snapshot")
        snapshot = HarnessSessionSnapshot.model_validate(raw_snapshot)
        if snapshot.state.session.id != child_session_id:
            _reject("a child registration with mismatched identity")
        self._child_states[child_session_id] = _read_response(
            snapshot, self._cwd, event_id=self._event_id
        ).state
        return _required_event_id(event, "child registration")

    async def _translate_child_event(
        self, event: Mapping[str, object], history_limit: int
    ) -> tuple[list[SessionBackendEvent], int]:
        child_session_id = _required_event_str(event, "sessionId")
        raw_child_event = event.get("event")
        if not isinstance(raw_child_event, dict):
            _reject("a child event wrapper without an event")
        embedded_session_id = raw_child_event.get("sessionId")
        if (
            isinstance(embedded_session_id, str)
            and embedded_session_id != child_session_id
        ):
            _reject("a child event wrapper with mismatched identity")
        authorization = await self._authorization_event(
            raw_child_event, session_id=child_session_id
        )
        if authorization is not None:
            translated = [authorization[0]]
        elif callback_events := self._callback_events(raw_child_event):
            translated = callback_events
        else:
            previous = self._child_states.get(child_session_id)
            if previous is None:
                _reject("a child event received before child registration")
            translated, current = self._child_state_update_events(
                raw_child_event, previous, history_limit
            )
            self._child_states[child_session_id] = current
        return translated, _required_event_id(event, "child wrapper")

    def _state_update_events(
        self,
        event: Mapping[str, object],
        previous: PublicSessionState,
        history_limit: int,
    ) -> tuple[list[SessionBackendEvent], PublicSessionState, int]:
        current, watermark = self._updated_state(event, history_limit)
        translated, current = self._translate_snapshot_update(previous, current)
        return translated, current, watermark

    def _child_state_update_events(
        self,
        event: Mapping[str, object],
        previous: PublicSessionState,
        history_limit: int,
    ) -> tuple[list[SessionBackendEvent], PublicSessionState]:
        """Translate a subagent's update, reporting its spend as its own.

        A child never advances ``self._runtime.stats``: that snapshot backs the
        root's ``/status``, whose figures the root's own updates already carry,
        so folding a child's cumulative usage in would overwrite them.
        """
        current, _watermark = self._updated_state(event, history_limit)
        app_events = [
            app_event
            for app_event in reconcile_snapshot(previous, current)
            if not isinstance(app_event, SessionSnapshot)
        ]
        return self._interleave_stats(
            app_events,
            current,
            _snapshot_stats(current),
            updated=previous.session.token_usage != current.session.token_usage,
        )

    def _updated_state(
        self, event: Mapping[str, object], history_limit: int
    ) -> tuple[PublicSessionState, int]:
        if event.get("type") != "session_state_updated":
            _reject(f"the Harness session event {event.get('type', event)!r}")
        raw_state = event.get("state")
        if not isinstance(raw_state, dict):
            _reject("a Harness session update without state")
        watermark = _required_event_id(event, "session")
        state = HarnessPublicSessionState.model_validate(raw_state)
        self._record_tool_telemetry(state)
        self._record_compaction_telemetry(state)
        self._forward_request_sent()
        state = _limit_harness_history(state, history_limit)
        current = _read_response(
            HarnessSessionSnapshot(
                state=state, history_limit=history_limit, watermark=watermark
            ),
            self._cwd,
            event_id=self._event_id,
        ).state
        # Track the post-snapshot context size so the next compaction can report
        # the size it replaced (its own snapshot will read this, pre-reset value).
        # A missing measurement means "keep the last reading" (``_context_tokens``
        # returns None); a real compaction resets to 0, which is a value, not None.
        context_tokens = _context_tokens(current.session.context_usage)
        if context_tokens is not None:
            self._context_tokens_before = context_tokens
        return current, watermark

    def _interleave_stats(
        self,
        app_events: Sequence[AppServerEvent],
        current: PublicSessionState,
        stats: AgentStatsSnapshot,
        *,
        updated: bool,
    ) -> tuple[list[SessionBackendEvent], PublicSessionState]:
        translated: list[SessionBackendEvent] = []
        for app_event in app_events:
            if isinstance(app_event, TurnCompleted) and updated:
                translated.append(self._stats_event(current.session.id, stats))
                updated = False
            self._event_id += 1
            translated.append(_event_envelope(app_event, self._event_id))
        if updated:
            translated.append(self._stats_event(current.session.id, stats))
        return translated, current.model_copy(
            update={"event_id": self._event_id}, deep=True
        )

    def _seed_tool_telemetry(self, state: HarnessPublicSessionState) -> None:
        """Mark a resumed session's terminal tool effects as already reported.

        Both the subagent and ordinary-tool events dedupe on the shared effect-id
        set, so seeding must cover both or a resume re-emits every past tool call.
        """
        for raw_entry in state.history.entries:
            subagent = _terminal_subagent_effect(raw_entry)
            if subagent is not None:
                self._session_telemetry.mark_recorded(subagent[0])
                continue
            tool = _terminal_tool_effect(raw_entry)
            if tool is not None:
                self._session_telemetry.mark_recorded(tool.effect_id)
                continue
            compaction = _terminal_compaction_effect(raw_entry)
            if compaction is not None:
                self._session_telemetry.mark_recorded(compaction.checkpoint_id)

    def _record_tool_telemetry(self, state: HarnessPublicSessionState) -> None:
        """Emit ``tool_call_finished`` once for each newly terminal tool effect.

        Subagent effects keep their content-free variant; every other tool maps
        to the ordinary event with legacy file metrics. Both share the effect-id
        dedupe so a snapshot reconciled twice never double-counts.
        """
        agent_profile_name = self._active_agent_profile_name()
        for raw_entry in state.history.entries:
            subagent = _terminal_subagent_effect(raw_entry)
            if subagent is not None:
                effect_id, operation, outcome, profile_source = subagent
                if self._session_telemetry.claim(effect_id):
                    self._session_telemetry.record_subagent_tool_call_finished(
                        operation=operation,
                        outcome=outcome,
                        profile_source=profile_source,
                    )
                continue
            tool = _terminal_tool_effect(raw_entry)
            if tool is None or not self._session_telemetry.claim(tool.effect_id):
                continue
            self._session_telemetry.record_tool_call_finished(
                tool_name=tool.tool_name,
                status=tool.status,
                agent_profile_name=agent_profile_name,
                nb_files_created=tool.nb_files_created,
                nb_files_modified=tool.nb_files_modified,
                file_extension=tool.file_extension,
            )

    def _active_agent_profile_name(self) -> str | None:
        agents = getattr(self._context, "agents", None)
        profile = getattr(agents, "active_profile", None)
        return getattr(profile, "name", None)

    def _auto_compact_threshold(self) -> int:
        try:
            return self._context.config_orchestrator.config.get_active_model().auto_compact_threshold
        except Exception:
            return 0

    def _record_compaction_telemetry(self, state: HarnessPublicSessionState) -> None:
        """Emit compaction telemetry once per newly terminal compaction checkpoint.

        ``auto_compact_triggered`` mirrors the legacy auto/reactive path (so it is
        gated on the automatic trigger) and reads the pre-compaction context size
        from the gauge, since the compaction's own snapshot has already reset it.
        ``compaction_failed`` follows only when the reason maps to a legacy bucket.
        """
        threshold: int | None = None
        for raw_entry in state.history.entries:
            compaction = _terminal_compaction_effect(raw_entry)
            if compaction is None or not self._session_telemetry.claim(
                compaction.checkpoint_id
            ):
                continue
            if compaction.trigger == "automatic":
                if threshold is None:
                    threshold = self._auto_compact_threshold()
                self._session_telemetry.record_auto_compact_triggered(
                    nb_context_tokens_before=self._context_tokens_before,
                    auto_compact_threshold=threshold,
                    status="success" if compaction.succeeded else "failure",
                )
            if not compaction.succeeded and compaction.reason is not None:
                self._session_telemetry.record_compaction_failed(
                    reason=compaction.reason
                )

    def _forward_request_sent(self) -> None:
        """Drain the runtime's buffered request-sent telemetry to the client.

        Runs on the app loop (drained from ``_updated_state`` and session
        shutdown) so ``send_request_sent`` schedules on the loop that owns the
        telemetry client, mirroring the subagent path. The runtime reports the
        per-call model, so it is preferred over the session's active alias.
        """
        for payload in self._context.request_sent.drain():
            self._session_telemetry.record_request_sent(
                model=payload.model,
                nb_context_chars=payload.nb_context_chars,
                nb_context_messages=payload.nb_context_messages,
                nb_prompt_chars=payload.nb_prompt_chars,
                call_type=request_call_type(payload.purpose, payload.iteration),
            )

    def _seed_stats(self, state: PublicSessionState) -> None:
        """Adopt a resumed session's spend and the context its last call left.

        Seeding only the billed totals left the gauge reading zero until the
        resumed session made a call of its own, which understates a context the
        Harness has already measured.
        """
        usage = state.session.token_usage
        context_tokens = _context_tokens(state.session.context_usage)
        if usage is None and context_tokens is None:
            return
        update: dict[str, object] = {}
        if usage is not None:
            update["session_prompt_tokens"] = usage.input_tokens
            update["session_completion_tokens"] = usage.output_tokens
        if context_tokens is not None:
            update["context_tokens"] = context_tokens
        self._runtime = self._runtime.model_copy(
            update={"stats": self._runtime.stats.model_copy(update=update)}
        )

    def _advance_stats(
        self, previous: PublicSessionState, current: PublicSessionState, steps: int
    ) -> AgentStatsSnapshot:
        stats = self._runtime.stats
        update: dict[str, object] = {"steps": stats.steps + steps}
        # The gauge follows the Harness's own measurement, which moves
        # independently of the bill: a compaction the provider priced at nothing
        # still emptied the context it replaced. Only that per-call figure
        # measures the live context -- the billed delta sums every call the
        # snapshot covers, which is what inflated the gauge -- so an unreported
        # context holds the last reading rather than guessing from spend.
        context_tokens = _context_tokens(current.session.context_usage)
        if context_tokens is not None:
            update["context_tokens"] = context_tokens
        usage = current.session.token_usage
        if usage is None:
            return stats.model_copy(update=update)
        before = previous.session.token_usage
        added_prompt = max(
            0, usage.input_tokens - (before.input_tokens if before else 0)
        )
        added_completion = max(
            0, usage.output_tokens - (before.output_tokens if before else 0)
        )
        # Cached tokens have no source in the Harness protocol and stay 0.
        update["session_prompt_tokens"] = usage.input_tokens
        update["session_completion_tokens"] = usage.output_tokens
        # A snapshot that spent nothing is not a turn: hold the last call's
        # figures rather than blanking the last-turn line.
        if added_prompt or added_completion:
            update["last_turn_prompt_tokens"] = added_prompt
            update["last_turn_completion_tokens"] = added_completion
        return stats.model_copy(update=update)

    def _stats_event(
        self, session_id: str, stats: AgentStatsSnapshot
    ) -> SessionBackendEvent:
        self._event_id += 1
        return _event_envelope(
            StatsUpdated(
                StatsUpdatedParams(
                    event_id=self._event_id,
                    session_id=session_id,
                    emitted_at=int(time.time() * 1000),
                    stats=stats,
                    context_window=self._runtime.context_window,
                )
            ),
            self._event_id,
        )

    async def _signal_event(
        self, event: Mapping[str, object], *, session_id: str
    ) -> tuple[SessionBackendEvent, int] | None:
        """Translate the events that carry a signal rather than a state change.

        Each maps to a notification of its own and none touch the session
        snapshot, so none reach the reconciliation below, which has nothing to
        diff for them. The event id rides back with the translation because a
        signal still spends one, and ``flush_events`` waits on the id the
        session reports, so skipping one hangs the request that published it.
        """
        authorization = await self._authorization_event(event, session_id=session_id)
        if authorization is not None:
            return authorization
        if event.get("type") == "notice":
            return self._notice_event(event), _required_event_id(event, "notice")
        return None

    async def _connector_authorization_event(
        self, event: Mapping[str, object], *, session_id: str
    ) -> SessionBackendEvent:
        self._update_connector_projection(await self.read_connectors())
        params = ConnectorAuthRequiredParams(
            session_id=session_id,
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
            session_id=session_id,
        )

    async def _authorization_event(
        self, event: Mapping[str, object], *, session_id: str
    ) -> tuple[SessionBackendEvent, int] | None:
        event_type = event.get("type")
        if event_type == "connector_authorization_required":
            watermark = _required_event_id(event, "connector")
            return (
                await self._connector_authorization_event(event, session_id=session_id),
                watermark,
            )
        if event_type == "mcp_authorization_required":
            watermark = _required_event_id(event, "MCP")
            return self._mcp_authorization_event(
                event, session_id=session_id
            ), watermark
        return None

    def _mcp_authorization_event(
        self, event: Mapping[str, object], *, session_id: str
    ) -> SessionBackendEvent:
        params = MCPAuthRequiredParams(
            session_id=session_id,
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
            session_id=session_id,
        )

    def _notice_event(self, event: Mapping[str, object]) -> SessionBackendEvent:
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

    def _translated_turn_queue_event(
        self, event: dict[str, Any]
    ) -> tuple[SessionBackendEvent, PublicTurnQueue] | None:
        raw_payload = event.get("payload")
        if not isinstance(raw_payload, dict) or raw_payload.get("type") != (
            "turn_queue_updated"
        ):
            return None
        parsed = HarnessEvent.model_validate(event)
        if not isinstance(parsed.payload, HarnessTurnQueueUpdatedEvent):
            _reject("an invalid Harness turn queue update")
        queue = _public_turn_queue(parsed.payload.queue)
        self._event_id += 1
        return (
            _turn_queue_event_envelope(queue, self._event_id, self.session_id),
            queue,
        )

    def _callback_events(
        self, event: Mapping[str, object]
    ) -> list[SessionBackendEvent] | None:
        event_type = event.get("type")
        if event_type not in {"callback_requested", "callback_resolved"}:
            return None
        raw_callback = event.get("callback")
        if not isinstance(raw_callback, dict):
            _reject("a Harness callback event without callback data")
        callback = _project_history_entry(raw_callback)
        if not isinstance(callback, PublicCallbackEntry):
            _reject("a Harness callback event with a non-callback entry")
        callback_key = (callback.session_id, callback.id)
        if event_type == "callback_requested":
            self._open_callbacks[callback_key] = callback
            self._event_id += 1
            return [
                _event_envelope(HistoryEntryAdded(callback), self._event_id),
                SessionBackendEvent(event=CallbackRequested(callback)),
            ]
        previous_callback = self._open_callbacks.pop(callback_key, callback)
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


_SUBAGENT_OPERATIONS: dict[str, SubagentOperation] = {
    "subagent.list": "list",
    "subagent.spawn": "spawn",
    "subagent.wait": "wait",
    "subagent.send_message": "send_message",
    "subagent.interrupt": "interrupt",
    "subagent.stop": "close",
}
_TERMINAL_EFFECT_STATES = frozenset({"completed", "failed", "cancelled", "skipped"})


def _terminal_subagent_effect(
    entry: object,
) -> (
    tuple[str, SubagentOperation, SubagentOutcome, SubagentProfileSource | None] | None
):
    if not isinstance(entry, Mapping) or entry.get("type") != "effect":
        return None
    effect_id = entry.get("id")
    detail = entry.get("detail")
    state = entry.get("state")
    if (
        not isinstance(effect_id, str)
        or not isinstance(detail, Mapping)
        or not isinstance(state, Mapping)
        or state.get("status") not in _TERMINAL_EFFECT_STATES
    ):
        return None
    tool_name = detail.get("toolName")
    operation = (
        _SUBAGENT_OPERATIONS.get(tool_name) if isinstance(tool_name, str) else None
    )
    if operation is None:
        return None
    outcome = _subagent_effect_outcome(operation, state)
    profile_source: SubagentProfileSource | None = None
    if operation == "spawn":
        raw_input = detail.get("input")
        agent_type = raw_input.get("agent") if isinstance(raw_input, Mapping) else None
        profile_source = "generic" if agent_type == "generic" else "vibe_profile"
    return effect_id, operation, outcome, profile_source


def _subagent_effect_outcome(
    operation: SubagentOperation, state: Mapping[str, object]
) -> SubagentOutcome:
    if state.get("status") != "completed":
        error = state.get("error")
        error_code = error.get("code") if isinstance(error, Mapping) else None
        if operation == "wait" and error_code == "subagent_wait_timeout":
            return "timeout"
        return "failure"
    output = state.get("output")
    structured = None
    if isinstance(output, Mapping):
        structured = output.get("structured_content", output.get("structuredContent"))
    if not isinstance(structured, Mapping) or structured.get("type") != "error":
        return "success"
    error = structured.get("error")
    failure_code = error.partition(":")[0] if isinstance(error, str) else None
    if operation == "wait" and failure_code == "subagent_wait_timeout":
        return "timeout"
    return "failure"


# Harness Core emits builtin tools under a namespaced name; the legacy loop
# reports the short Vibe name, so map the ones telemetry segments on (file
# metrics, skill) and pass anything else (MCP/provided/connector) through as-is.
_TOOL_NAME_ALIASES: Final = {
    "file_system.read_file": "read_file",
    "file_system.write_file": "write_file",
    "file_system.search_replace": "edit",
    "file_system.bash": "bash",
    "skill.read": "skill",
}
# Effect lifecycle status → the legacy tool-finished taxonomy. A cancelled tool
# never produced a result, so it lands in the same bucket as a user skip.
_TOOL_EFFECT_STATUS: Final[dict[str, Literal["success", "failure", "skipped"]]] = {
    "completed": "success",
    "failed": "failure",
    "cancelled": "skipped",
    "skipped": "skipped",
}


@dataclass(frozen=True, slots=True)
class _ToolCallTelemetry:
    effect_id: str
    tool_name: str
    status: Literal["success", "failure", "skipped"]
    nb_files_created: int
    nb_files_modified: int
    file_extension: str | None


def _effect_file_extension(path: object) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    suffix = Path(path).suffix.lower()
    return suffix or None


def _terminal_tool_effect(entry: object) -> _ToolCallTelemetry | None:
    """Reconstruct a terminal ordinary tool call from a history effect.

    Mirrors ``_terminal_subagent_effect`` but for the non-subagent tools the
    harness runs in Core (file system, shell, skill, MCP, ...). Derives the file
    metrics the legacy ``tool_call_finished`` carries from the raw tool input,
    which the projection leaves verbatim under ``detail.input``.
    """
    if not isinstance(entry, Mapping) or entry.get("type") != "effect":
        return None
    effect_id = entry.get("id")
    detail = entry.get("detail")
    state = entry.get("state")
    if (
        not isinstance(effect_id, str)
        or not isinstance(detail, Mapping)
        or not isinstance(state, Mapping)
        or detail.get("kind") != "tool"
        or state.get("status") not in _TERMINAL_EFFECT_STATES
    ):
        return None
    raw_name = detail.get("toolName")
    # Subagent effects have their own content-free event; skip them here.
    if not isinstance(raw_name, str) or raw_name in _SUBAGENT_OPERATIONS:
        return None
    tool_name = _TOOL_NAME_ALIASES.get(raw_name, raw_name)
    raw_status = state.get("status")
    status = (
        _TOOL_EFFECT_STATUS.get(raw_status, "failure")
        if isinstance(raw_status, str)
        else "failure"
    )
    nb_files_created = 0
    nb_files_modified = 0
    file_extension: str | None = None
    if status == "success":
        raw_input = detail.get("input")
        # read/write use ``path``; search_replace uses ``file_path``.
        path: object = None
        if isinstance(raw_input, Mapping):
            path = raw_input.get("path")
            if path is None:
                path = raw_input.get("file_path")
        match tool_name:
            case "write_file":
                nb_files_created = 1
                file_extension = _effect_file_extension(path)
            case "edit":
                nb_files_modified = 1
                file_extension = _effect_file_extension(path)
            case "read_file":
                file_extension = _effect_file_extension(path)
    return _ToolCallTelemetry(
        effect_id=effect_id,
        tool_name=tool_name,
        status=status,
        nb_files_created=nb_files_created,
        nb_files_modified=nb_files_modified,
        file_extension=file_extension,
    )


@dataclass(frozen=True, slots=True)
class _CompactionTelemetry:
    checkpoint_id: str
    trigger: str
    succeeded: bool
    reason: Literal["tool_call", "empty_summary"] | None


def _terminal_compaction_effect(entry: object) -> _CompactionTelemetry | None:
    """Reconstruct a terminal compaction from its history checkpoint.

    The projection writes one ``kind == "compaction"`` checkpoint per attempt and
    stamps the legacy failure reason into ``details`` (Core only exposes it in the
    message). The in-progress start entry is skipped via ``generationStatus``.
    """
    if (
        not isinstance(entry, Mapping)
        or entry.get("type") != "checkpoint"
        or entry.get("kind") != "compaction"
        or entry.get("generationStatus") != "completed"
    ):
        return None
    checkpoint_id = entry.get("id")
    details = entry.get("details")
    if not isinstance(checkpoint_id, str) or not isinstance(details, Mapping):
        return None
    trigger = details.get("trigger")
    if trigger not in {"automatic", "manual"}:
        return None
    succeeded = details.get("error") is None
    reason = details.get("reason") if not succeeded else None
    if reason not in {"tool_call", "empty_summary"}:
        reason = None
    return _CompactionTelemetry(
        checkpoint_id=checkpoint_id, trigger=trigger, succeeded=succeeded, reason=reason
    )


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


def _harness_enqueue_params(params: TurnEnqueueParams) -> HarnessTurnEnqueueParams:
    return HarnessTurnEnqueueParams.model_validate(
        params.model_dump(mode="json", by_alias=True)
    )


def _harness_replace_params(
    params: TurnQueueReplaceParams,
) -> HarnessTurnQueueReplaceParams:
    return HarnessTurnQueueReplaceParams.model_validate(
        params.model_dump(mode="json", by_alias=True)
    )


def _text_from_blocks(blocks: list[ContentBlock]) -> str:
    return "\n\n".join(
        block.text for block in blocks if isinstance(block, TextContentBlock)
    )


def _text_from_session_blocks(blocks: list[SessionContentBlock]) -> str:
    return "\n\n".join(
        block.text for block in blocks if isinstance(block, SessionTextContentBlock)
    )


def _approximate_steps(app_events: Sequence[AppServerEvent]) -> int:
    return sum(
        1
        for app_event in app_events
        if isinstance(app_event, HistoryEntryAdded)
        and isinstance(app_event.entry, PublicMessageEntry)
        and app_event.entry.role in {"user", "assistant"}
    )


def _context_tokens(context_usage: VibeTokenUsage | None) -> int | None:
    """The live context, as the Harness measured it on its newest model call.

    ``None`` when the Harness reported no call to measure, which means hold the
    last reading: the cumulative ``token_usage`` is not a substitute, since it
    counts every call's prompt and climbs past the window on its own.
    """
    if context_usage is None:
        return None
    return context_usage.input_tokens + context_usage.output_tokens


def _snapshot_stats(state: PublicSessionState) -> AgentStatsSnapshot:
    """Read a session's spend off one snapshot, with no running total to add to."""
    usage = state.session.token_usage
    context_tokens = _context_tokens(state.session.context_usage)
    return AgentStatsSnapshot(
        session_prompt_tokens=usage.input_tokens if usage else 0,
        session_completion_tokens=usage.output_tokens if usage else 0,
        # A snapshot has no earlier reading to hold, so an unmeasured context
        # reports as empty rather than borrowing the cumulative prompt total.
        context_tokens=0 if context_tokens is None else context_tokens,
    )


def _repriced_stats(
    carried: AgentStatsSnapshot, derived: AgentStatsSnapshot
) -> AgentStatsSnapshot:
    return carried.model_copy(
        update={
            "input_price_per_million": derived.input_price_per_million,
            "output_price_per_million": derived.output_price_per_million,
            "cached_input_price_per_million": derived.cached_input_price_per_million,
        }
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
        details = normalized.get("details")
        if (
            normalized.get("type") == "notice"
            and "detail" not in normalized
            and isinstance(details, dict)
            and details.get("kind") == "scheduled_loop_fired"
        ):
            normalized["detail"] = normalized.pop("details")
        history.append(_project_history_entry(normalized))
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
            turn_queue=_public_turn_queue(snapshot.state.turn_queue),
        ),
        last_event_id=last_event_id,
    )


def _harness_event_watermark(event: dict[str, Any]) -> int:
    event_id = event.get("eventId")
    if isinstance(event_id, int):
        return event_id
    if isinstance(event_id, str) and event_id.isdecimal():
        return int(event_id)
    _reject("a Harness session event without an event id")


def _project_history_entry(value: object) -> PublicHistoryEntry:
    source = validate_history_entry(value)
    category = unified_tool_category(source)
    projected = project_unified_history_entry(source)
    if category is not None:
        add_unified_tool_projection(
            category=category,
            outcome="degraded" if projected == source else "projected",
        )
    return _normalize_effect_output(projected)


def _public_session(
    session: HarnessPublicSession,
    cwd: str | None,
    *,
    harness: Literal["legacy", "unified"] = "unified",
) -> PublicSession:
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
    context_usage = (
        VibeTokenUsage.model_validate(
            session.context_usage.model_dump(mode="json", by_alias=True)
        )
        if session.context_usage is not None
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
        context_usage=context_usage,
        harness=harness,
    )


def _legacy_public_session(
    session: ResumeSessionInfo,
    config: VibeConfigSchema,
    *,
    harness: Literal["legacy", "unified"],
) -> PublicSession:
    """Project a legacy ``ResumeSessionInfo`` into a ``PublicSession`` row.

    Legacy sessions store timestamps as ISO strings; ``PublicSession`` expects
    int milliseconds. ``_time_ms`` parses the ISO format and falls back to
    ``now_ms()`` on parse failure, so a malformed timestamp never breaks the
    listing.
    """
    return PublicSession(
        id=session.session_id,
        root_session_id=None,
        parent_session_id=session.parent_session_id,
        title=session.title,
        preview=SessionLoader.get_first_user_message(
            session.session_id, config.session_logging
        ),
        status=VibeIdleSessionStatus(),
        created_at=_time_ms(session.start_time or session.updated_at),
        updated_at=_time_ms(session.updated_at),
        cwd=session.cwd or None,
        harness=harness,
    )


def _read_legacy_session(
    params: SessionReadParams, config: VibeConfigSchema
) -> SessionReadResponse | None:
    """Build a ``SessionReadResponse`` from a legacy session on disk.

    The unified host's ``read`` only knows about the unified store. A legacy
    session listed by the merged ``list`` has not been imported yet, so the host
    raises ``NOT_FOUND``. This fallback reads the legacy JSONL transcript and
    projects it into ``PublicHistoryEntry`` objects so the picker preview works
    without a full resume/import.
    """
    session_dir = SessionLoader.find_session_by_id(
        params.session_id, config.session_logging
    )
    if session_dir is None:
        return None
    try:
        messages, metadata_dict = SessionLoader.load_session(session_dir)
    except (OSError, ValueError):
        return None
    if not metadata_dict:
        return None
    metadata = SessionMetadata.model_validate(metadata_dict)
    session_id = metadata.session_id
    history = project_message_history(session_id, messages, metadata)
    # Limit to the requested page size for consistency with the unified path.
    history_limit = params.history.limit if params.history else len(history)
    history = history[-history_limit:] if history_limit < len(history) else history
    return SessionReadResponse(
        state=PublicSessionState(
            event_id=0,
            session=_legacy_public_session(
                ResumeSessionInfo(
                    session_id=session_id,
                    cwd=metadata.environment.get("working_directory") or "",
                    title=metadata.title,
                    start_time=metadata.start_time,
                    updated_at=metadata.start_time,
                    parent_session_id=metadata.parent_session_id,
                ),
                config,
                harness="legacy",
            ),
            history=history,
            turns=[],
            turn_queue=PublicTurnQueue(),
        ),
        last_event_id=0,
    )


def _encode_merged_cursor(session: PublicSession) -> str:
    """Encode a cursor for the merged list from (updated_at, session_id)."""
    return base64.b64encode(f"{session.updated_at}:{session.id}".encode()).decode()


def _decode_merged_cursor(cursor: str) -> tuple[int, str] | None:
    """Decode a merged cursor back into (updated_at, session_id).

    Returns ``None`` if the cursor is malformed; the caller treats that as
    "start from the beginning".
    """
    try:
        decoded = base64.b64decode(cursor).decode()
        updated_str, _, session_id = decoded.partition(":")
        return int(updated_str), session_id
    except (ValueError, UnicodeDecodeError):
        return None


def _merged_cursor_index(merged: list[PublicSession], cursor: str | None) -> int:
    """Find the start index for client-side pagination on the merged list.

    The merged list is sorted by ``(updated_at, id)`` descending. The cursor
    identifies the *last item of the previous page* — the start index is the
    position of the first item strictly less than the cursor key. A ``None``
    cursor means "start from the beginning" (index 0).
    """
    if cursor is None:
        return 0
    decoded = _decode_merged_cursor(cursor)
    if decoded is None:
        return 0
    cursor_updated_at, cursor_id = decoded
    for i, session in enumerate(merged):
        if session.updated_at < cursor_updated_at:
            return i
        if session.updated_at == cursor_updated_at and session.id < cursor_id:
            return i
    return len(merged)


def _project_scheduled_loop(loop: CoreScheduledLoop) -> ScheduledLoop:
    return ScheduledLoop(
        id=loop.id,
        prompt=loop.prompt,
        interval_seconds=loop.interval_seconds,
        next_fire_at=loop.next_fire_at,
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
        queue_item_id=getattr(raw, "queue_item_id", None),
    )


def _public_turn_queue(queue: object) -> PublicTurnQueue:
    raw = cast(Any, queue)
    return PublicTurnQueue.model_validate(raw.model_dump(mode="json", by_alias=True))


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


def _turn_queue_event_envelope(
    queue: PublicTurnQueue, event_id: int, session_id: str
) -> SessionBackendEvent:
    params = TurnQueueUpdatedParams(
        event_id=event_id,
        session_id=session_id,
        emitted_at=int(time.time() * 1000),
        queue=queue,
    )
    return SessionBackendEvent(
        event=TurnQueueUpdated(queue),
        method="turn_queue_updated",
        params=params,
        session_id=session_id,
        event_id=event_id,
    )


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
                    # The harness reports remote descriptions verbatim, so they
                    # routinely span several lines. The `/mcp` detail view
                    # renders one non-wrapping row per tool, so flatten here,
                    # at the single producer every connector read flows through.
                    description=format_tool_display_description(
                        tool.description, source_name=source.alias
                    ),
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
        _raise_session_backend_error(exc)
    except ValueError as exc:
        raise SessionBackendError(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc


def _raise_session_backend_error(exc: HarnessSessionError) -> Never:
    if exc.code == "context_compaction_failed":
        data: dict[str, Any] = {"reason": "context_compaction_failed"}
        if exc.details is not None:
            reason = exc.details.get("reason")
            if isinstance(reason, str):
                data["reason"] = reason
            details = exc.details.get("details")
            if details is not None:
                data["details"] = details
        raise SessionBackendError(
            ProtocolErrorCode.COMPACTION_FAILED,
            str(exc),
            data,
            after_response=getattr(exc, "after_response", None),
            on_response_abandoned=getattr(exc, "on_response_abandoned", None),
        ) from exc
    if exc.code == "callback_closed":
        raise SessionBackendError(ProtocolErrorCode.CALLBACK_CLOSED, str(exc)) from exc
    if exc.code == "callback_not_found":
        raise SessionBackendError(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
    if exc.code == "callback_conflict":
        raise SessionBackendError(ProtocolErrorCode.CONFLICT, str(exc)) from exc
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
    if exc.code == "turn_queue_full":
        max_items = exc.details.get("max_items") if exc.details else None
        raise SessionBackendError(
            ProtocolErrorCode.CONFLICT, str(exc), {"maxItems": max_items}
        ) from exc
    if exc.code == "turn_queue_idempotency_conflict":
        idempotency_key = exc.details.get("idempotency_key") if exc.details else None
        raise SessionBackendError(
            ProtocolErrorCode.CONFLICT, str(exc), {"idempotencyKey": idempotency_key}
        ) from exc
    if exc.code == "turn_queue_item_not_found":
        queue_item_id = exc.details.get("queue_item_id") if exc.details else None
        raise SessionBackendError(
            ProtocolErrorCode.NOT_FOUND, str(exc), {"queueItemId": queue_item_id}
        ) from exc
    code = (
        ProtocolErrorCode.CONFLICT
        if exc.code
        in {
            "child_session_requires_parent",
            "session_busy",
            "client_command_conflict",
            "turn_conflict",
            "turn_queue_pending",
            "unfinished_work_migration",
        }
        else ProtocolErrorCode.INTERNAL_ERROR
    )
    data = {"harnessCode": exc.code}
    if exc.details is not None:
        data["details"] = exc.details
        reason = exc.details.get("reason")
        if isinstance(reason, str):
            data["reason"] = reason
    raise SessionBackendError(code, str(exc), data) from exc


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
