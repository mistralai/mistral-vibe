"""Translation between the Vibe app-server port and the Unified Harness."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
import contextlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import getpass
import json
from pathlib import Path
import time
from typing import Any, Final, Literal, Never, Protocol, assert_never, cast
from uuid import uuid4

from mistralai_vibe_local_harness.protocol import RustHarnessConfig
import mistralai_vibe_local_harness.session_protocol as harness_session_protocol
from mistralai_vibe_local_harness.session_protocol import (
    Event as HarnessEvent,
    PublicSession as HarnessPublicSession,
    PublicSessionState as HarnessPublicSessionState,
    ResolvedPluginDefinition,
    SessionReadParams as HarnessSessionReadParams,
    SessionSnapshot as HarnessSessionSnapshot,
    SessionStartParams as HarnessSessionStartParams,
    TokenUsage as HarnessTokenUsage,
    TurnEnqueueParams as HarnessTurnEnqueueParams,
    TurnQueueReadParams as HarnessTurnQueueReadParams,
    TurnQueueRemoveParams as HarnessTurnQueueRemoveParams,
    TurnQueueReplaceParams as HarnessTurnQueueReplaceParams,
    TurnQueueResumeParams as HarnessTurnQueueResumeParams,
    TurnQueueUpdatedEvent as HarnessTurnQueueUpdatedEvent,
)
from mistralai_vibe_local_harness.vibe import (
    CLASSIFICATION_EVENT_TYPE,
    CompiledHooks,
    CompletedWorktreeHistoryEntry as HarnessCompletedWorktreeHistoryEntry,
    ConnectorGatewayClient as HarnessConnectorGatewayClient,
    ConnectorRouteSnapshot as HarnessConnectorRouteSnapshot,
    DeferredTurnPreparation as HarnessDeferredTurnPreparation,
    DeferredTurnPreparationContext as HarnessDeferredTurnPreparationContext,
    DeferredTurnPreparationError as HarnessDeferredTurnPreparationError,
    DeferredTurnPreparationResult as HarnessDeferredTurnPreparationResult,
    DeferredTurnStartCapability,
    DeferredTurnStartParams as HarnessDeferredTurnStartParams,
    FailedWorktreeHistoryEntry as HarnessFailedWorktreeHistoryEntry,
    HarnessNotImplementedError,
    HarnessReplayDivergenceError,
    HarnessSessionError,
    HarnessSessionListItem,
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
    MCPToolFilter,
    PreparedRuntimeInput as HarnessPreparedRuntimeInput,
    PublicHistoryEntry as HarnessPublicHistoryEntry,
    RequestSentTelemetry,
    ResolvedConnector as HarnessResolvedConnector,
    ResolvedConnectorCatalog as HarnessResolvedConnectorCatalog,
    ResolvedConnectorSelection as HarnessResolvedConnectorSelection,
    ResolvedConnectorSetting as HarnessResolvedConnectorSetting,
    ResolvedConnectorTool as HarnessResolvedConnectorTool,
    ResolvedMCPCatalog as HarnessResolvedMCPCatalog,
    ResolvedMCPServerConfig as HarnessResolvedMCPServerConfig,
    RunningWorktreeHistoryEntry as HarnessRunningWorktreeHistoryEntry,
    TurnMentionStats as HarnessTurnMentionStats,
    TurnStartRequest as HarnessTurnStartRequest,
    UnifiedHarnessSessionBackend,
    UnifiedHarnessSessionBackendHost,
)
from mistralai_vibe_local_harness.vibe._storage import SessionPin, sha256_json
from mistralai_vibe_local_harness.vibe.plugins import SessionPluginBinding
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
from vibe.app_server._config_paths import model_thinking_path
from vibe.app_server._config_write import (
    config_write_ops_to_patches,
    config_write_targets,
    model_after_write,
    model_config_write_ops,
)
from vibe.app_server._deferred_config import DeferredConfiguration
from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._host import config_schema_response
from vibe.app_server._identity import IdentityController, IdentityGateway
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._model import ProtocolModel, validate_backend_wire, validate_wire
from vibe.app_server._narration import NarrationContext, NarrationService
from vibe.app_server._patch import apply_json_patch
from vibe.app_server._plugin_mcp import PluginMCPCatalog
from vibe.app_server._plugins import (
    PluginReloadUnavailableError,
    SessionPlugins,
    UnifiedPluginProvider,
    plugin_reload_notices,
)
from vibe.app_server._projection import (
    project_agent_summary,
    project_config_view,
    project_debug_logs,
    project_installed_skill_summaries,
    project_message_history,
    writable_disabled_skills,
)
from vibe.app_server._projector import EventProjector, ProjectedUpdate
from vibe.app_server._provided_tools import (
    TODO_TOOL_NAME,
    VIBE_PROVIDED_TOOL_MODES,
    VIBE_PROVIDED_TOOL_NAMES,
    VIBE_TOOL_GROUP,
    VibeProvidedTools,
    resolved_max_todos,
)
from vibe.app_server._root_session import rebind_history_with_checkpoint
from vibe.app_server._session_backend_port import (
    ConnectorAuthRequest,
    MCPAuthorizationProvider,
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationSnapshot,
    MCPCatalogOwner,
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
    model_thinking_is_session_writable,
    set_session_active_model_override,
    set_session_reasoning_effort_override,
    with_session_active_model_write,
)
from vibe.app_server._shell import (
    ShellConflictError,
    ShellController,
    manual_shell_context,
    manual_shell_output_limit,
    resolve_workspace_cwd,
    shell_effect_cancelled,
    shell_effect_detail,
    shell_effect_error,
    shell_effect_state,
)
from vibe.app_server._skills_service import SkillsController
from vibe.app_server._state import history_page
from vibe.app_server._tool_projection import project_effect_output_value
from vibe.app_server._turn_input import session_content_blocks_from_vibe
from vibe.app_server._unified_permissions import (
    ProvidedToolNames,
    UnifiedPermissionResolver,
)
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    UnifiedScheduledLoops,
)
from vibe.app_server._unified_scratchpad import scratchpad_block_to_restate
from vibe.app_server._unified_tool_observability import add_unified_tool_projection
from vibe.app_server._unified_tool_projection import (
    project_unified_history_entry,
    unified_tool_category,
)
from vibe.app_server._unified_vibe_code import (
    UnifiedTeleportContextSummarizer,
    UnifiedVibeCodeSession,
)
from vibe.app_server._utils import iso_from_time_ms, now_ms, optional_time_ms, time_ms
from vibe.app_server._vibe_code import (
    VibeCodeAccessError,
    VibeCodeConflictError,
    VibeCodeController,
    VibeCodeError,
)
from vibe.app_server._vision import SessionImageDescriber
from vibe.app_server._workspace import (
    PromptPreparationError,
    WorkspaceTrustError,
    decide_workspace_trust,
    is_trust_grant,
    mentioned_file_content_blocks_async,
    prepare_prompt_from_context,
    require_trust_session_id,
    resolve_session_trust_target,
)
from vibe.app_server._worktree_effects import WorktreeEffect, WorktreeProgress
from vibe.app_server._worktree_session import SessionWorktrees, WorktreeResolution
from vibe.app_server.config import ProxySettingsView
from vibe.app_server.connector_catalog import (
    ConnectorCatalogError,
    ConnectorCatalogService,
)
from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    ChildSessionUpdated,
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
    TurnRetrying,
    TurnStarted,
    reconcile_snapshot,
)
from vibe.app_server.mcp_catalog import project_mcp_sources
from vibe.app_server.models import (
    AccountView,
    AgentStatsSnapshot,
    AgentSummary,
    ApprovalCallbackDetail,
    ApprovalCallbackOutput,
    ApprovalDecisionType,
    ArchivedSessionStatus as VibeArchivedSessionStatus,
    BlockedSessionStatus as VibeBlockedSessionStatus,
    CompletedEffectState,
    ConnectorCounts,
    ContentBlock,
    EffectDetail,
    EffectState,
    FailedEffectState,
    FailedSessionStatus as VibeFailedSessionStatus,
    GenericEffectDetail,
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
    PublicChildSession,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicRetryCategory,
    PublicRetryState,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnQueue,
    PublicTurnStatus,
    RunningEffectState,
    RunningSessionStatus as VibeRunningSessionStatus,
    ScheduledLoop,
    SessionLogSummary,
    SkillEffectDetail,
    SkillSummary,
    SubagentEffectDetail,
    TextContentBlock,
    TokenUsage as VibeTokenUsage,
    TurnErrorCode,
    WorktreeEffectDetail,
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
    ChildSessionUpdatedParams,
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
    ConfigWriteOpWire,
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
    ModelConfigWriteParams,
    NarrationSummarizeParams,
    NarrationSummarizeResponse,
    PageRequest,
    PluginInfoParams,
    PluginInfoResponse,
    PluginReloadParams,
    PluginReloadResponse,
    ProtocolErrorCode,
    RuntimeMutationResponse,
    RuntimeMutationStatus,
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
    SessionEmbeddedResourceContentBlock,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryClearParams,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionImageContentBlock,
    SessionListParams,
    SessionListResponse,
    SessionLogReadParams,
    SessionLogReadResponse,
    SessionOptions,
    SessionPinParams,
    SessionPinResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionReadyReadResponse,
    SessionReadyWaitResponse,
    SessionResourceLinkContentBlock,
    SessionResumeParams,
    SessionRewindParams,
    SessionRewindReadParams,
    SessionRewindReadResponse,
    SessionRewindResponse,
    SessionSettingsUpdateParams,
    SessionShellCommandParams,
    SessionShellCommandResponse,
    SessionSnapshotParams,
    SessionStartParams,
    SessionStopParams,
    SessionStopResponse,
    SessionTextContentBlock,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    SessionUpdatedParams,
    ShellRunParams,
    ShellRunResponse,
    StatsUpdatedParams,
    TelemetryRecordParams,
    TeleportCancelParams,
    TeleportCancelResponse,
    TeleportPushRespondParams,
    TeleportStartParams,
    TeleportStartResponse,
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
    TurnQueueSteerParams,
    TurnQueueSteerResponse,
    TurnQueueUpdatedParams,
    TurnRetryingParams,
    TurnStartedParams,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnSteerResponse,
    TurnUserInputEntry,
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
from vibe.core.agents.manager import AgentManager
from vibe.core.config import MissingAPIKeyError, VibeConfigSchema
from vibe.core.config.admin_config import MANAGED_CONFIG_TIMEOUT
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator, ConfigPatchValidationError
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse
from vibe.core.experiments.session import (
    initialize_experiments as session_initialize_experiments,
)
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
)
from vibe.core.session.saved_sessions import (
    delete_saved_session,
    update_saved_session_pin,
)
from vibe.core.session.session_loader import METADATA_FILENAME, SessionLoader
from vibe.core.session.session_logger import SessionLogger
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
from vibe.core.telemetry.types import LaunchContext, ProjectPickerTelemetryPayload
from vibe.core.teleport.types import TeleportPushResponseEvent, TeleportYieldEvent
from vibe.core.tools.builtins.skill import already_loaded_message, skill_content_marker
from vibe.core.trusted_folders import has_agents_md_file
from vibe.core.types import ScheduledLoop as CoreScheduledLoop, SessionMetadata
from vibe.observability.logging import logger
from vibe.setup.auth.whoami import WhoAmICache, WhoAmIResult, resolve_user_plan
from vibe.utils import AgentEntrypoint
from vibe.utils.io import read_safe
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
    skill_payloads: Mapping[str, str]

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
                affinity_id=self.completion_attribution.affinity_id,
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
    mcp_authentication: MCPAuthenticationService = field(
        default_factory=MCPAuthenticationService
    )
    # False means ``storage_root`` is a throwaway directory the host deletes on
    # shutdown, so nothing may treat it as durable.
    session_logging_enabled: bool = True
    # The adapter the harness was configured with. Held so ``reconfigure_mcp``
    # and ``plugin/reload`` can update its plugin-owned name set without
    # re-creating it or touching the harness.
    mcp_authorization_adapter: Any = None
    mcp_catalog_service: Any = None
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
    # Route-to-published-name map the MCP and connector projections write as the
    # Harness accepts a catalogue, and ``permissions`` reads to find the config
    # key a gated route was configured under. Frozen field, mutable object.
    provided_names: ProvidedToolNames = field(default_factory=ProvidedToolNames)
    # Backs the executor Vibe's own tool group runs through, and holds the per-session
    # todo list the closures read. Frozen field, mutable object.
    vibe_tools: VibeProvidedTools = field(default_factory=VibeProvidedTools)


class SessionContextBuilder(Protocol):
    def __call__(
        self,
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: AgentEntrypoint = "cli",
    ) -> Awaitable[UnifiedSessionContext]: ...


@dataclass(frozen=True, slots=True)
class UnifiedReadContext:
    """What a session read needs. Deliberately cannot express a runtime."""

    storage_root: str
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    legacy_source_loader: LegacySourceLoader
    legacy_source_resolver: LegacySourceResolver

    @property
    def config(self) -> VibeConfigSchema:
        return self.config_orchestrator.config


class ReadContextBuilder(Protocol):
    def __call__(self, options: SessionOptions) -> Awaitable[UnifiedReadContext]: ...


# Legacy writes the resolved response into the session's ``meta.json``. Unified
# sidecar metadata is Vibe-owned and does not persist experiment assignments, so
# continuity comes from the global eval cache that
# ``_apply_cached_experiment_variants`` reads at build time.
class _NullExperimentSink:
    async def persist_experiments(self, response: EvalResponse | None) -> None:
        del response


@dataclass(frozen=True, slots=True)
class _MergedListingKey:
    cwd: str | None
    cwds: tuple[str, ...] | None
    root_session_id: str | None
    parent_session_id: str | None
    pinned: bool | None


@dataclass(frozen=True, slots=True)
class _MergedListing:
    """One cursor walk's view of the merged unified + legacy session list."""

    key: _MergedListingKey
    sessions: list[PublicSession]
    continue_session_id: str | None


@dataclass(frozen=True, slots=True)
class _SessionMetadataProjection:
    session_id: str
    patch: tuple[JsonPatchOperation, ...]


@dataclass(frozen=True, slots=True)
class _Unchanged:
    """Marks a nullable metadata field the caller is not touching."""


_UNCHANGED: Final = _Unchanged()
_NULL_EXPERIMENT_SINK: Final = _NullExperimentSink()
_SESSION_LISTING_PAGE = 500


def _unified_session_dir(storage_root: str, session_id: str) -> Path:
    return Path(storage_root) / "unified" / session_id


def _read_unified_session_metadata(session_dir: Path) -> SessionMetadata | None:
    try:
        raw = json.loads(read_safe(session_dir / METADATA_FILENAME).text)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return SessionMetadata.model_validate(raw)
    except ValidationError:
        return None


def _metadata_username() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _metadata_title_source(session: HarnessPublicSession) -> Literal["auto", "manual"]:
    raw = getattr(session, "title_source", None)
    value = getattr(raw, "value", raw)
    return "manual" if value == "manual" else "auto"


def _metadata_bumped_at(
    previous: SessionMetadata | None, bumped_at: datetime | None
) -> str | None:
    if bumped_at is None:
        return previous.bumped_at if previous is not None else None

    candidate = bumped_at.astimezone(UTC)
    previous_bumped_at = previous.bumped_at if previous is not None else None
    existing = optional_time_ms(previous_bumped_at)
    candidate_ms = int(candidate.timestamp() * 1000)
    if existing is not None and existing >= candidate_ms:
        return previous_bumped_at
    return candidate.isoformat()


def _metadata_pinned_at(
    previous: SessionMetadata | None, pinned_at: datetime | None | _Unchanged
) -> str | None:
    """Resolve the pin to persist.

    Unlike ``bumped_at``, which only ever moves forward, a pin is a command:
    unpinning has to be able to clear a timestamp a pin just wrote. That makes
    ``None`` a real value here, so callers that are not touching the pin pass
    ``_UNCHANGED`` instead.

    Pinning something already pinned keeps the original timestamp, so the
    pinned shelf does not reorder for a request that changed nothing.
    """
    previous_pinned_at = previous.pinned_at if previous is not None else None
    if isinstance(pinned_at, _Unchanged):
        return previous_pinned_at
    if pinned_at is None:
        return None
    if previous_pinned_at is not None:
        return previous_pinned_at
    return pinned_at.astimezone(UTC).isoformat()


def _build_unified_session_metadata(
    session: HarnessPublicSession,
    cwd: str | None,
    *,
    previous: SessionMetadata | None = None,
    bumped_at: datetime | None = None,
    pinned_at: datetime | None | _Unchanged = _UNCHANGED,
) -> SessionMetadata:
    if previous is not None and previous.session_id != session.id:
        previous = None

    environment = dict(previous.environment) if previous is not None else {}
    environment["working_directory"] = cwd
    origin_directory = (
        previous.origin_directory
        if previous is not None
        else environment["working_directory"]
    )

    return SessionMetadata(
        session_id=session.id,
        parent_session_id=session.parent_session_id,
        start_time=iso_from_time_ms(session.created_at),
        end_time=iso_from_time_ms(session.updated_at),
        git_commit=previous.git_commit if previous is not None else None,
        git_branch=previous.git_branch if previous is not None else None,
        environment=environment,
        origin_directory=origin_directory,
        username=previous.username if previous is not None else _metadata_username(),
        child_sessions=previous.child_sessions if previous is not None else [],
        loops=previous.loops if previous is not None else [],
        title=session.title,
        title_source=_metadata_title_source(session),
        bumped_at=_metadata_bumped_at(previous, bumped_at),
        pinned_at=_metadata_pinned_at(previous, pinned_at),
        experiments=previous.experiments if previous is not None else None,
        config=previous.config if previous is not None else None,
        import_provenance=previous.import_provenance if previous is not None else None,
        created_worktree=previous.created_worktree if previous is not None else None,
    )


async def _persist_unified_session_metadata(
    session_dir: Path,
    session: HarnessPublicSession,
    cwd: str | None,
    *,
    previous: SessionMetadata | None = None,
    bumped_at: datetime | None = None,
    pinned_at: datetime | None | _Unchanged = _UNCHANGED,
) -> SessionMetadata:
    metadata = _build_unified_session_metadata(
        session, cwd, previous=previous, bumped_at=bumped_at, pinned_at=pinned_at
    )
    session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    await SessionLogger.persist_metadata(metadata.model_dump(mode="json"), session_dir)
    return metadata


def _metadata_bumped_at_ms(metadata: SessionMetadata | None) -> int | None:
    return optional_time_ms(metadata.bumped_at) if metadata is not None else None


def _metadata_pinned_at_ms(metadata: SessionMetadata | None) -> int | None:
    return optional_time_ms(metadata.pinned_at) if metadata is not None else None


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


class _UnifiedSkillsHost:
    """Presents the Unified backend as a ``SkillsHost``.

    Unlike the legacy loop, a skill change here converges by reloading the
    config and re-deriving: that rebuilds both the catalogue Core is configured
    with and the payload map the Runtime serves, which have to move together.
    """

    def __init__(
        self,
        context: UnifiedSessionContext,
        *,
        runtime: Callable[[], RuntimeSnapshot],
        require_idle: Callable[[], None],
        require_session: Callable[[str], None],
        refresh: Callable[[], Awaitable[RuntimeSnapshot]],
    ) -> None:
        self._context = context
        self._runtime = runtime
        self._require_idle = require_idle
        self._require_session = require_session
        self._refresh = refresh

    @property
    def config(self) -> VibeConfigSchema:
        return self._context.config_orchestrator.config

    @property
    def config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        return self._context.config_orchestrator

    @property
    def skill_roots(self) -> list[Path]:
        return self._context.harness_files.project_roots

    def require_idle(self) -> None:
        self._require_idle()

    def require_session(self, session_id: str) -> None:
        self._require_session(session_id)

    def list_skills(self) -> list[SkillSummary]:
        return list(self._runtime().skills)

    def installed_skills(self) -> list[SkillSummary]:
        """Registry pins (one per name+scope), local skills, and plugin skills."""
        mgr = SkillManager(
            config_getter=lambda: self._context.config_orchestrator.config,
            harness_files=self._context.harness_files,
        )
        orchestrator = self._context.config_orchestrator
        installed = project_installed_skill_summaries(
            mgr.installed_skills(),
            orchestrator.config,
            writable_disabled_skills(orchestrator),
        )
        # The runtime snapshot already includes plugin skills (resolved by
        # ``project_core_skills``); merge in any that are not already present
        # so the browser shows them alongside registry pins and local skills.
        existing = {s.name for s in installed}
        installed.extend(
            s.model_copy(update={"enabled": True, "locked": True})
            for s in self._runtime().skills
            if s.source == "plugin" and s.name not in existing
        )
        return installed

    async def refresh(self) -> RuntimeSnapshot:
        return await self._refresh()


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


def _require_session_logging(context: UnifiedSessionContext) -> None:
    # The legacy source resolver still finds ids under the save dir with logging
    # off, so without this the harness imports the transcript into the throwaway
    # store and every new turn goes out with it. Same message as the legacy runtime.
    if context.session_logging_enabled:
        return
    raise SessionBackendError(
        ProtocolErrorCode.NOT_FOUND,
        "Session logging is disabled. Enable it in config to use --continue or --resume",
    )


def adapt_harness_host(
    host: object,
    build_session_context: SessionContextBuilder,
    services: SessionBackendServices | None = None,
    *,
    build_read_context: ReadContextBuilder | None = None,
    launch_context_getter: LaunchContextGetter | None = None,
    release_storage: Callable[[], None] | None = None,
) -> UnifiedHarnessBackendHostAdapter:
    return UnifiedHarnessBackendHostAdapter(
        cast(UnifiedHarnessSessionBackendHost, host),
        build_session_context,
        services,
        build_read_context=build_read_context,
        launch_context_getter=launch_context_getter,
        release_storage=release_storage,
    )


class UnifiedHarnessBackendHostAdapter:
    """Vibe's session Host backed by the Unified Harness Runtime."""

    def __init__(
        self,
        host: UnifiedHarnessSessionBackendHost,
        build_context: SessionContextBuilder,
        services: SessionBackendServices | None = None,
        *,
        build_read_context: ReadContextBuilder | None = None,
        launch_context_getter: LaunchContextGetter | None = None,
        release_storage: Callable[[], None] | None = None,
    ) -> None:
        self._host = host
        self._build_context = build_context
        # Supplied by the composition root. Without it a listing falls back to
        # building a whole session context, which is correct but pays for
        # plugins, MCP and connectors it never reads.
        self._build_read_context = build_read_context
        self._services = services
        self._launch_context_getter = launch_context_getter
        self._release_storage = release_storage
        self._telemetry_clients: set[TelemetryClient] = set()
        self._adapters: dict[str, UnifiedHarnessBackendAdapter] = {}
        self._worktrees = SessionWorktrees()
        self._merged_listing: _MergedListing | None = None

    @property
    def harness_kind(self) -> SessionBackendKind:
        return "unified"

    async def start(self, params: SessionStartParams) -> SessionLifecycleResult:
        if params.agent_config.worktree is None:
            return await self._start_resolved(params, params.agent_config)

        options = params.agent_config.model_copy(update={"worktree": None})
        result = await self._start_resolved(params, options)
        backend = cast(UnifiedHarnessBackendAdapter, result.backend)
        backend.defer_turns(
            self._worktrees.raise_behind(
                backend.session_id,
                lambda moved: self._move_session(backend, moved),
                params.agent_config,
                options,
            ),
            worktree_progress=(
                WorktreeProgress(
                    name=(
                        params.agent_config.worktree.name
                        if params.agent_config.worktree.kind == "create"
                        else None
                    )
                )
                if params.agent_config.worktree.kind in {"auto", "create"}
                else None
            ),
        )
        return result

    async def _move_session(
        self, backend: UnifiedHarnessBackendAdapter, options: SessionOptions
    ) -> None:
        previous = backend.cwd
        cwd = _session_cwd(options)
        context, _ = await self._lifecycle_context(options, require_api_key=False)
        # Pushing the context is the commit point, because it is what retargets
        # the tools. Everything after it is bookkeeping over a session that has
        # already moved, so a failure there is logged rather than raised: raising
        # would trigger startup cleanup for the worktree the tools now use.
        await backend.adopt_context(context)
        try:
            await backend.persist_cwd(cwd)
            if previous is not None and options.trust_workspace:
                context.harness_files.trust_store.revoke_session_trust(Path(previous))
            await self._announce_cwd(backend.session_id, cwd)
        except Exception as exc:
            logger.warning("Failed to settle a moved session", exc_info=exc)

    async def _notify_from_services(self, method: str, params: ProtocolModel) -> None:
        if self._services is not None:
            await self._services.notify(method, params)

    async def _announce_cwd(self, session_id: str, cwd: str) -> None:
        if self._services is None:
            return
        await self._services.notify(
            "session/updated",
            SessionUpdatedParams(
                event_id=0,
                session_id=session_id,
                patch=[JsonPatchOperation(op="replace", path="/cwd", value=cwd)],
                emitted_at=now_ms(),
            ),
        )

    async def _start_resolved(
        self, params: SessionStartParams, options: SessionOptions
    ) -> SessionLifecycleResult:
        resolution = await self._worktrees.resolve_for_start(options)
        try:
            context, derivation = await self._lifecycle_context(
                resolution.options, require_api_key=True
            )
            session = await _harness_call(
                self._host.start(
                    HarnessSessionStartParams(history_limit=params.history_limit),
                    cwd=_session_cwd(resolution.options),
                    ephemeral=True,
                    hook_bindings=context.hooks.bindings,
                    hook_handlers=context.hooks.handlers,
                )
            )
            backend = self._adapter(
                session,
                context,
                derivation,
                scheduled_loops_enabled=not resolution.options.headless,
            )
            await self._prepare_opened_backend(
                backend,
                backend.restore_scheduled_loops(),
                worktree_resolution=resolution,
            )
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise
        # Fresh start emits new_session/ready once the background experiment eval
        # resolves, so those events carry user_plan/experiment_attributes — the
        # only path that emits both, mirroring the legacy AgentLoop.
        self._start_experiments(backend, after=backend._emit_fresh_start_telemetry)
        return SessionLifecycleResult(
            backend=backend, after_response=backend.start_scheduled_loops
        )

    async def resume(self, params: SessionResumeParams) -> SessionLifecycleResult:
        self._worktrees.reject_input(params.agent_config)
        context, derivation = await self._lifecycle_context(
            params.agent_config, require_api_key=True
        )
        _require_session_logging(context)
        context, derivation, resolution = await self._pin_to_session_cwd(
            params.agent_config,
            params.session_id,
            context,
            derivation,
            require_api_key=True,
        )
        try:
            session, context = await _harness_call(
                self._resume_harness(params.session_id, params.history_limit, context)
            )
            backend = self._adapter(
                session,
                context,
                derivation,
                scheduled_loops_enabled=not params.agent_config.headless,
            )
            await self._prepare_opened_backend(
                backend,
                backend.restore_scheduled_loops(),
                worktree_resolution=resolution,
            )
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise
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
        context, derivation = await self._lifecycle_context(
            params.agent_config, require_api_key=True
        )
        _require_session_logging(context)
        listing = await _harness_call(self._host.list(limit=1))
        target_id = listing.continue_session_id
        if target_id is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, "No session to continue"
            )
        context, derivation, resolution = await self._pin_to_session_cwd(
            params.agent_config, target_id, context, derivation, require_api_key=True
        )
        try:
            session, context = await _harness_call(
                self._resume_harness(target_id, params.history_limit, context)
            )
            backend = self._adapter(
                session,
                context,
                derivation,
                scheduled_loops_enabled=not params.agent_config.headless,
            )
            await self._prepare_opened_backend(
                backend,
                backend.restore_scheduled_loops(),
                worktree_resolution=resolution,
            )
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise
        self._start_experiments(backend)
        return SessionLifecycleResult(
            backend=backend, after_response=backend.start_scheduled_loops
        )

    async def _resume_harness(
        self, session_id: str, history_limit: int, context: UnifiedSessionContext
    ) -> tuple[UnifiedHarnessSessionBackend, UnifiedSessionContext]:
        """Configure connector routes before recovery and retry old stores if needed.

        A store written before capability baselines were journalled replays its
        connector-bearing inputs against whatever capabilities the resume supplies,
        so it diverges unless the catalog is in place first. The retry below only
        catches the divergences a Harness new enough to flag ``capabilities_required``
        reports, which is why the eager pass stays: against the pinned Harness it is
        the only thing standing between a pre-existing connector session and a
        replay it cannot finish.
        """

        async def resume() -> UnifiedHarnessSessionBackend:
            return await self._host.resume(
                session_id,
                history_limit=history_limit,
                hook_bindings=context.hooks.bindings,
                hook_handlers=context.hooks.handlers,
            )

        resolved = await self._resolve_connectors_for_resume(context)
        if resolved is not None:
            context = resolved
        try:
            return await resume(), context
        except HarnessReplayDivergenceError as exc:
            if not exc.details or exc.details.get("capabilities_required") is not True:
                raise
            resolved = await self._resolve_connectors_for_resume(context)
            if resolved is None:
                raise
            context = resolved
            return await resume(), context

    async def _resolve_connectors_for_resume(
        self, context: UnifiedSessionContext
    ) -> UnifiedSessionContext | None:
        """Configure the connector routes a legacy store needs to replay its journal."""
        service = context.connector_catalog_service
        if service is None or context.connector_catalog is not None:
            return None
        try:
            catalog = await service.resolve_catalog(context.config_orchestrator)
            if catalog is None:
                return None
            selection = service.resolve_selection(context.config_orchestrator, catalog)
            resolved = replace(
                context, connector_catalog=catalog, connector_selection=selection
            )
            self._configure_connectors(resolved, catalog, selection)
        except ConnectorCatalogError:
            logger.warning("Connector catalog is unavailable during resume recovery")
            return None
        except Exception:
            # The caller re-raises the divergence when this returns None. Letting an
            # unexpected failure out instead would replace a replay error the resume
            # already reports with one about connectors.
            logger.exception("Failed to configure connectors for resume recovery")
            return None
        return resolved

    async def fork(self, params: SessionForkParams) -> SessionForkResult:
        options = params.agent_config or SessionOptions()
        self._worktrees.reject_input(options)
        source = self._adapters.get(params.source_session_id)
        if source is not None and source._closed:
            source = None
        context, derivation = await self._lifecycle_context(
            options, require_api_key=True
        )
        context, derivation, resolution = await self._pin_to_session_cwd(
            options, params.source_session_id, context, derivation, require_api_key=True
        )
        try:
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
                worktree_resolution=resolution,
            )
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise
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
        key = _MergedListingKey(
            cwd=params.cwd,
            cwds=tuple(params.cwds) if params.cwds is not None else None,
            root_session_id=params.root_session_id,
            parent_session_id=params.parent_session_id,
            pinned=params.pinned,
        )
        listing = self._merged_listing
        # A cursor continues a walk whose first page already swept both stores,
        # so rebuilding here would pay for the whole merge once per page. It
        # would also re-sort live data mid-walk: a session whose ``updated_at``
        # advances moves ahead of the cursor into the region already passed,
        # and the walk never returns it. Hold the first page's merge instead
        # and let the walk read a stable snapshot of it.
        if params.cursor is None or listing is None or listing.key != key:
            listing = await self._build_merged_listing(params, key)
            self._merged_listing = listing

        merged = listing.sessions
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
            continue_session_id=listing.continue_session_id,
        )

    async def _read_context(self, options: SessionOptions) -> UnifiedReadContext:
        """The single door every session read goes through."""
        if self._build_read_context is not None:
            context = await self._build_read_context(options)
            self._configure_read_host(context)
            return context

        session_context, _ = await self._lifecycle_context(
            options, require_api_key=False
        )
        return UnifiedReadContext(
            storage_root=session_context.storage_root,
            config_orchestrator=session_context.config_orchestrator,
            legacy_source_loader=session_context.legacy_source_loader,
            legacy_source_resolver=session_context.legacy_source_resolver,
        )

    def _configure_read_host(self, context: UnifiedReadContext) -> None:
        """Only the Host state reads reach: the store, and the legacy callables
        that ``rename`` needs via ``Host.session_cwd``.
        """
        self._configure_storage_root(context.storage_root)
        self._host.configure_legacy_source_loader(context.legacy_source_loader)
        self._host.configure_legacy_source_resolver(context.legacy_source_resolver)

    def _configure_storage_root(self, storage_root: str) -> None:
        """One store per process, so two cwds that disagree cannot both be open."""
        try:
            self._host.configure_storage(storage_root)
        except RuntimeError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                "This process already has a session open against a different "
                "session store. Directories whose session_logging settings "
                "disagree cannot share one app-server process.",
            ) from exc

    async def _build_merged_listing(
        self, params: SessionListParams, key: _MergedListingKey
    ) -> _MergedListing:
        requested = (
            params.cwds
            if params.cwds is not None
            else ([params.cwd] if params.cwd is not None else [])
        )
        requested_cwds = tuple(Path(raw).expanduser().resolve() for raw in requested)
        # A single managed worktree, asked for by its own path, is the one
        # shape the stores can narrow themselves. Everything else is swept and
        # filtered here: neither store answers a worktree-aware question, and
        # neither answers about several checkouts at once.
        # None: the stores narrow it. A tuple filters here, so an empty one
        # matches nothing rather than everything.
        pushed_down = (
            params.cwds is None
            and len(requested_cwds) <= 1
            and all(self._worktrees.is_managed(cwd) for cwd in requested_cwds)
        )
        retained_repositories: tuple[Path, ...] | None = (
            None if pushed_down else requested_cwds
        )
        options = SessionOptions(cwd=params.cwd or next(iter(requested), None))
        context = await self._read_context(options)

        # One store is a host round trip and the other a worker-thread
        # filesystem scan, so reading them in sequence would add the smaller
        # to the larger for nothing.
        (unified_items, continue_session_id), legacy_items = await asyncio.gather(
            self._sweep_unified_sessions(
                params, options, context.storage_root, retained_repositories
            ),
            self._list_legacy_sessions(params, context.config, retained_repositories),
        )

        # Merge and sort by (updated_at, session_id) descending. Session IDs are
        # UUIDs, so the (updated_at, id) key is unique across both stores.
        return _MergedListing(
            key=key,
            sessions=sorted(
                [*unified_items, *legacy_items],
                key=lambda s: (s.updated_at, s.id),
                reverse=True,
            ),
            continue_session_id=continue_session_id,
        )

    async def _sweep_unified_sessions(
        self,
        params: SessionListParams,
        options: SessionOptions,
        storage_root: str,
        retained_repositories: tuple[Path, ...] | None,
    ) -> tuple[list[PublicSession], str | None]:
        """Every unified session matching the request, and the continue pointer."""
        items: list[PublicSession] = []
        cursor: str | None = None
        continue_session_id: str | None = None
        while True:
            result = await _harness_call(
                self._host.list(
                    limit=_SESSION_LISTING_PAGE,
                    cursor=cursor,
                    cwd=(
                        None
                        if retained_repositories is not None
                        else _session_cwd(options)
                        if params.cwd is not None
                        else None
                    ),
                    root_session_id=params.root_session_id,
                    parent_session_id=params.parent_session_id,
                )
            )
            items.extend(
                await asyncio.to_thread(
                    _public_unified_sessions, storage_root, result.items
                )
            )
            if continue_session_id is None:
                continue_session_id = result.continue_session_id
            if result.next_cursor is None or not result.items:
                break
            cursor = result.next_cursor
        if retained_repositories is not None:
            items = [
                session
                for session in items
                if self._session_cwd_matches_any(session.cwd, retained_repositories)
            ]
            listed_ids = {session.id for session in items}
            if continue_session_id not in listed_ids:
                latest = max(
                    items,
                    key=lambda session: (session.updated_at, session.id),
                    default=None,
                )
                continue_session_id = latest.id if latest is not None else None
        if params.pinned is not None:
            # TODO: Move pin filtering into a source that can paginate the
            # filtered set. The Harness cannot read Vibe's pin sidecars, so the
            # current implementation must sweep every Harness page first. The
            # returned page is correct, but this filter is not pagination-
            # compatible and its cost grows with the whole saved catalogue.
            # After the sweep rather than inside the Harness page request: a
            # pin lives in Vibe's sidecar, which the Harness cannot read, so
            # there is nothing for it to narrow the page by.
            items = [
                session
                for session in items
                if (session.pinned_at is not None) is params.pinned
            ]
        return items, continue_session_id

    async def _list_legacy_sessions(
        self,
        params: SessionListParams,
        config: VibeConfigSchema,
        retained_repositories: tuple[Path, ...] | None,
    ) -> list[PublicSession]:
        """Every legacy session matching the request, in one filesystem read.

        A secondary source: if the read fails the picker degrades to
        unified-only rather than blocking the primary listing. Lineage filters
        (root/parent) are unified-only, because legacy sessions carry no
        ``root_session_id``.
        """
        if params.root_session_id is not None or params.parent_session_id is not None:
            return []
        try:
            # Projected on the worker thread too: an untitled session still
            # reads its transcript for a preview, which must not run on the
            # event loop.
            sessions = await asyncio.to_thread(
                _legacy_public_sessions,
                config,
                None if retained_repositories is not None else params.cwd,
            )
        except OSError:
            logger.debug("Legacy session listing failed; returning unified-only")
            return []
        if params.pinned is not None:
            sessions = [
                session
                for session in sessions
                if (session.pinned_at is not None) is params.pinned
            ]
        if retained_repositories is None:
            return sessions
        return [
            session
            for session in sessions
            if self._session_cwd_matches_any(session.cwd, retained_repositories)
        ]

    def _session_cwd_matches_any(
        self, session_cwd: str | None, requested_cwds: tuple[Path, ...]
    ) -> bool:
        return any(
            self._session_cwd_matches(session_cwd, requested)
            for requested in requested_cwds
        )

    def _session_cwd_matches(
        self, session_cwd: str | None, requested_cwd: Path
    ) -> bool:
        if session_cwd is None:
            return False
        cwd = Path(session_cwd).expanduser().resolve()
        if cwd == requested_cwd:
            return True
        mapping = self._worktrees.retained_repository_mapping(cwd)
        return (
            mapping is not None
            and requested_cwd.is_relative_to(mapping.root)
            and mapping.cwd.is_relative_to(requested_cwd)
        )

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        options = SessionOptions()
        # Unchanged from before the narrow read context existed. Moving this
        # to `_read_context` makes a blocked session read back a short history
        # after a process restart (local-code-replay / local-code-live-session
        # cover it). Which part of the fuller setup it depends on is not
        # established, so this keeps the behaviour that works.
        context, _ = await self._lifecycle_context(options, require_api_key=False)
        try:
            result = await _harness_call(self._host.read(_harness_read_params(params)))
            adapter = self._adapters.get(params.session_id)
            if adapter is not None and not adapter._closed:
                metadata = adapter._metadata
            else:
                metadata = await asyncio.to_thread(
                    _read_unified_session_metadata,
                    _unified_session_dir(
                        context.storage_root, result.snapshot.state.session.id
                    ),
                )
            pins = await _stored_session_pins(
                self._host, result.snapshot.state.session.id, context.agents
            )
            return _read_response(
                result.snapshot, result.cwd, metadata=metadata, pins=pins
            )
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

    async def read_config(self, params: ConfigReadParams) -> ConfigReadResponse:
        """The configuration a stored session runs, without resuming it.

        A client renders its pickers from this long before it attaches, so the
        answer is the session's rather than the host's: the same context a
        resume builds, put on the same pins.
        """
        if params.session_id is None:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, "A session id is required"
            )
        # The first build is what configures the Host's storage, so the stored
        # cwd is only knowable after it. A session kept elsewhere is answered
        # from a context rebuilt against its own, the way a resume is.
        options = SessionOptions(cwd=params.cwd)
        context, _ = await self._lifecycle_context(options, require_api_key=False)
        stored_cwd = await _harness_call(self._host.session_cwd(params.session_id))
        if stored_cwd is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {params.session_id}"
            )
        if stored_cwd != _session_cwd(options):
            context, _ = await self._lifecycle_context(
                _with_session_cwd(options, stored_cwd), require_api_key=False
            )
        await self._restore_session_pins(params.session_id, context)
        return await _config_read_response(
            context.config_orchestrator, context.harness_files
        )

    async def rename(
        self, params: SessionTitleUpdateParams
    ) -> SessionTitleUpdateResponse:
        context = await self._read_context(SessionOptions())
        snapshot = await _harness_call(
            self._host.rename(params.session_id, params.title)
        )
        title = snapshot.state.session.title
        if title is None:
            raise RuntimeError("The session title was not updated")
        adapter = self._adapters.get(params.session_id)
        if adapter is not None:
            await adapter._persist_session_metadata_best_effort()
        else:
            session_dir = _unified_session_dir(context.storage_root, params.session_id)
            try:
                cwd = await _harness_call(self._host.session_cwd(params.session_id))
                await _persist_unified_session_metadata(
                    session_dir,
                    snapshot.state.session,
                    cwd,
                    previous=await asyncio.to_thread(
                        _read_unified_session_metadata, session_dir
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist Unified session metadata after rename",
                    exc_info=exc,
                )
        return SessionTitleUpdateResponse(title=title, last_event_id=snapshot.watermark)

    async def pin(self, params: SessionPinParams) -> SessionPinResponse:
        """Record a pin against the Vibe-owned sidecar, not the Harness.

        The Harness has no notion of a pin, so unlike ``rename`` there is
        nothing to ask it for first: the sidecar is the whole of the write. An
        open session goes through its adapter so the in-memory metadata and the
        subscribers stay in step; a closed one is rewritten in place.
        """
        context = await self._read_context(SessionOptions())
        pinned_at = datetime.now(UTC) if params.pinned else None
        adapter = self._adapters.get(params.session_id)
        if adapter is not None and not adapter._closed:
            metadata = await adapter._persist_session_metadata(pinned_at=pinned_at)
            adapter._publish_pinned_at(metadata)
            return SessionPinResponse(pinned_at=_metadata_pinned_at_ms(metadata))

        session_dir = _unified_session_dir(context.storage_root, params.session_id)
        try:
            snapshot = await _harness_call(
                self._host.read(
                    HarnessSessionReadParams(
                        session_id=params.session_id, history_limit=1
                    )
                )
            )
        except SessionBackendError as exc:
            if exc.code is not ProtocolErrorCode.NOT_FOUND:
                raise
            try:
                legacy_metadata = await update_saved_session_pin(
                    params.session_id,
                    pinned_at.isoformat() if pinned_at is not None else None,
                    context.config_orchestrator.config.session_logging,
                )
            except ValueError as legacy_exc:
                if str(legacy_exc).startswith("Session not found:"):
                    raise exc from legacy_exc
                raise SessionBackendError(
                    ProtocolErrorCode.INVALID_PARAMS, str(legacy_exc)
                ) from legacy_exc
            stored = legacy_metadata.get("pinned_at")
            return SessionPinResponse(
                pinned_at=optional_time_ms(stored if isinstance(stored, str) else None)
            )
        metadata = await _persist_unified_session_metadata(
            session_dir,
            snapshot.snapshot.state.session,
            snapshot.cwd,
            previous=await asyncio.to_thread(
                _read_unified_session_metadata, session_dir
            ),
            pinned_at=pinned_at,
        )
        return SessionPinResponse(pinned_at=_metadata_pinned_at_ms(metadata))

    async def delete(self, params: SessionDeleteParams) -> EmptyResponse:
        context = await self._read_context(SessionOptions())
        try:
            await _harness_call(self._host.delete(params.session_id))
        except SessionBackendError as exc:
            if exc.code is not ProtocolErrorCode.NOT_FOUND:
                raise
            deleted = await delete_saved_session(
                params.session_id, context.config_orchestrator.config.session_logging
            )
            if not deleted:
                raise
            return EmptyResponse()
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
        if source._session.pin(SessionPin.ACTIVE_MODEL) is not None:
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
                cwd=source.cwd,
                ephemeral=True,
                hook_bindings=source._context.hooks.bindings,
                hook_handlers=source._context.hooks.handlers,
            )
        )
        backend = self._adapter(
            session,
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
        await asyncio.gather(
            *(
                adapter._persist_session_metadata_best_effort()
                for adapter in self._adapters.values()
            ),
            return_exceptions=True,
        )
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
            # After the Harness close, so nothing still holds the store open.
            if self._release_storage is not None:
                await asyncio.to_thread(self._release_storage)

    def _release_worktrees(self) -> None:
        for backend in self._adapters.values():
            if backend.cwd is None:
                continue
            self._worktrees.release(Path(backend.cwd), backend.session_id)

    def _adapter(
        self,
        session: UnifiedHarnessSessionBackend,
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
        # Mutable holder the attribution closure reads live; the adapter
        # updates it as snapshots reconcile, so the provider request's
        # metadata carries the current turn's message id.
        message_id_holder: list[str | None] = [None]
        adapter = UnifiedHarnessBackendAdapter(
            session,
            context,
            derivation,
            deferred_turns=session,
            host=self._host,
            services=self._services,
            telemetry_client=telemetry,
            session_replaced=self._session_replaced,
            rewrite_plugins=self._host.rewrite_session_plugins,
            scheduled_loops_enabled=scheduled_loops_enabled,
            launch_context=launch_context,
            user_plan=user_plan,
            # This session's own attribution. The adapter binds it into every
            # derivation it adopts, so a fork never reaches back into this one.
            completion_attribution=build_completion_attribution(
                telemetry,
                telemetry_launch_context,
                message_id_getter=lambda: message_id_holder[0],
            ),
            notify=self._notify_from_services,
        )
        adapter._message_id_holder = message_id_holder
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
        self,
        backend: UnifiedHarnessBackendAdapter,
        prepare: Awaitable[None],
        *,
        worktree_resolution: WorktreeResolution | None = None,
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
        self._occupy_worktree(backend, worktree_resolution)
        await backend._persist_session_metadata_best_effort()

    # Where start, resume and continue all end up, which is why the marking
    # happens here rather than three times over. Only after the open succeeded:
    # a session that failed to open is not standing anywhere.
    def _occupy_worktree(
        self,
        backend: UnifiedHarnessBackendAdapter,
        resolution: WorktreeResolution | None,
    ) -> None:
        if backend.cwd is None:
            if resolution is not None and resolution.pending_hold is not None:
                resolution.pending_hold.release()
            return
        cwd = Path(backend.cwd)
        self._worktrees.hold(
            cwd,
            backend.session_id,
            None if resolution is None else resolution.pending_hold,
        )

    async def _pin_to_session_cwd(
        self,
        options: SessionOptions,
        session_id: str,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        require_api_key: bool,
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation, WorktreeResolution]:
        """Rebuild the context against an existing session's stored cwd, if it differs.

        The caller must have already built ``context`` once (which configures the Host's
        storage/legacy resolver — what ``session_cwd`` needs). If the session was created
        in a different cwd than the caller's, rebuild so hooks are discovered and compiled
        against the session's own cwd; binding ids embed it, so a resume/fork otherwise
        skips every persisted hook or binds the wrong project's hooks (§7 as-built).
        """
        stored_cwd = await _harness_call(self._host.session_cwd(session_id))
        restored = False
        if stored_cwd is not None:
            restored = await self._worktrees.restore(Path(stored_cwd))
        pinned_options = _with_session_cwd(options, stored_cwd)
        resolution = await self._worktrees.resolve_for_start(pinned_options)
        try:
            if stored_cwd is not None and (
                restored or stored_cwd != _session_cwd(options)
            ):
                if options.trust_workspace and stored_cwd != _session_cwd(options):
                    # The first build recorded an ephemeral --trust grant for the caller cwd on
                    # the process-wide trust store. Its ancestor walk would still trust the pinned
                    # (often descendant) session cwd, so revoke that one grant before rebuilding.
                    context.harness_files.trust_store.revoke_session_trust(
                        Path(_session_cwd(options))
                    )
                context, derivation = await self._lifecycle_context(
                    resolution.options, require_api_key=require_api_key
                )
            rederive = await self._restore_session_pins(session_id, context)
            if rederive:
                derivation = await asyncio.to_thread(
                    context.derive, UnifiedSessionSettings()
                )
                self._host.configure_runtime(
                    derivation.core_config, adapter_config=derivation.adapter_config
                )
            return context, derivation, resolution
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise

    async def _restore_session_pins(
        self, session_id: str, context: UnifiedSessionContext
    ) -> bool:
        """Put a context on the choices a session was left with.

        Every stored pin is a user choice the session reopens with. Storage and
        lookup are generic (see ``SessionPin``); only applying one is per-pin
        policy. Declaration order is restore order: the level applies to
        whichever model the model and agent pins leave active.
        """
        restored = False
        for pin in SessionPin:
            value = await _harness_call(self._host.session_pin(session_id, pin))
            applied = value is not None and await self._apply_restored_pin(
                pin, context, value
            )
            logger.debug(
                "Session pin restore session=%s pin=%s stored=%r applied=%s",
                session_id,
                pin.value,
                value,
                applied,
            )
            restored = restored or applied
        return restored

    async def _apply_restored_pin(
        self, pin: SessionPin, context: UnifiedSessionContext, value: str
    ) -> bool:
        """Apply one restored pin to a resumed, continued, or forked context.

        Returns whether the derivation must be rebuilt. A removed or excluded
        value becomes unpinned and follows the current default. This is the one
        place a new pin needs per-pin restore behavior.
        """
        match pin:
            case SessionPin.ACTIVE_MODEL:
                failures = await set_session_active_model_override(
                    context.config_orchestrator,
                    value,
                    reason="restore session active model",
                )
                if failures:
                    raise SessionBackendError(
                        ProtocolErrorCode.INTERNAL_ERROR,
                        f"Failed to restore session active model: {failures[0]}",
                    )
                return True
            case SessionPin.AGENT_NAME:
                if value == context.agents.active_profile.name:
                    return False
                try:
                    context.agents.switch_profile(value)
                except ValueError:
                    logger.debug(
                        "Stored session running mode %r is unavailable; keeping the default",
                        value,
                    )
                    return False
                return True
            case SessionPin.REASONING_EFFORT:
                if not value:
                    return False
                failures = await set_session_reasoning_effort_override(
                    context.config_orchestrator,
                    value,
                    reason="restore session reasoning effort",
                )
                if failures:
                    raise SessionBackendError(
                        ProtocolErrorCode.INTERNAL_ERROR,
                        f"Failed to restore session reasoning effort: {failures[0]}",
                    )
                return True
            case _:
                assert_never(pin)

    def _entrypoint(self) -> AgentEntrypoint:
        if self._services is None:
            return "cli"
        return self._services.client_info().entrypoint

    async def _lifecycle_context(
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
        self._configure_storage_root(context.storage_root)
        self._host.configure_legacy_source_loader(context.legacy_source_loader)
        self._host.configure_legacy_source_resolver(context.legacy_source_resolver)
        self._host.configure_runtime(
            derivation.core_config, adapter_config=derivation.adapter_config
        )
        self._host.configure_plugins(context.plugin_provider, context.requested_plugins)
        # Registered so the resolver reads `vibe.todo` under the name its permission
        # is configured with; the modes are per tool, because only the scratchpad is
        # unconditionally allowed (see VIBE_PROVIDED_TOOL_MODES).
        context.provided_names.register(VIBE_TOOL_GROUP, VIBE_PROVIDED_TOOL_NAMES)
        self._host.configure_provided_tool_executor(
            [VIBE_TOOL_GROUP],
            context.vibe_tools.executor_factory(
                context.storage_root,
                # Read per call, not captured: `/config` can raise or lower the cap
                # inside a session's life.
                max_todos=lambda: resolved_max_todos(
                    context.config_orchestrator.config.tools.get(TODO_TOOL_NAME)
                ),
            ),
            mode=VIBE_PROVIDED_TOOL_MODES,
        )
        # The harness Rust type does not carry ``owner``, so the adapter infers
        # it from the names the merged catalog actually attributed to plugins.
        # Built here from the start-time catalog and updated on every
        # ``reconfigure_mcp`` and ``plugin/reload``, so a name a configured
        # server won never reads as plugin-owned and new plugin names do.
        mcp_authorization_adapter = _HarnessMCPAuthorizationProviderAdapter(
            context.mcp_authorization_provider
        )
        mcp_authorization_adapter.update_plugin_server_names(context.mcp_catalog)
        context = replace(context, mcp_authorization_adapter=mcp_authorization_adapter)
        self._host.configure_mcp(
            _harness_mcp_catalog(
                context.mcp_catalog,
                _mcp_tool_filter(context.config_orchestrator.config),
            ),
            mcp_authorization_adapter,
            cache_root=context.mcp_cache_root,
            http_transport_policy=HarnessMCPHTTPTransportPolicy(
                enable_system_trust_store=context.mcp_enable_system_trust_store
            ),
        )
        self._configure_connectors(context, connector_catalog, connector_selection)
        return context, derivation

    def _configure_connectors(
        self,
        context: UnifiedSessionContext,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
    ) -> None:
        self._host.configure_connectors(
            _harness_connector_catalog(catalog),
            _harness_connector_selection(selection),
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

    def _project_mcp(self, snapshot: HarnessMCPRouteSnapshot) -> SessionMCPState:
        self._context.provided_names.register("mcp", _published_mcp_names(snapshot))
        return _session_mcp_state(snapshot, self.mcp_config_orchestrator)

    async def read_mcp(self) -> SessionMCPState:
        return self._project_mcp(await _harness_call(self._session.read_mcp()))

    async def reconfigure_mcp(
        self, configuration: ResolvedMCPCatalog, *, force_remote_discovery: bool
    ) -> SessionMCPState:
        from vibe.app_server._runtime import merge_plugin_mcp_into_catalog

        configuration = merge_plugin_mcp_into_catalog(
            configuration,
            self._context.plugin_mcp,
            authentication=self._context.mcp_authentication,
        )
        self._context.mcp_authorization_adapter.update_plugin_server_names(
            configuration
        )
        snapshot = await _harness_call(
            self._session.reconfigure_mcp(
                _harness_mcp_catalog(
                    configuration, _mcp_tool_filter(self.mcp_config_orchestrator.config)
                ),
                force_remote_discovery=force_remote_discovery,
            )
        )
        return self._project_mcp(snapshot)

    async def authorization_changed(
        self, *, name: str, descriptor_revision: str
    ) -> SessionMCPState:
        snapshot = await _harness_call(
            self._session.authorization_changed(
                name=name, descriptor_revision=descriptor_revision
            )
        )
        return self._project_mcp(snapshot)

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
        return self._project_mcp(snapshot)

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

    def _project_connectors(
        self, snapshot: HarnessConnectorRouteSnapshot
    ) -> SessionConnectorState:
        self._context.provided_names.register(
            "connector", _published_connector_names(snapshot)
        )
        return _session_connector_state(snapshot)

    async def read_connectors(self) -> SessionConnectorState:
        return self._project_connectors(
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
        state = self._project_connectors(snapshot)
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
        state = self._project_connectors(snapshot)
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
                display_name=source.display_name,
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


class _UnifiedSessionPersistence:
    _session: UnifiedHarnessSessionBackend
    _context: UnifiedSessionContext

    def _persists_to_disk(self) -> bool:
        if not self._context.session_logging_enabled:
            return False
        return not bool(getattr(self._session, "ephemeral", True))


class _UnifiedSessionMetadataAdapter(_UnifiedSessionPersistence):
    """Maintains Vibe-owned sidecar metadata for Unified Harness sessions.

    The Harness remains authoritative for execution state and public session
    fields it owns. This sidecar exists for Vibe-owned metadata that the Harness
    deliberately does not know about, currently ``bumped_at``, while preserving a
    legacy-shaped ``meta.json`` for local filesystem tooling.
    """

    _storage_root: str
    _metadata: SessionMetadata | None
    _metadata_lock: asyncio.Lock

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def cwd(self) -> str | None:
        return self._session.cwd

    async def _persist_session_metadata_after_promotion(
        self, *, was_ephemeral: bool
    ) -> None:
        if not was_ephemeral or not self._persists_to_disk():
            return
        try:
            await self._persist_session_metadata()
        except Exception as exc:
            logger.warning(
                "Failed to persist Unified session metadata after session promotion",
                exc_info=exc,
            )
            self._session.publish_notice(
                "The session was saved, but its metadata could not be saved."
            )

    async def _persist_session_metadata_best_effort(self) -> None:
        if not self._persists_to_disk():
            return
        try:
            await self._persist_session_metadata()
        except Exception as exc:
            logger.warning("Failed to persist Unified session metadata", exc_info=exc)

    async def _persist_session_metadata(
        self,
        *,
        bumped_at: datetime | None = None,
        pinned_at: datetime | None | _Unchanged = _UNCHANGED,
    ) -> SessionMetadata:
        async with self._metadata_lock:
            return await self._persist_session_metadata_locked(
                bumped_at=bumped_at, pinned_at=pinned_at
            )

    async def _persist_session_metadata_locked(
        self,
        *,
        bumped_at: datetime | None,
        pinned_at: datetime | None | _Unchanged = _UNCHANGED,
    ) -> SessionMetadata:
        result = await _harness_call(
            self._session.read(
                HarnessSessionReadParams(session_id=self.session_id, history_limit=1)
            )
        )
        metadata = _build_unified_session_metadata(
            result.snapshot.state.session,
            self.cwd,
            previous=self._metadata,
            bumped_at=bumped_at,
            pinned_at=pinned_at,
        )
        if not self._persists_to_disk():
            self._metadata = metadata
            return metadata
        session_dir = _unified_session_dir(self._storage_root, self.session_id)
        session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        await SessionLogger.persist_metadata(
            metadata.model_dump(mode="json"), session_dir
        )
        self._metadata = metadata
        return metadata

    def _publish_pinned_at(self, metadata: SessionMetadata) -> None:
        """Announce the new pin on this session's own event stream.

        Nothing else will: a pin changes no turn and no Harness state, so an
        already-subscribed client would hold the old value until it next
        re-read the session.
        """
        self._publish_session_metadata(
            _SessionMetadataProjection(
                session_id=self.session_id,
                patch=(
                    JsonPatchOperation(
                        op="replace",
                        path="/pinnedAt",
                        value=_metadata_pinned_at_ms(metadata),
                    ),
                ),
            )
        )

    def _publish_session_metadata(
        self, metadata: _SessionMetadataProjection
    ) -> None: ...


class _UnifiedScheduledLoopsAdapter(_UnifiedSessionPersistence):
    _storage_root: str
    _scheduled_loops: UnifiedScheduledLoops
    _scheduled_loops_enabled: bool
    _scheduled_loops_task: asyncio.Task[None] | None
    _pending_startup_notices: list[str]
    _defer_event_flush: bool

    @property
    def session_id(self) -> str:
        return self._session.session_id

    _setup_abandoned = False

    @property
    def cwd(self) -> str | None:
        return self._session.cwd

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
        if not was_ephemeral or not self._persists_to_disk():
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
                    loop = await self._scheduled_loops.create(
                        params.interval, params.prompt
                    )
                    return LoopsCreateResponse(loop=_project_scheduled_loop(loop))
                case "loops/delete":
                    params = validate_wire(LoopsDeleteParams, raw_params)
                    self._require_session(params.session_id)
                    loop = await self._scheduled_loops.delete(params.loop_id)
                    return LoopsDeleteResponse(loop=_project_scheduled_loop(loop))
                case "loops/clear":
                    params = validate_wire(LoopsClearParams, raw_params)
                    self._require_session(params.session_id)
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

    async def _prepared_turn_params[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT, *, inject_skill: bool
    ) -> ParamsT: ...


# The runtime reads these fields per tool call to gate approval: ``bypass_approval``
# plus ``tool_modes``/``provided_tool_mode`` select allow/ask/deny/classify, and
# ``permission_resolver`` refines an ``ask`` builtin (see the SDK's _local_actions
# ``execute`` / ``_gate_tool_call``). A mid-turn agent switch must graft exactly
# these onto the config the turn is already running; everything else in a derivation
# is bound to Core turn-start state (model, skills, compaction) and must not move
# mid-turn. Keep this list beside ``graft_live_approval_policy`` so a newly added
# per-call approval field can never be read live but silently dropped on a switch.
_LIVE_APPROVAL_POLICY_FIELDS: tuple[str, ...] = (
    "bypass_approval",
    "tool_modes",
    "provided_tool_mode",
    # The classifier's Mistral route is read live on every classify call, so a
    # mid-turn switch into smart approve must carry it too; otherwise a non-Mistral
    # session keeps a stale (None) route and every gated call comes back
    # unverifiable instead of auto-approving.
    "classifier_provider",
    "permission_resolver",
)


def graft_live_approval_policy(
    running: LocalRuntimeAdapterConfig, derived: LocalRuntimeAdapterConfig
) -> LocalRuntimeAdapterConfig:
    """Carry only the per-call approval surface from ``derived`` onto ``running``.

    A mode switch mid-turn changes how the running turn's next tool call is gated
    without disturbing the model, skills, or compaction the Core bound at turn
    start. Centralising the field set is what stops the mid-turn switch from
    updating some approval fields while leaving others (e.g. ``provided_tool_mode``
    for MCP/connector tools) stale.
    """
    return replace(
        running,
        **{name: getattr(derived, name) for name in _LIVE_APPROVAL_POLICY_FIELDS},
    )


@dataclass(frozen=True, slots=True)
class _ChildIdentity:
    name: str
    agent_type: str
    spawned_at: int


class UnifiedHarnessBackendAdapter(  # noqa: PLR0904 - implements app-server session operations
    _UnifiedHarnessMCPAdapter,
    _UnifiedHarnessConnectorAdapter,
    _UnifiedSessionMetadataAdapter,
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

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        session: UnifiedHarnessSessionBackend,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        host: UnifiedHarnessSessionBackendHost | None = None,
        services: SessionBackendServices | None = None,
        telemetry_client: TelemetryClient | None = None,
        session_replaced: SessionReplacementCallback | None = None,
        rewrite_plugins: PluginRewrite | None = None,
        deferred_turns: DeferredTurnStartCapability | None = None,
        scheduled_loops_enabled: bool = True,
        launch_context: LaunchContext | None = None,
        user_plan: str | None = None,
        completion_attribution: CompletionAttributionSource | None = None,
        notify: Callable[[str, ProtocolModel], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self._deferred_turns = deferred_turns
        session.configure_turn_settlement(self._settle_configuration)
        self._closed = False
        self._completion_attribution = completion_attribution
        self._host = host
        self._notify = notify
        self._services = services
        self._turns_deferred: asyncio.Event | None = None
        self._deferred_setup_task: asyncio.Task[None] | None = None
        self._deferred_setup_result: WorktreeResolution | None = None
        self._deferred_turn_claimed = False
        self._worktree_progress: WorktreeProgress | None = None
        self._setup_abandoned = False
        self._context = context
        self._settings = UnifiedSessionSettings()
        self._runtime = derivation.runtime
        self._adapter_config = derivation.adapter_config
        self._model = derivation.adapter_config.model
        self._skills = derivation.skill_payloads
        self._bind_completion_attribution(derivation)
        self._storage_root = context.storage_root
        self._metadata = _read_unified_session_metadata(self._session_dir())
        self._metadata_lock = asyncio.Lock()
        # These Harness receipts do not distinguish success from replay. Track
        # successful identities only for the Vibe-owned recency side effect.
        self._accepted_callback_activity: set[tuple[str, str]] = set()
        self._accepted_queued_steer_activity: set[tuple[str, str]] = set()
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
        self._child_identities: dict[str, _ChildIdentity] = {}
        self._stopped_children: dict[str, int] = {}
        self._observed_stop_effect_ids: set[str] = set()
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
        # Turn-to-message attribution for tool-call telemetry. The user
        # entry's id is the public message id, and effects carry only their
        # turn id, so the adapter learns the mapping as snapshots reconcile
        # and attributes each terminal effect to the user message that
        # started its turn.
        self._turn_client_message_ids: dict[str, str] = {}
        self._last_client_message_id: str | None = None
        # Mutable holder the attribution closure reads live; set by the host
        # adapter factory so the provider request metadata carries the current
        # turn's message id.
        self._message_id_holder: list[str | None] = [None]
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
        self._image_describer = SessionImageDescriber(
            lambda: self._context.config_orchestrator.config,
            notice=lambda message: self._session.publish_notice(message),
            session_id=lambda: self._session.session_id,
            record_event=self._telemetry.send_telemetry_event,
        )
        self._session_replaced = session_replaced
        self._teleport_active: str | None = None
        self._vibe_code: VibeCodeController | None = None
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
        self._skills_controller = SkillsController(
            _UnifiedSkillsHost(
                context,
                runtime=lambda: self._runtime,
                require_idle=self._require_idle,
                require_session=self._require_session,
                refresh=self._refresh_after_skill_change,
            )
        )
        self._experiments_task: asyncio.Task[None] | None = None
        self._connector_resolve_task: asyncio.Task[None] | None = None
        self._deferred = DeferredConfiguration(
            derive=self._derive_configuration,
            push=self._push_configuration,
            adopt=self._adopt_derivation,
            turn_running=lambda: self._session.active_turn_id is not None,
        )
        self._defer_event_flush = False
        self._skip_deferred_flush_once = False
        self._release_deferred_events: Callable[[], None] | None = None
        self._translated_state: PublicSessionState | None = None
        self._pretranslated_events: dict[
            int, tuple[tuple[SessionBackendEvent, ...], PublicSessionState]
        ] = {}
        self._scheduled_loops = UnifiedScheduledLoops(
            self._session_dir() / "scheduled-loops.json",
            persistent=self._persists_to_disk,
        )
        self._scheduled_loops_enabled = scheduled_loops_enabled
        self._scheduled_loops_task: asyncio.Task[None] | None = None
        self._pending_startup_notices: list[str] = []
        self._host_shell = _HostShell(ShellController(self._cwd_path()))

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

    @property
    def config(self) -> VibeConfigSchema:
        return self._context.config_orchestrator.config

    @property
    def launch_context(self) -> LaunchContext | None:
        return self._launch_context

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
        return validate_backend_wire(
            PluginInfo, info.model_dump(mode="json", by_alias=True)
        )

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
        # ``rescan`` rebinds plugin MCP servers in ``PluginMCPCatalog`` and the
        # auth service, but neither tells the harness MCPRuntime. Without a
        # reconfigure the runtime keeps the session-start catalog and the
        # frozen authorization name set, so new plugin MCP tools never appear
        # and stale plugin names keep routing ``owner`` wrong.
        if self._context.mcp_catalog_service is not None:
            configuration = await self._context.mcp_catalog_service.resolve_catalog(
                self.mcp_config_orchestrator
            )
            await self.reconfigure_mcp(configuration, force_remote_discovery=False)
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

    def _require_readable_session(self, session_id: str) -> None:
        if (
            session_id == self.session_id
            or self.references_child(session_id)
            or session_id in self._child_states
        ):
            return
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

    async def dispatch_extension(  # noqa: PLR0912 - one branch per protocol method
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
            case "session/shellCommand":
                return await self._dispatch_shell_command(raw_params)
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
            case _ if method.startswith("skills/"):
                return await self._skills_controller.dispatch(method, raw_params)
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
            case "workspace/trust/decision":
                return await self._workspace_trust_decision(raw_params)
            case _ if method.startswith("vibeCode/"):
                return await self._dispatch_vibe_code(method, raw_params)
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _workspace_trust_decision(
        self, raw_params: dict[str, Any]
    ) -> DispatchResult:
        """Apply a workspace trust decision to this session's working directory.

        Unlike the session-less host route, which any peer can aim at any
        directory, this session-scoped route pins the decision to the
        session's working directory; a grant reloads config and re-derives
        the runtime.
        """
        params = validate_wire(WorkspaceTrustDecisionParams, raw_params)
        try:
            session_id = require_trust_session_id(params.session_id)
        except WorkspaceTrustError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        self._require_session(session_id)
        grant = is_trust_grant(params.decision)
        if not self._session.cwd:
            # The harness's legacy-migration fallback can persist an empty
            # cwd, which would otherwise resolve to the server process cwd.
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                "The session has no working directory to trust",
            )
        try:
            cwd = resolve_session_trust_target(self._session.cwd, params.cwd)
        except WorkspaceTrustError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        if grant:
            self._require_idle()
        try:
            response = await asyncio.to_thread(
                decide_workspace_trust,
                cwd,
                params.decision,
                self._context.harness_files.trust_store,
            )
        except WorkspaceTrustError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        if grant:
            await self._context.config_orchestrator.reload(
                preflight=self._preflight_config
            )
            await self._apply_derivation()
        return DispatchResult(response, runtime_updated=grant)

    async def _dispatch_shell_command(
        self, raw_params: dict[str, Any]
    ) -> DispatchResult:
        """Run a manual `!<command>`, stream it, and hand its output to the model.

        Two things have to happen and the Harness owns neither. The model reads
        the result through the Harness's own ``inject_context``, which is where
        the legacy backend puts it too, so the block reaching the model is the
        same one on both backends. The terminal reads the result off a ``shell``
        effect entry, and Core owns history in a Unified session -- a Host
        writing a manual command into it would author a turn out of a user's
        aside. So the entry is projected here and merged into this session's
        event stream, exactly as ``TurnController`` does for the legacy backend.

        The one deliberate difference: an effect the Harness never stored cannot
        come back on resume, where the legacy backend replays it from the
        ``ManualShellContext`` it persisted alongside the message.
        """
        params = validate_wire(SessionShellCommandParams, raw_params)
        self._require_session(params.session_id)
        if params.action == "interrupt":
            if params.operation_id is None:
                raise SessionBackendError(
                    ProtocolErrorCode.INVALID_PARAMS,
                    "operation_id is required for action='interrupt'",
                )
            await self._host_shell.controller.interrupt(params.operation_id)
            return DispatchResult(
                SessionShellCommandResponse(last_event_id=self._event_id)
            )

        command = params.command or ""
        if not command.strip():
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, "Shell command cannot be empty"
            )
        operation_id = params.operation_id or str(uuid4())
        run_params = ShellRunParams(
            session_id=params.session_id,
            operation_id=operation_id,
            command=command,
            timeout_seconds=params.timeout_seconds or 30.0,
            cwd=self._workspace_cwd(params.cwd),
        )
        result = await self._run_manual_shell(run_params)

        limit = manual_shell_output_limit(self._context.config_orchestrator.config)
        injected = await self.inject_context(
            ContextInjectParams(
                session_id=params.session_id,
                input=[
                    TextContentBlock(
                        text=manual_shell_context(result, max_output_bytes=limit)
                    )
                ],
            )
        )
        return DispatchResult(
            SessionShellCommandResponse(last_event_id=self._event_id),
            after_response=injected.after_response,
        )

    async def _run_manual_shell(self, params: ShellRunParams) -> ShellRunResponse:
        """Run the command behind a ``shell`` effect entry the terminal can draw."""
        effect = _ManualShellEffect(
            EventProjector(params.session_id, None), params.operation_id
        )
        started_at = time.monotonic()

        def elapsed_ms() -> float:
            return (time.monotonic() - started_at) * 1000

        async def observe_output(chunk: str) -> None:
            self._publish_local_event(effect.append_output(chunk))

        # A conflicting operation id never opened an entry, so it must not close
        # one either -- it is the one failure that leaves the timeline untouched.
        if self._host_shell.controller.is_running(params.operation_id):
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                f"Shell operation already exists: {params.operation_id}",
            )

        self._publish_local_event(effect.start(shell_effect_detail(params.command)))
        try:
            result = await self._host_shell.controller.run(params, observe_output)
        except asyncio.CancelledError:
            self._publish_local_event(
                effect.complete(
                    shell_effect_cancelled(
                        output_text=effect.output_text, duration_ms=elapsed_ms()
                    )
                )
            )
            raise
        except Exception as exc:
            self._publish_local_event(
                effect.complete(
                    shell_effect_error(
                        exc, output_text=effect.output_text, duration_ms=elapsed_ms()
                    )
                )
            )
            if isinstance(exc, ShellConflictError):
                raise SessionBackendError(ProtocolErrorCode.CONFLICT, str(exc)) from exc
            raise
        self._publish_local_event(
            effect.complete(
                shell_effect_state(
                    result, output_text=effect.output_text, duration_ms=elapsed_ms()
                )
            )
        )
        return result

    def _publish_local_event(self, event: AppServerEvent) -> None:
        """Queue an event the Host minted for this session's own event stream.

        Dropped when nothing is subscribed: the queue is drained by the stream,
        so buffering without one would stall the next ``flush_events`` on an
        event no Client is waiting for.
        """
        if not self._events_subscribed:
            return
        self._event_id += 1
        self._host_shell.pending += 1
        self._host_shell.queue.put_nowait(_event_envelope(event, self._event_id))

    def _workspace_cwd(self, requested_cwd: str | None) -> str:
        try:
            return resolve_workspace_cwd(self._cwd_path(), requested_cwd)
        except RequestFailure as exc:
            raise SessionBackendError(exc.code, str(exc), exc.data) from exc

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
                self._require_readable_session(history_params.session_id)
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
                self._require_readable_session(turns_params.session_id)
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
        self._require_readable_session(params.session_id)
        if params.session_id != self.session_id:
            return await self._read_child_response(params)
        result = await self._session.read(_harness_read_params(params))
        return self._read_response(result.snapshot)

    async def _accepted_user_activity_result[ResponseT: ProtocolModel](
        self,
        response: ResponseT,
        *,
        after_response: Callable[[], None] | None = None,
        on_response_abandoned: Callable[[], None] | None = None,
        runtime_updated: bool = False,
        accepted: bool = True,
    ) -> SessionBackendResult[ResponseT]:
        metadata = (
            await self._persist_accepted_user_activity_metadata() if accepted else None
        )
        return SessionBackendResult(
            response=response,
            after_response=self._after_session_metadata_response(
                metadata, after_response
            ),
            on_response_abandoned=on_response_abandoned,
            runtime_updated=runtime_updated,
        )

    async def _persist_accepted_user_activity_metadata(
        self,
    ) -> _SessionMetadataProjection | None:
        try:
            metadata = await self._persist_session_metadata(bumped_at=datetime.now(UTC))
        except Exception:
            logger.exception(
                "Failed to persist Unified session bumped_at for session_id=%s",
                self.session_id,
            )
            return None
        return self._accepted_user_activity_metadata_projection(metadata)

    def _accepted_user_activity_metadata_projection(
        self, metadata: SessionMetadata
    ) -> _SessionMetadataProjection | None:
        bumped_at = _metadata_bumped_at_ms(metadata)
        if bumped_at is None:
            return None
        return _SessionMetadataProjection(
            session_id=self.session_id,
            patch=(
                JsonPatchOperation(op="replace", path="/bumpedAt", value=bumped_at),
            ),
        )

    def _after_session_metadata_response(
        self,
        metadata: _SessionMetadataProjection | None,
        after_response: Callable[[], None] | None,
    ) -> Callable[[], None] | None:
        if metadata is None:
            return after_response

        def publish_metadata() -> None:
            try:
                self._publish_session_metadata(metadata)
            except Exception:
                logger.exception(
                    "Failed to publish Unified session metadata update "
                    "for session_id=%s",
                    metadata.session_id,
                )
            finally:
                if after_response is not None:
                    after_response()

        return publish_metadata

    def _publish_session_metadata(self, metadata: _SessionMetadataProjection) -> None:
        state = self._translated_state
        if state is None or state.session.id != metadata.session_id:
            return
        previous = state.session
        session = PublicSession.model_validate(
            apply_json_patch(
                previous.model_dump(mode="json", by_alias=True), list(metadata.patch)
            )
        )
        if session == previous:
            return
        self._publish_local_event(
            SessionUpdated(
                previous=previous, session=session, patch=list(metadata.patch)
            )
        )
        self._translated_state = state.model_copy(
            update={"event_id": self._event_id, "session": session}, deep=True
        )

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
        # Host-minted events go out first and unconditionally: a Client that
        # asked to wait for incoming notifications waits only for the ones that
        # preceded the response, so a `!` command answering ahead of its own
        # effect entry would leave the terminal with nothing to draw.
        await self._flush_host_events()
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

    async def _flush_host_events(self) -> None:
        async with self._events_condition:
            while self._events_subscribed and self._host_shell.pending > 0:
                await self._events_condition.wait()

    def guard_request(self) -> None:
        self._session.guard_request()

    async def switch_agent(
        self, params: AgentSwitchParams
    ) -> SessionBackendResult[RuntimeMutationResponse]:
        self._require_session(params.session_id)
        # Pending worktree setup ends by adopting a context built from the launch
        # options, which discards a profile switched before it lands.
        await self._await_deferred_setup()
        # Entering or leaving smart-approve flips gated tools between "classify" and
        # "ask"/"allow" in the adapter config, which _apply_derivation pushes to the
        # live session -- no Core hook rebinding, so the switch is safe mid-session.
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
        # Pin the running mode so resume/continue/fork put the session back on it.
        await _harness_call(
            self._session.persist_pin(SessionPin.AGENT_NAME, params.agent_name)
        )
        # A profile can repoint the model, and the level belongs to that model.
        await self._persist_session_reasoning_effort()
        return SessionBackendResult(
            response=RuntimeMutationResponse(
                runtime=self._runtime, status=self._mutation_status()
            )
        )

    def _mutation_status(self) -> RuntimeMutationStatus:
        """Whether the session is running the configuration, or holding it."""
        return (
            RuntimeMutationStatus.PENDING
            if self._deferred.parked
            else RuntimeMutationStatus.APPLIED
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
        return await self._write_config(params)

    async def write_model_config(
        self, params: ModelConfigWriteParams
    ) -> SessionBackendResult[ConfigWriteResponse]:
        """Pick the model, and how hard it thinks, without waiting for idle.

        The Core takes its settings when a turn starts, so this is the one
        configuration a running turn cannot read however early it is written.
        Refusing it would lose a choice the user has already made, so it is
        persisted now and applied at the next turn boundary. Every other setting
        keeps ``config/write``'s idle-only contract.
        """
        self._require_session(params.session_id)
        try:
            ops = model_config_write_ops(
                self._context.config_orchestrator.config,
                model_alias=params.model_alias,
                reasoning_effort=params.reasoning_effort,
            )
        except ValueError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        # In the same batch, so one derivation serves both, and against the
        # model the write leaves active rather than the one it found.
        orchestrator = self._context.config_orchestrator
        written_model = model_after_write(orchestrator.config, params.model_alias)
        if params.reasoning_effort is not None and model_thinking_is_session_writable(
            orchestrator, written_model
        ):
            ops = [
                *ops,
                ConfigWriteOpWire(
                    op="set",
                    path=model_thinking_path(written_model),
                    value=params.reasoning_effort,
                    target_layer=OverridesLayer.NAME,
                ),
            ]
        result = await self._write_config(
            ConfigWriteParams(
                session_id=params.session_id,
                ops=ops,
                reason="model configuration",
                reload_runtime=True,
            )
        )
        if result.response.rejected or result.response.failures:
            return result
        if params.model_alias is not None:
            await self._persist_session_active_model(params.model_alias)
        await self._persist_session_reasoning_effort()
        return result

    async def _write_config(
        self, params: ConfigWriteParams
    ) -> SessionBackendResult[ConfigWriteResponse]:
        self._require_no_session_rewrite()
        apply_derivation = self._apply_written_config
        orchestrator = self._context.config_orchestrator
        session_model_pinned = self._session.pin(SessionPin.ACTIVE_MODEL) is not None
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
            await apply_derivation()
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
                await apply_derivation()
                return SessionBackendResult(
                    response=ConfigWriteResponse(
                        runtime=self._runtime,
                        failures=[str(failure) for failure in failures],
                    )
                )
        await apply_derivation()
        return SessionBackendResult(
            response=ConfigWriteResponse(
                runtime=self._runtime, status=self._mutation_status()
            )
        )

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
        return await _config_read_response(
            self._context.config_orchestrator, self._context.harness_files
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

    def _require_no_session_rewrite(self) -> None:
        """Reject a configuration mutation aimed at a session being rewritten.

        Compaction rewrites the history a derivation is computed against, and a
        teleport copies the session as it stands. Both bar even a pick a running
        turn would only park: there is no later boundary to land it on that the
        rewrite has not already read past.
        """
        if self._defer_event_flush:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "Context compaction is already running"
            )
        if self._teleport_active is not None:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                f"A teleport is already running: {self._teleport_active}",
                {"teleportId": self._teleport_active},
            )

    def _require_idle(self) -> None:
        """Reject a configuration mutation aimed at a session mid-turn.

        The Rust Core reads its settings when the turn starts, so applying a new
        derivation under a running turn would either be silently ignored or swap
        the provider between two iterations of the same turn.
        """
        self._require_no_session_rewrite()
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

    async def _apply_derivation(self, *, allow_reserved_turn: bool = False) -> None:
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
        if allow_reserved_turn and self._deferred_turns is not None:
            await self._deferred.apply_through(self._push_into_reserved_turn)
            return
        await self._deferred.apply()

    async def _push_into_reserved_turn(
        self, derivation: UnifiedRuntimeDerivation
    ) -> None:
        """Push into a session holding a turn it has not started.

        A deferred turn is accepted before its workspace exists, and the Core
        reads its settings when a turn starts, so a reserved one is still
        between turns as far as the configuration goes. Parking here would
        leave that turn to run on the configuration its worktree replaced.
        """
        await self._session.apply_runtime_configuration(
            derivation.core_config.settings,
            derivation.adapter_config,
            derivation.core_config.capabilities,
            system_instructions=derivation.core_config.system_instructions,
            allow_reserved_turn=True,
        )

    def defer_turns(
        self,
        work: Awaitable[WorktreeResolution | None],
        *,
        worktree_progress: WorktreeProgress | None = None,
    ) -> None:
        ready = asyncio.Event()
        self._turns_deferred = ready
        self._deferred_setup_result = None
        self._deferred_turn_claimed = False
        self._worktree_progress = worktree_progress
        self._setup_abandoned = False

        async def run() -> None:
            try:
                self._deferred_setup_result = await work
            except asyncio.CancelledError:
                self._setup_abandoned = True
                raise
            except Exception as exc:
                self._setup_abandoned = True
                logger.warning("Deferred session setup failed", exc_info=exc)
            finally:
                ready.set()

        self._deferred_setup_task = asyncio.create_task(run())

    async def _await_deferred_setup(self) -> WorktreeResolution | None:
        ready = self._turns_deferred
        if ready is None:
            return None
        await ready.wait()
        if self._setup_abandoned:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                "The session's workspace could not be prepared",
            )
        return self._deferred_setup_result

    async def adopt_context(self, context: UnifiedSessionContext) -> None:
        previous = self._context
        # The Harness keeps the routes it already accepted, and nothing re-reads
        # them here, so the incoming resolver has to inherit their published
        # names or every routed provided tool would fall back to its route and
        # lose the permission configured against it.
        context.provided_names.adopt(previous.provided_names)
        # Same reason: the live executor closes over the lists the previous holder
        # handed out, so a rewind has to reach those and not the incoming blank ones.
        context.vibe_tools.adopt(previous.vibe_tools)
        self._context = context
        try:
            await self._apply_derivation(allow_reserved_turn=True)
        except BaseException:
            self._context = previous
            raise

    async def persist_cwd(self, cwd: str) -> None:
        await _harness_call(self._session.persist_cwd(cwd))

    def _projected_runtime(
        self, derivation: UnifiedRuntimeDerivation
    ) -> RuntimeSnapshot:
        """A derivation's projection of the config, over this session's own facts."""
        return derivation.runtime.model_copy(
            update={
                "mcp": self._runtime.mcp,
                "connectors": self._runtime.connectors,
                "stats": _repriced_stats(self._runtime.stats, derivation.runtime.stats),
            }
        )

    async def _derive_configuration(self) -> UnifiedRuntimeDerivation:
        return await asyncio.to_thread(self._context.derive, self._settings)

    async def _push_configuration(self, derivation: UnifiedRuntimeDerivation) -> None:
        await self._session.apply_runtime_configuration(
            derivation.core_config.settings,
            derivation.adapter_config,
            derivation.core_config.capabilities,
        )

    def _adopt_derivation(self, derivation: UnifiedRuntimeDerivation) -> None:
        """Publish the derivation the session is now running."""
        self._runtime = self._projected_runtime(derivation)
        self._adapter_config = derivation.adapter_config
        self._model = derivation.adapter_config.model
        self._session_telemetry.set_model(self._model)
        self._skills = derivation.skill_payloads
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
        profile back. What it needs is the approval policy, which is read per
        tool action: ``graft_live_approval_policy`` carries exactly that set
        (``_LIVE_APPROVAL_POLICY_FIELDS``) onto the config the session is
        already running, landing on the running turn's next tool call.

        Only the approval policy moves. The rest of the derivation is paired with
        state the Core holds across the turn -- it reads its settings when the
        turn starts and reconfigures through its own command queue -- so moving
        one half alone would split them: skill payloads are the adapter's half of
        the definitions the Core advertises, and ``active_model`` is what the
        adapter serves the turn's completions with. They go together on the
        next turn, which ``start_turn`` flushes.
        """
        # Exclusive for the same reason a config write is serialised: this
        # derives and pushes for itself, so an apply that derived before the
        # switch could otherwise land after it and restore the old agent.
        async with self._deferred.exclusive():
            derivation = await asyncio.to_thread(self._context.derive, self._settings)
            if self._session.active_turn_id is None:
                await self._session.apply_runtime_configuration(
                    derivation.core_config.settings,
                    derivation.adapter_config,
                    derivation.core_config.capabilities,
                )
                self._deferred.mark_applied()
                self._adopt_derivation(derivation)
                return
            approval_only = graft_live_approval_policy(
                self._adapter_config, derivation.adapter_config
            )
            self._session.apply_adapter_config(approval_only)
            # Propagate the approval policy change to subagent bindings and
            # already-running child sessions, so a mid-turn mode switch (e.g.
            # switching to auto-approve while a subagent is running) takes
            # effect on the subagent's next tool call.
            await self._session.reconfigure_subagents(approval_only)  # type: ignore[attr-defined]
            self._deferred.park()
            self._adopt_derivation(replace(derivation, adapter_config=approval_only))

    async def _refresh_after_skill_change(self) -> RuntimeSnapshot:
        await self._context.config_orchestrator.reload(preflight=self._preflight_config)
        await self._apply_derivation()
        return self._runtime

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
        await self._deferred.apply_when_idle()

    async def _apply_written_config(self) -> None:
        """Apply what the user just wrote, and report it even when it is parked.

        Clients read the runtime on the response as the catalogue they just
        wrote, so a parked write must still answer with it. A derivation nobody
        asked for stays invisible until it takes effect.
        """
        await self._apply_derivation_when_idle()
        # Under the lock: derived outside it, a projection can be computed
        # before a settle adopts a newer configuration and assigned after,
        # leaving the session reporting a runtime it is no longer running.
        async with self._deferred.exclusive():
            if not self._deferred.parked:
                return
            # Derived again, deliberately: the config can move again before the
            # boundary, and the later derivation is the one that takes effect.
            # Only the projection moves here; the turn keeps what it bound.
            derivation = await asyncio.to_thread(self._context.derive, self._settings)
            self._runtime = self._projected_runtime(derivation)

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

    async def _settle_configuration(self) -> None:
        """Land a parked write before the Session opens a turn on it."""
        if await self._deferred.settle():
            await self._announce_runtime()

    async def _settle_reserved_turn_configuration(self) -> None:
        """Land a parked write before a reserved turn's Runtime is promoted.

        A deferred turn never passes the boundary ``_settle_configuration``
        runs on: it is reserved while its workspace is prepared, and promoted
        from there.
        """
        if await self._deferred.settle_through(self._push_into_reserved_turn):
            await self._announce_runtime()

    async def _announce_runtime(self) -> None:
        """Tell subscribers what the session is running now.

        Dispatch emits ``runtime/updated`` for a write it applied, and there is
        nothing to emit one for a write it parked. A turn opens on every path
        that announces from here and the configuration has already landed, so a
        client that cannot be reached is logged rather than left to stop it.
        """
        try:
            await self._notify_runtime_updated()
        except Exception:
            logger.exception(
                "Failed to emit runtime/updated for session_id=%s", self.session_id
            )

    async def _stop_background_work(self) -> None:
        """Stop what the session still owns before teardown continues."""
        scheduler = self._scheduled_loops_task
        self._scheduled_loops_task = None
        if scheduler is None:
            return
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler

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
        if self._teleport_active is not None:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                f"A teleport is already running: {self._teleport_active}",
                {"teleportId": self._teleport_active},
            )
        self._require_model_owned_root(params.session_id)
        if (
            self._turns_deferred is not None
            and not self._deferred_turn_claimed
            and self._deferred_turns is not None
            and self._session.ephemeral
        ):
            self._deferred_turn_claimed = True
            try:
                return await self._start_deferred_turn(params)
            except BaseException as exc:
                self._deferred_turn_claimed = False
                if not isinstance(exc, Exception) or self._session.ephemeral:
                    raise
        await self._await_deferred_setup()
        runtime_updated = await self._pin_session_model_choice()
        params = await self._prepared_turn_params(params, inject_skill=True)
        # Set the message id before the harness session schedules the runtime
        # task, so the provider request metadata reads it on the first
        # completion rather than racing the adapter's snapshot reconciliation.
        self._set_client_message_id(params.client_user_message_id)
        was_ephemeral = self._session.ephemeral
        result = cast(Any, await _harness_call(self._session.start_turn(params)))
        await self._persist_scheduled_loops_after_promotion(was_ephemeral=was_ephemeral)
        await self._persist_session_metadata_after_promotion(
            was_ephemeral=was_ephemeral
        )
        response = result.response
        turn = response.turn
        return await self._accepted_user_activity_result(
            TurnStartResponse(
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

    async def _start_deferred_turn(
        self, params: TurnStartParams
    ) -> SessionBackendResult[TurnStartResponse]:
        deferred_turns = self._deferred_turns
        if deferred_turns is None:
            raise RuntimeError("Deferred Turn capability is unavailable")
        progress = self._worktree_progress
        progress_entry_id = f"worktree-{uuid4()}"
        resolution: WorktreeResolution | None = None
        was_ephemeral = self._session.ephemeral

        # Deferred preparation starts after the response, but completion
        # attribution must already identify the accepted user message.
        self._set_client_message_id(params.client_user_message_id)

        def pending_entries() -> tuple[HarnessPublicHistoryEntry, ...]:
            if progress is None:
                return ()
            return (
                self._worktree_history_entry(
                    progress_entry_id, progress.detail, progress.state
                ),
            )

        def completed_entries() -> tuple[HarnessPublicHistoryEntry, ...]:
            if progress is None:
                return ()
            prepared = resolution.prepared_worktree if resolution is not None else None
            if prepared is None:
                raise RuntimeError("Created worktree setup returned no worktree")
            effect = WorktreeEffect.resolved(prepared)
            return (
                self._worktree_history_entry(
                    progress_entry_id, effect.detail, effect.state
                ),
            )

        def failed_entries(error: Exception) -> tuple[HarnessPublicHistoryEntry, ...]:
            if progress is None:
                return ()
            if resolution is not None and resolution.prepared_worktree is not None:
                return completed_entries()
            return (
                self._worktree_history_entry(
                    progress_entry_id, progress.detail, progress.failed_state(error)
                ),
            )

        async def after_promotion() -> None:
            await self._persist_scheduled_loops_after_promotion(
                was_ephemeral=was_ephemeral
            )
            await self._persist_session_metadata_after_promotion(
                was_ephemeral=was_ephemeral
            )

        async def prepare(
            _context: HarnessDeferredTurnPreparationContext,
        ) -> HarnessDeferredTurnPreparationResult:
            nonlocal resolution
            try:
                setup = await self._await_deferred_setup()
                if not isinstance(setup, WorktreeResolution):
                    raise RuntimeError("Deferred worktree setup returned no resolution")
                resolution = setup
                await self._settle_reserved_turn_configuration()
                if await self._pin_session_model_choice():
                    await self._announce_runtime()
                prepared = await self._prepared_turn_params(params, inject_skill=True)
            except Exception as exc:
                raise HarnessDeferredTurnPreparationError(
                    exc, failed_entries(exc)
                ) from exc
            return HarnessDeferredTurnPreparationResult(
                runtime_input=HarnessPreparedRuntimeInput(
                    turn=self._deferred_turn_request(prepared)
                ),
                history_entries=completed_entries(),
                after_promotion=after_promotion,
            )

        result = await _harness_call(
            deferred_turns.start_deferred_turn(
                HarnessDeferredTurnStartParams(
                    session_id=self.session_id,
                    turn=self._deferred_turn_request(params),
                    prepare=HarnessDeferredTurnPreparation(
                        pending_history_entries=pending_entries(), run=prepare
                    ),
                )
            )
        )

        def on_response_abandoned() -> None:
            self._deferred_turn_claimed = False
            result.on_response_abandoned()

        return await self._accepted_user_activity_result(
            TurnStartResponse(
                turn=PublicTurn(
                    id=result.turn_id,
                    session_id=result.session_id,
                    status=PublicTurnStatus.IN_PROGRESS,
                    started_at=result.started_at,
                ),
                last_event_id=self._event_id,
            ),
            after_response=result.after_response,
            on_response_abandoned=on_response_abandoned,
        )

    async def _notify_runtime_updated(self) -> None:
        if self._services is None:
            return
        await self._services.notify("runtime/updated", self.runtime_updated_params())

    def _worktree_history_entry(
        self,
        entry_id: str,
        detail: WorktreeEffectDetail,
        state: RunningEffectState | CompletedEffectState | FailedEffectState,
    ) -> HarnessPublicHistoryEntry:
        entry = PublicEffectEntry(
            id=entry_id,
            session_id=self.session_id,
            created_at=now_ms(),
            updated_at=now_ms(),
            generation_status=(
                PublicEntryGenerationStatus.IN_PROGRESS
                if isinstance(state, RunningEffectState)
                else PublicEntryGenerationStatus.COMPLETED
            ),
            title="worktree",
            detail=detail,
            state=state,
        )
        if isinstance(state, RunningEffectState):
            return HarnessRunningWorktreeHistoryEntry.model_validate(
                entry, from_attributes=True
            )
        if isinstance(state, CompletedEffectState):
            return HarnessCompletedWorktreeHistoryEntry.model_validate(
                entry, from_attributes=True
            )
        return HarnessFailedWorktreeHistoryEntry.model_validate(
            entry, from_attributes=True
        )

    @staticmethod
    def _deferred_turn_request(params: TurnStartParams) -> HarnessTurnStartRequest:
        return HarnessTurnStartRequest(
            idempotency_key=params.idempotency_key,
            session_id=params.session_id,
            message=tuple(
                _harness_deferred_content_block(block)
                for block in session_content_blocks_from_vibe(params.message)
            ),
            injected=params.injected,
            client_user_message_id=params.client_user_message_id,
            auto_title=params.auto_title,
            user_display_content=(
                harness_session_protocol.UserDisplayContentAnnotation.model_validate(
                    params.user_display_content, from_attributes=True
                )
                if params.user_display_content is not None
                else None
            ),
            mention_stats=(
                HarnessTurnMentionStats.model_validate(
                    params.mention_stats, from_attributes=True
                )
                if params.mention_stats is not None
                else None
            ),
        )

    async def enqueue_turn(
        self, params: TurnEnqueueParams
    ) -> SessionBackendResult[TurnEnqueueResponse]:
        if self._teleport_active is not None:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT,
                f"A teleport is already running: {self._teleport_active}",
                {"teleportId": self._teleport_active},
            )
        await self._await_deferred_setup()
        # The session model is deliberately left alone: a queued turn is
        # promoted by the Session, which settles the parked configuration
        # first, and `start_turn` pins what a turn it opens itself runs on.
        # Recording here would resolve an empty pick to today's default and
        # take the session off the default the user asked to follow.
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
        return await self._accepted_user_activity_result(
            validate_backend_wire(
                TurnEnqueueResponse,
                result.response.model_dump(mode="json", by_alias=True),
            ),
            after_response=result.after_response,
            accepted=result.after_response is not None,
        )

    async def _persist_session_active_model(self, active_model: str) -> bool:
        """Record the model this session runs on the session itself.

        A reopened session restores its model from this pin, so a pick that
        only reached the configuration would come back as the model the last
        turn started with -- contradicting the transcript marker it wrote.

        The pick is recorded as the user made it, empty alias included: storing
        what the default resolves to today would pin the session to that model,
        and following the default is what picking it asked for.
        """
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
        return await _harness_call(
            self._session.persist_pin(SessionPin.ACTIVE_MODEL, active_model)
        )

    async def _persist_session_reasoning_effort(self) -> bool:
        """Record the level this session runs, so a reopen does not read the host's."""
        orchestrator = self._context.config_orchestrator
        model = orchestrator.config.get_active_model()
        # A model owned above the session has no level the session can call its
        # own, and the one recorded for a previous model is not it: the empty
        # pin is how a session says it follows the configuration.
        level = (
            model.thinking
            if model_thinking_is_session_writable(orchestrator, model.alias)
            else ""
        )
        if (self._session.pin(SessionPin.REASONING_EFFORT) or "") == level:
            return False
        return await _harness_call(
            self._session.persist_pin(SessionPin.REASONING_EFFORT, level)
        )

    async def _pin_session_model_choice(self) -> bool:
        """Pin what the turn about to start runs on: the model, and its level."""
        model = self._context.config_orchestrator.config.get_active_model()
        pinned_model = await self._persist_session_active_model(model.alias)
        pinned_effort = await self._persist_session_reasoning_effort()
        if not (pinned_model or pinned_effort):
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
            response=validate_backend_wire(
                TurnQueueReadResponse,
                result.response.model_dump(mode="json", by_alias=True),
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
            response=validate_backend_wire(
                TurnQueueRemoveResponse,
                result.response.model_dump(mode="json", by_alias=True),
            ),
            after_response=result.after_response,
        )

    async def replace_queued_turn(
        self, params: TurnQueueReplaceParams
    ) -> SessionBackendResult[TurnQueueReplaceResponse]:
        await self._await_deferred_setup()
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
        return await self._accepted_user_activity_result(
            validate_backend_wire(
                TurnQueueReplaceResponse,
                result.response.model_dump(mode="json", by_alias=True),
            ),
            after_response=result.after_response,
            accepted=result.after_response is not None,
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
        return await self._accepted_user_activity_result(
            validate_backend_wire(
                TurnQueueResumeResponse,
                result.response.model_dump(mode="json", by_alias=True),
            ),
            after_response=result.after_response,
            accepted=result.after_response is not None,
        )

    async def steer_queued_turn(
        self, params: TurnQueueSteerParams
    ) -> SessionBackendResult[TurnQueueSteerResponse]:
        harness_params_type = cast(
            Any, getattr(harness_session_protocol, "TurnQueueSteerParams", None)
        )
        raw_steer_queued_turn = getattr(self._session, "steer_queued_turn", None)
        if harness_params_type is None or not callable(raw_steer_queued_turn):
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "The installed Unified Harness does not support queued steering",
            )
        steer_queued_turn = cast(Callable[[Any], Awaitable[Any]], raw_steer_queued_turn)
        result = cast(
            Any,
            await _harness_call(
                steer_queued_turn(
                    harness_params_type.model_validate(
                        params.model_dump(mode="json", by_alias=True)
                    )
                )
            ),
        )
        activity_key = (params.session_id, params.queue_item_id)
        accepted = activity_key not in self._accepted_queued_steer_activity
        self._accepted_queued_steer_activity.add(activity_key)
        await self.flush_events()
        response = result.response
        return await self._accepted_user_activity_result(
            TurnQueueSteerResponse(
                queue_item_id=response.queue_item_id,
                turn_id=response.turn_id,
                last_event_id=self._event_id,
            ),
            after_response=result.after_response,
            accepted=accepted,
        )

    async def steer_turn(
        self, params: TurnSteerParams
    ) -> SessionBackendResult[TurnSteerResponse]:
        self._require_model_owned_root(params.session_id)
        params = await self._prepared_turn_params(
            params, inject_skill=params.inject_invoked_skill
        )
        self._set_client_message_id(params.client_user_message_id)
        result = cast(Any, await _harness_call(self._session.steer_turn(params)))
        response = result.response
        return await self._accepted_user_activity_result(
            TurnSteerResponse(
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
        harness_after_response = cast(
            Callable[[], None] | None, getattr(result, "after_response", None)
        )
        harness_on_response_abandoned = cast(
            Callable[[], None] | None, getattr(result, "on_response_abandoned", None)
        )
        after_response = harness_after_response
        on_response_abandoned = harness_on_response_abandoned
        if self._deferred_turn_claimed:
            response_settled = False

            def settle_response(callback: Callable[[], None] | None) -> None:
                nonlocal response_settled
                if response_settled:
                    return
                response_settled = True
                self._deferred_turn_claimed = False
                if callback is not None:
                    callback()

            def release_after_response() -> None:
                settle_response(harness_after_response)

            def release_on_response_abandoned() -> None:
                settle_response(harness_on_response_abandoned)

            after_response = release_after_response
            on_response_abandoned = release_on_response_abandoned

        return SessionBackendResult(
            response=TurnInterruptResponse(
                accepted=response["accepted"], last_event_id=response["last_event_id"]
            ),
            after_response=after_response,
            on_response_abandoned=on_response_abandoned,
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
            params = await self._with_described_input_images(params)
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
        await self._persist_session_metadata_after_promotion(
            was_ephemeral=was_ephemeral
        )
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
        callback_response = CallbackResultResponse(
            accepted=response["accepted"], last_event_id=response["last_event_id"]
        )
        if not (
            isinstance(params, CallbackResultParams)
            and params.result.output is not None
            and callback_response.accepted
        ):
            return SessionBackendResult(
                response=callback_response, after_response=result.after_response
            )
        activity_key = (params.session_id, params.result.callback_id)
        accepted = activity_key not in self._accepted_callback_activity
        self._accepted_callback_activity.add(activity_key)
        return await self._accepted_user_activity_result(
            callback_response, after_response=result.after_response, accepted=accepted
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
                self.cwd,
                event_id=self._event_id,
                metadata=self._metadata,
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
        # An in-place rewind keeps the session, and with it the executor closure the
        # todo list lives in: left alone, `todo read` would answer with entries the
        # truncated history no longer records. A fork rewind gets a fresh executor.
        self._context.vibe_tools.forget_todos(params.session_id)
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

    async def _dispatch_vibe_code(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        try:
            controller = self._vibe_code_controller()
            if method.startswith("vibeCode/projects/"):
                return await self._dispatch_vibe_code_projects(
                    method, raw_params, controller
                )
            return await self._dispatch_teleport(method, raw_params, controller)
        except VibeCodeConflictError as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except VibeCodeAccessError as exc:
            raise RequestFailure(ProtocolErrorCode.FORBIDDEN, str(exc)) from exc
        except VibeCodeError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc

    def _vibe_code_controller(self) -> VibeCodeController:
        if self._vibe_code is None:
            session = UnifiedVibeCodeSession(self)
            notify = self._host_notify
            self._vibe_code = VibeCodeController(session, notify)
        return self._vibe_code

    async def _host_notify(self, method: str, params: ProtocolModel) -> None:
        if self._notify is not None:
            await self._notify(method, params)

    async def _dispatch_vibe_code_projects(
        self, method: str, raw_params: dict[str, Any], controller: VibeCodeController
    ) -> DispatchResult:
        match method:
            case "vibeCode/projects/open":
                params = validate_wire(VibeCodeProjectsOpenParams, raw_params)
                self._require_session(params.session_id)
                picker_id, view, project_id = await controller.open(
                    purpose=params.purpose, prompt=params.prompt
                )
                response: ProtocolModel = VibeCodeProjectsOpenResponse(
                    picker_id=picker_id, view=view, resolved_project_id=project_id
                )
            case "vibeCode/projects/loadMore":
                params = validate_wire(VibeCodeProjectsLoadMoreParams, raw_params)
                self._require_session(params.session_id)
                view, focus = await controller.load_more(params.picker_id)
                response = VibeCodeProjectsLoadMoreResponse(
                    view=view, focus_option_id=focus
                )
            case "vibeCode/projects/create":
                params = validate_wire(VibeCodeProjectCreateParams, raw_params)
                self._require_session(params.session_id)
                view, project = await controller.create(
                    picker_id=params.picker_id,
                    name=params.name,
                    default_branch=params.default_branch,
                )
                response = VibeCodeProjectCreateResponse(view=view, project=project)
            case "vibeCode/projects/select":
                params = validate_wire(VibeCodeProjectSelectParams, raw_params)
                self._require_session(params.session_id)
                view, project = await controller.select(
                    picker_id=params.picker_id, project_id=params.project_id
                )
                response = VibeCodeProjectSelectResponse(view=view, project=project)
            case "vibeCode/projects/unlink":
                params = validate_wire(VibeCodeProjectUnlinkParams, raw_params)
                self._require_session(params.session_id)
                view = await controller.unlink(params.picker_id)
                response = VibeCodeProjectUnlinkResponse(view=view)
            case "vibeCode/projects/cancel":
                params = validate_wire(VibeCodeProjectCancelParams, raw_params)
                self._require_session(params.session_id)
                await controller.cancel_picker(params.picker_id)
                response = EmptyResponse()
            case "vibeCode/projects/recover":
                params = validate_wire(VibeCodeProjectRecoverParams, raw_params)
                self._require_session(params.session_id)
                view, recovered = await controller.recover_stale_link(params.picker_id)
                response = VibeCodeProjectRecoverResponse(
                    recovered=recovered, view=view
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _dispatch_teleport(
        self, method: str, raw_params: dict[str, Any], controller: VibeCodeController
    ) -> DispatchResult:
        after_response: Callable[[], None] | None = None
        match method:
            case "vibeCode/teleport/start":
                params = validate_wire(TeleportStartParams, raw_params)
                self._require_session(params.session_id)
                await controller.reserve_teleport(params)
                response: ProtocolModel = TeleportStartResponse(
                    operation_id=params.operation_id
                )
                after_response = lambda: controller.start_teleport(params)
            case "vibeCode/teleport/cancel":
                params = validate_wire(TeleportCancelParams, raw_params)
                self._require_session(params.session_id)
                response = TeleportCancelResponse(
                    cancelled=await controller.cancel_teleport(params.operation_id)
                )
            case "vibeCode/teleport/push/respond":
                params = validate_wire(TeleportPushRespondParams, raw_params)
                self._require_session(params.session_id)
                controller.respond_to_push(params.operation_id, params.approved)
                response = EmptyResponse()
            case _:
                raise method_not_found(method)
        return DispatchResult(response, after_response)

    async def _read_history_entries(self) -> list[PublicHistoryEntry]:
        """Read the latest projected history entries (bounded to 500)."""
        state = await self._read_page_state(self.session_id)
        return state.history or []

    def _begin_teleport(self, operation_id: str) -> None:
        self._require_idle()
        self._teleport_active = operation_id

    def _finish_teleport(self, operation_id: str) -> None:
        if self._teleport_active == operation_id:
            self._teleport_active = None

    async def _run_teleport_orchestration(
        self,
        prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: ProjectPickerTelemetryPayload | None = None,
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportPushResponseEvent | None]:
        from vibe.core.teleport.orchestrator import TeleportOrchestrator
        from vibe.core.teleport.teleport import TeleportService

        config = self.config
        teleport_service = TeleportService(
            vibe_code_sessions_base_url=config.vibe_code_sessions_base_url,
            api_key=config.resolve_mistral_api_key(),
            workdir=self._cwd_path(),
        )
        summarizer = UnifiedTeleportContextSummarizer(self)
        entries = await self._read_history_entries()
        summarizer.set_cached_entries(entries)
        orchestrator = TeleportOrchestrator(
            summarizer=summarizer,
            teleport_service=teleport_service,
            telemetry_client=self._telemetry,
            session_id=self.session_id,
            nb_session_messages=len(entries),
            project_picker=project_picker,
            launch_context=self._launch_context,
        )
        gen = orchestrator.execute(prompt, project_id=project_id)
        try:
            response: TeleportPushResponseEvent | None = None
            while True:
                try:
                    event = await gen.asend(response)
                except StopAsyncIteration:
                    break
                response = yield event
        finally:
            with contextlib.suppress(GeneratorExit, asyncio.CancelledError):
                await gen.aclose()

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._vibe_code is not None:
            with contextlib.suppress(Exception):
                await self._vibe_code.reset()
        # Flush any completion whose snapshot never drove a final ``_updated_state``
        # before teardown closes the telemetry client.
        self._forward_request_sent()
        # Before the slower parts of the close, so a sweep running concurrently
        # cannot read this worktree as still occupied by a session that is on
        # its way out.
        if self.cwd is not None:
            SessionWorktrees.release(Path(self.cwd), self.session_id)
        self._release_deferred_events = None
        self._skip_deferred_flush_once = False
        self._defer_event_flush = False
        # Kill any `!` command still running, so a long-lived subprocess cannot
        # outlive the session that spawned it.
        with contextlib.suppress(Exception):
            await self._host_shell.controller.close()
        await self._stop_background_work()
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
        setup = self._deferred_setup_task
        self._deferred_setup_task = None
        if setup is not None:
            setup.cancel()
            with contextlib.suppress(BaseException):
                await setup
        self._emit_session_closed_telemetry()
        with contextlib.suppress(Exception):
            await self._context.experiment_manager.aclose()
        try:
            await self._persist_session_metadata_best_effort()
            try:
                await self._scheduled_loops.persist()
            except ScheduledLoopStoreError as exc:
                logger.warning("Failed to persist scheduled loops", exc_info=exc)
            await self._session.shutdown()
        finally:
            await self._telemetry.aclose()

    def _cwd_path(self) -> Path:
        return Path(self.cwd or Path.cwd()).expanduser().resolve()

    def _mention_roots(self) -> tuple[Path, ...]:
        """The roots a mention may be inlined from, as the file tools see them."""
        return tuple(self._adapter_config.workspace_roots)

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
        enabled = self._context.session_logging_enabled
        persisted = self._persists_to_disk()
        return SessionLogSummary(
            enabled=enabled,
            session_id=self.session_id,
            persisted=persisted,
            path=str(self._session_dir()) if persisted else None,
            title=state.session.title,
            needs_initial_auto_title=state.session.title is None,
        )

    async def _prepare_prompt(
        self, params: WorkspacePromptPrepareParams
    ) -> PreparedPrompt:
        # No session dir means the image rides inline instead of being snapshotted
        # to one, which is what the legacy backend does with logging disabled.
        session_dir = (
            self._session_dir() if self._context.session_logging_enabled else None
        )
        return prepare_prompt_from_context(
            params.message, cwd=self._cwd_path(), session_dir=session_dir
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

    async def _scratchpad_block(self) -> ContentBlock | None:
        text = await scratchpad_block_to_restate(
            storage_root=self._storage_root,
            session_id=self.session_id,
            history=self._history_entries,
        )
        return None if text is None else TextContentBlock(text=text)

    async def _history_entries(self) -> Sequence[PublicHistoryEntry]:
        return (await self._read_page_state(self.session_id)).history or ()

    async def _prepared_turn_params[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT, *, inject_skill: bool
    ) -> ParamsT:
        skill = (
            await self._invoked_skill_block(params.message) if inject_skill else None
        )
        scratchpad = await self._scratchpad_block()
        try:
            # Ahead of the mentioned files, so an image description is steered
            # by what the user typed rather than by an inlined file's contents.
            params = await self._with_described_images(params)
            params = await self._with_mentioned_file_blocks(params)
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        appended = [block for block in (skill, scratchpad) if block is not None]
        if not appended:
            return params
        return params.model_copy(update={"message": [*params.message, *appended]})

    async def _prepared_queue_params[
        ParamsT: TurnEnqueueParams | TurnQueueReplaceParams
    ](self, params: ParamsT) -> ParamsT:
        try:
            params = await self._with_described_image_entries(params)
        except ValueError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
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

    async def _with_described_images[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT
    ) -> ParamsT:
        described = await self._image_describer.described_blocks(params.message)
        if described is params.message:
            return params
        return params.model_copy(update={"message": described})

    async def _with_described_image_entries[
        ParamsT: TurnEnqueueParams | TurnQueueReplaceParams
    ](self, params: ParamsT) -> ParamsT:
        # Core dequeues into a turn on its own, so the adapter gets no second
        # look at this content: an image left queued reaches the provider.
        entries = list(params.entries)
        changed = False
        for index, entry in enumerate(entries):
            if not isinstance(entry, TurnUserInputEntry):
                continue
            content = await self._image_describer.described_session_blocks(
                entry.content
            )
            if content is entry.content:
                continue
            entries[index] = entry.model_copy(update={"content": content})
            changed = True
        if not changed:
            return params
        return params.model_copy(update={"entries": entries})

    async def _with_mentioned_file_blocks[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT
    ) -> ParamsT:
        text = _text_from_blocks(params.message)
        blocks = await mentioned_file_content_blocks_async(
            text, base_dir=self._cwd_path(), workspace_roots=self._mention_roots()
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
            text, base_dir=self._cwd_path(), workspace_roots=self._mention_roots()
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

    async def _with_described_input_images(
        self, params: ContextInjectParams
    ) -> ContextInjectParams:
        described = await self._image_describer.described_blocks(params.input)
        if described is params.input:
            return params
        return params.model_copy(update={"input": described})

    async def _with_mentioned_file_blocks_for_input(
        self, params: ContextInjectParams
    ) -> ContextInjectParams:
        text = _text_from_blocks(params.input)
        blocks = await mentioned_file_content_blocks_async(
            text, base_dir=self._cwd_path(), workspace_roots=self._mention_roots()
        )
        if not blocks:
            return params
        return params.model_copy(update={"input": [*params.input, *blocks]})

    async def _read_page_state(self, session_id: str) -> PublicSessionState:
        params = SessionReadParams(
            session_id=session_id,
            history=PageRequest(limit=500),
            turns=PageRequest(limit=500),
        )
        if session_id != self.session_id:
            return (await self._read_child_response(params)).state
        result = await self._session.read(_harness_read_params(params))
        return _read_response(
            result.snapshot,
            self.cwd,
            metadata=self._metadata,
            pins=self._live_session_pins(),
        ).state

    async def _read_child_response(
        self, params: SessionReadParams
    ) -> SessionReadResponse:
        if self._host is None:
            state = self._child_states.get(params.session_id)
            if state is None:
                raise SessionBackendError(
                    ProtocolErrorCode.NOT_FOUND,
                    f"Session not found: {params.session_id}",
                )
            return SessionReadResponse(state=state, last_event_id=state.event_id)
        result = await _harness_call(self._host.read(_harness_read_params(params)))
        metadata = await asyncio.to_thread(
            _read_unified_session_metadata,
            _unified_session_dir(self._storage_root, params.session_id),
        )
        return _read_response(
            result.snapshot, result.cwd, event_id=self._event_id, metadata=metadata
        )

    def _live_session_pins(self) -> _SessionPins:
        return _SessionPins(
            model=self._session.pin(SessionPin.ACTIVE_MODEL) or None,
            reasoning_effort=self._session.pin(SessionPin.REASONING_EFFORT) or None,
            agent=_pinned_agent(
                self._context.agents, self._session.pin(SessionPin.AGENT_NAME)
            ),
        )

    def _read_response(self, snapshot: HarnessSessionSnapshot) -> SessionReadResponse:
        response = _read_response(
            snapshot,
            self.cwd,
            event_id=self._event_id,
            metadata=self._metadata,
            pins=self._live_session_pins(),
        )
        state = self._state_with_child_summaries(response.state)
        response = response.model_copy(update={"state": state})
        self._event_id = response.last_event_id
        return response

    async def _translated_events(  # noqa: PLR0915
        self, subscription: HarnessSessionSubscription, previous: PublicSessionState
    ) -> AsyncIterator[SessionBackendEvent]:
        async with self._events_condition:
            self._events_subscribed = True
            self._events_condition.notify_all()
        try:
            async for event in self._with_host_events(subscription.events):
                if isinstance(event, SessionBackendEvent):
                    yield event
                    await self._settle_host_event()
                    continue
                event_type = event.get("type")
                if event_type == "child_session_registered":
                    registered, previous, watermark = self._register_child_event(
                        event, previous
                    )
                    for child_event in registered:
                        yield child_event
                    self._translated_state = previous
                    await self._mark_harness_event_observed(watermark)
                    continue
                if event_type == "child_session_event":
                    translated, previous, watermark = await self._translate_child_event(
                        event, subscription.snapshot.history_limit, previous
                    )
                    for child_event in translated:
                        yield child_event
                    self._translated_state = previous
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
                # Must stay ahead of the fallback handling below: a classification
                # is a telemetry side-channel, not a UI event.
                if await self._consumed_classification_event(event):
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
            # Nothing is left to forward what the Host queued, and a later
            # subscription is a different stream -- so drop it rather than
            # replay it there, and release whoever is waiting on it.
            while not self._host_shell.queue.empty():
                _ = self._host_shell.queue.get_nowait()
            async with self._events_condition:
                self._events_subscribed = False
                self._host_shell.pending = 0
                self._events_condition.notify_all()

    async def _settle_host_event(self) -> None:
        async with self._events_condition:
            self._host_shell.pending -= 1
            self._events_condition.notify_all()

    async def _with_host_events(
        self, harness_events: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any] | SessionBackendEvent]:
        """Interleave events the Host minted with the Harness subscription.

        A Unified session has exactly one event stream and the Harness owns it,
        so an event with no Harness event behind it -- today only a manual `!`
        command's ``shell`` effect -- has nothing to ride out on. Merging here
        keeps it on the one stream, in the order the Host produced it, without
        putting a Host-authored entry into Core's history.
        """
        pending_host: asyncio.Task[SessionBackendEvent] | None = None
        pending_harness: asyncio.Task[dict[str, Any]] | None = None
        try:
            while True:
                if pending_host is None:
                    pending_host = asyncio.create_task(self._host_shell.queue.get())
                if pending_harness is None:
                    pending_harness = asyncio.ensure_future(anext(harness_events))
                done, _ = await asyncio.wait(
                    (pending_host, pending_harness), return_when=asyncio.FIRST_COMPLETED
                )
                if pending_host in done:
                    host = pending_host
                    pending_host = None
                    yield host.result()
                if pending_harness in done:
                    harness = pending_harness
                    pending_harness = None
                    try:
                        yield harness.result()
                    except StopAsyncIteration:
                        return
        finally:
            for task in (pending_host, pending_harness):
                if task is None:
                    continue
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

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
        retry_changed = previous.retrying != current.retrying
        current = self._state_with_child_summaries(current)
        app_events = [
            app_event
            for app_event in reconcile_snapshot(previous, current)
            if not isinstance(app_event, SessionSnapshot | ChildSessionUpdated)
        ]
        stats = self._advance_stats(previous, current, _approximate_steps(app_events))
        updated = stats != self._runtime.stats
        self._runtime = self._runtime.model_copy(update={"stats": stats})
        translated, current = self._interleave_stats(
            app_events, current, stats, updated=updated
        )
        child_events, current = self._child_summary_events(previous, current)
        translated = [*translated, *child_events]
        if not retry_changed:
            return translated, current

        self._event_id += 1
        current = current.model_copy(update={"event_id": self._event_id}, deep=True)
        params = SessionSnapshotParams(
            event_id=self._event_id,
            session_id=current.session.id,
            emitted_at=int(time.time() * 1000),
            state=current,
        )
        translated.append(
            SessionBackendEvent(
                event=SessionSnapshot(current),
                method="session/snapshot",
                params=params,
                session_id=current.session.id,
                event_id=self._event_id,
            )
        )
        if current.retrying is not None:
            translated.append(_retrying_event(current.session.id, current.retrying))
        return translated, current

    def _register_child_event(
        self, event: Mapping[str, object], root_state: PublicSessionState
    ) -> tuple[list[SessionBackendEvent], PublicSessionState, int]:
        child_session_id = _required_event_str(event, "sessionId")
        raw_snapshot = event.get("snapshot")
        if not isinstance(raw_snapshot, dict):
            _reject("a child registration without a snapshot")
        snapshot = HarnessSessionSnapshot.model_validate(raw_snapshot)
        if snapshot.state.session.id != child_session_id:
            _reject("a child registration with mismatched identity")
        metadata = _read_unified_session_metadata(
            _unified_session_dir(self._storage_root, child_session_id)
        )
        state = _read_response(
            snapshot, self.cwd, event_id=self._event_id, metadata=metadata
        ).state
        self._child_states[child_session_id] = state
        current = self._state_with_child_summaries(root_state)
        translated, current = self._child_summary_events(root_state, current)
        return (translated, current, _required_event_id(event, "child registration"))

    async def _translate_child_event(
        self,
        event: Mapping[str, object],
        history_limit: int,
        root_state: PublicSessionState,
    ) -> tuple[list[SessionBackendEvent], PublicSessionState, int]:
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
        # A subagent's classification is a telemetry side-channel, exactly like the
        # root's (see _consumed_classification_event): forward it and emit no UI
        # events. Without this it falls through to the child state update below, which
        # treats the classification as a snapshot and drops the subagent's real
        # events from the parent stream.
        if raw_child_event.get("type") == CLASSIFICATION_EVENT_TYPE:
            self._emit_classification_telemetry(raw_child_event)
            return [], root_state, _required_event_id(event, "child wrapper")
        authorization = await self._authorization_event(
            raw_child_event, session_id=child_session_id
        )
        if authorization is not None:
            translated = [authorization[0]]
        elif (
            callback_events := self._callback_events(
                raw_child_event, include_history=False
            )
        ) is not None:
            translated = callback_events
        else:
            previous = self._child_states.get(child_session_id)
            if previous is None:
                _reject("a child event received before child registration")
            metadata = _read_unified_session_metadata(
                _unified_session_dir(self._storage_root, child_session_id)
            )
            current = self._updated_child_state(
                raw_child_event, previous, history_limit, metadata=metadata
            )
            self._child_states[child_session_id] = current
            refreshed = self._state_with_child_summaries(root_state)
            translated, root_state = self._child_summary_events(root_state, refreshed)
            if previous.retrying != current.retrying and current.retrying is not None:
                translated.append(_retrying_event(child_session_id, current.retrying))
        return (translated, root_state, _required_event_id(event, "child wrapper"))

    def _state_with_child_summaries(
        self, state: PublicSessionState
    ) -> PublicSessionState:
        self._observe_child_metadata(state.history or [])
        children = sorted(
            (
                self._child_summary(child_state.session)
                for child_state in self._child_states.values()
            ),
            key=lambda child: (child.created_at, child.id),
        )
        return state.model_copy(update={"child_sessions": children})

    def _observe_child_metadata(self, history: Sequence[PublicHistoryEntry]) -> None:
        preceding_spawn_ids: set[str] = set()
        for entry in history:
            if not isinstance(entry, PublicEffectEntry):
                continue
            detail = entry.detail
            if (
                isinstance(detail, SubagentEffectDetail)
                and detail.tool_name == "subagent.spawn"
                and detail.child_session_id is not None
                and detail.input is not None
            ):
                self._child_identities[detail.child_session_id] = _ChildIdentity(
                    name=detail.display.message or "subagent",
                    agent_type=detail.input.agent,
                    spawned_at=entry.created_at,
                )
                preceding_spawn_ids.add(detail.child_session_id)
                continue
            if (
                entry.id in self._observed_stop_effect_ids
                or not isinstance(detail, GenericEffectDetail)
                or detail.tool_name != "subagent.stop"
                or not isinstance(entry.state, CompletedEffectState)
                or not isinstance(detail.input, dict)
            ):
                continue
            agent_name = detail.input.get("agentName")
            if not isinstance(agent_name, str):
                continue
            candidates = [
                (identity.spawned_at, child_id)
                for child_id, identity in self._child_identities.items()
                if identity.name == agent_name
                and (
                    identity.spawned_at < entry.created_at
                    or child_id in preceding_spawn_ids
                )
                and child_id not in self._stopped_children
            ]
            if not candidates:
                continue
            _, child_id = max(candidates)
            self._stopped_children[child_id] = entry.updated_at
            self._observed_stop_effect_ids.add(entry.id)

    def _child_summary(self, session: PublicSession) -> PublicChildSession:
        identity = self._child_identities.get(session.id)
        stopped_at = self._stopped_children.get(session.id)
        return PublicChildSession(
            id=session.id,
            name=(
                identity.name
                if identity is not None
                else session.title or session.id[:8]
            ),
            agent_type=identity.agent_type if identity is not None else "subagent",
            status=(
                VibeArchivedSessionStatus()
                if stopped_at is not None
                else session.status
            ),
            token_usage=session.token_usage or VibeTokenUsage(),
            context_usage=session.context_usage,
            created_at=session.created_at,
            updated_at=max(session.updated_at, stopped_at or 0),
        )

    def _child_summary_events(
        self, previous: PublicSessionState, current: PublicSessionState
    ) -> tuple[list[SessionBackendEvent], PublicSessionState]:
        previous_children = {child.id: child for child in previous.child_sessions}
        events: list[SessionBackendEvent] = []
        for child in current.child_sessions:
            if previous_children.get(child.id) == child:
                continue
            self._event_id += 1
            events.append(
                self._child_session_event(
                    child, self._event_id, session_id=current.session.id
                )
            )
        return events, current.model_copy(update={"event_id": self._event_id})

    def _child_session_event(
        self, child_session: PublicChildSession, event_id: int, *, session_id: str
    ) -> SessionBackendEvent:
        params = ChildSessionUpdatedParams(
            event_id=event_id,
            session_id=session_id,
            emitted_at=int(time.time() * 1000),
            child_session=child_session,
        )
        return SessionBackendEvent(
            event=ChildSessionUpdated(child_session),
            method="session/childSessionUpdated",
            params=params,
            session_id=session_id,
            event_id=event_id,
        )

    def _state_update_events(
        self,
        event: Mapping[str, object],
        previous: PublicSessionState,
        history_limit: int,
    ) -> tuple[list[SessionBackendEvent], PublicSessionState, int]:
        current, watermark = self._updated_state(
            event, history_limit, cwd=self.cwd, metadata=self._metadata
        )
        translated, current = self._translate_snapshot_update(previous, current)
        return translated, current, watermark

    def _updated_child_state(
        self,
        event: Mapping[str, object],
        previous: PublicSessionState,
        history_limit: int,
        *,
        metadata: SessionMetadata | None,
    ) -> PublicSessionState:
        current, _watermark = self._updated_state(
            event,
            history_limit,
            cwd=previous.session.cwd,
            metadata=metadata,
            record_root_telemetry=False,
        )
        return current

    def _updated_state(
        self,
        event: Mapping[str, object],
        history_limit: int,
        *,
        cwd: str | None,
        metadata: SessionMetadata | None,
        record_root_telemetry: bool = True,
    ) -> tuple[PublicSessionState, int]:
        if event.get("type") != "session_state_updated":
            _reject(f"the Harness session event {event.get('type', event)!r}")
        raw_state = event.get("state")
        if not isinstance(raw_state, dict):
            _reject("a Harness session update without state")
        watermark = _required_event_id(event, "session")
        state = HarnessPublicSessionState.model_validate(raw_state)
        if record_root_telemetry:
            self._record_tool_telemetry(state)
            self._record_compaction_telemetry(state)
            self._forward_request_sent()
        state = _limit_harness_history(state, history_limit)
        current = _read_response(
            HarnessSessionSnapshot(
                state=state, history_limit=history_limit, watermark=watermark
            ),
            cwd,
            event_id=self._event_id,
            metadata=metadata,
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
            if isinstance(app_event, TurnQueueUpdated):
                # The Harness stamps its turn queue onto every state event it
                # publishes, so a state update minted for any other reason can
                # be where a queue change is first seen -- and then this diff,
                # not the dedicated queue event, is what has to carry it. The
                # envelope is built here because the event holds the queue
                # alone and the session id is only known at this level.
                translated.append(
                    _turn_queue_event_envelope(
                        app_event.queue, self._event_id, current.session.id
                    )
                )
                continue
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
        dedupe so a snapshot reconciled twice never double-counts. Each event
        carries the client message id of the user entry that opened its turn,
        matching the legacy loop's ``message_id`` attribution: entries are
        scanned in page order, so an effect that cannot be resolved through its
        turn id falls back to the most recently seen user entry.
        """
        agent_profile_name = self._active_agent_profile_name()
        for raw_entry in state.history.entries:
            client_message_id = _entry_client_message_id(raw_entry)
            if client_message_id is not None:
                self._last_client_message_id = client_message_id
                self._sync_message_id_holder()
                turn_id = _entry_turn_id(raw_entry)
                if turn_id is not None:
                    self._turn_client_message_ids[turn_id] = client_message_id
            message_id = self._effect_message_id(raw_entry)
            subagent = _terminal_subagent_effect(raw_entry)
            if subagent is not None:
                effect_id, operation, outcome, profile_source = subagent
                if self._session_telemetry.claim(effect_id):
                    self._session_telemetry.record_subagent_tool_call_finished(
                        operation=operation,
                        outcome=outcome,
                        profile_source=profile_source,
                        message_id=message_id,
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
                message_id=message_id,
                decision=tool.decision,
                approval_type=tool.approval_type,
                approval_source=tool.approval_source,
            )

    def _effect_message_id(self, raw_entry: object) -> str | None:
        """Attribute an effect to its turn's user message.

        Turns learned on an earlier snapshot win (a multi-iteration turn's
        effects usually reconcile on pages that no longer carry its user
        entry); unknown turns fall back to the last user entry seen in the
        current page, which is in-order correct.
        """
        turn_id = _entry_turn_id(raw_entry)
        if turn_id is not None:
            return self._turn_client_message_ids.get(
                turn_id, self._last_client_message_id
            )
        return self._last_client_message_id

    def _sync_message_id_holder(self) -> None:
        """Propagate the current message id to the attribution closure's holder.

        The completion attribution reads the holder live on each provider call,
        so the request metadata carries the same message id the tool and request
        telemetry events do.
        """
        self._message_id_holder[0] = self._last_client_message_id

    def _set_client_message_id(self, message_id: str | None) -> None:
        """Set the current turn's message id before the runtime drives actions.

        ``start_turn`` and ``steer_turn`` receive the client message id
        synchronously and the harness session schedules the runtime work as a
        task, so setting the holder here guarantees the provider request
        metadata reads it on the first completion — before any snapshot
        reconciliation could race it.
        """
        self._last_client_message_id = message_id
        self._sync_message_id_holder()

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
        ``_record_tool_telemetry`` runs before this method in ``_updated_state``,
        so ``_last_client_message_id`` already reflects the current snapshot's
        user entry.
        """
        for payload in self._context.request_sent.drain():
            self._session_telemetry.record_request_sent(
                model=payload.model,
                nb_context_chars=payload.nb_context_chars,
                nb_context_messages=payload.nb_context_messages,
                nb_prompt_chars=payload.nb_prompt_chars,
                call_type=request_call_type(payload.purpose, payload.iteration),
                message_id=self._last_client_message_id,
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

    async def _consumed_classification_event(self, event: Mapping[str, object]) -> bool:
        """Intercept a smart-approve classification: forward it to analytics, not the UI.

        Returns whether the event was a classification (and therefore consumed).
        """
        if event.get("type") != CLASSIFICATION_EVENT_TYPE:
            return False
        self._emit_classification_telemetry(event)
        watermark = event.get("eventId")
        if isinstance(watermark, int):
            await self._mark_harness_event_observed(watermark)
        return True

    def _emit_classification_telemetry(self, event: Mapping[str, object]) -> None:
        """Forward one harness smart-approve classification to product analytics."""
        keys = (
            "tool_name",
            "verdict",
            "tier",
            "reason",
            "outcome",
            "escalated",
            "latency_ms",
            "classifier_model",
            "classifier_prompt_version",
            "risk_level",
            "user_authorization",
            "approval_grant",
            "turn_id",
            "call_id",
        )
        properties = {key: event[key] for key in keys if event.get(key) is not None}
        self._telemetry.send_telemetry_event("vibe.tool_classification", properties)

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
        self, event: Mapping[str, object], *, include_history: bool = True
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
            if not include_history:
                return [SessionBackendEvent(event=CallbackRequested(callback))]
            self._event_id += 1
            return [
                _event_envelope(HistoryEntryAdded(callback), self._event_id),
                SessionBackendEvent(event=CallbackRequested(callback)),
            ]
        previous_callback = self._open_callbacks.pop(callback_key, callback)
        if not include_history:
            return []
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

# Harness completion-failure codes, in the vocabulary clients are given. A code
# left unmapped reaches them as a string no `TurnErrorCode` branch matches, so
# the turn is treated as an unknown failure.
_HARNESS_ERROR_CODES: Final[dict[str, TurnErrorCode]] = {
    "images_not_supported": TurnErrorCode.IMAGES_NOT_SUPPORTED,
    "model_stream_failed": TurnErrorCode.BACKEND_ERROR,
    "model_unauthorized": TurnErrorCode.INVALID_API_KEY,
}


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
    decision: Literal["execute", "skip"] | None
    approval_type: Literal["always", "never", "ask"] | None
    approval_source: Literal["config", "smart", "user", "bypass", "never"] | None


def _effect_file_extension(path: object) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    suffix = Path(path).suffix.lower()
    return suffix or None


def _entry_turn_id(entry: object) -> str | None:
    """Read the turn id off any history entry, None when absent."""
    if not isinstance(entry, Mapping):
        return None
    turn_id = entry.get("turnId")
    return turn_id if isinstance(turn_id, str) else None


def _entry_client_message_id(entry: object) -> str | None:
    """Read the public message id off a user history entry.

    The projection writes user entries with ``id = client_message_id or
    entry_id`` (content-block meta is stripped before entries reach the
    adapter), and the app-server reports that same value as the message id,
    so the entry id is the attribution source.
    """
    if (
        not isinstance(entry, Mapping)
        or entry.get("type") != "message"
        or entry.get("role") != "user"
    ):
        return None
    entry_id = entry.get("id")
    return entry_id if isinstance(entry_id, str) and entry_id else None


def _terminal_tool_effect(  # noqa: PLR0914
    entry: object,
) -> _ToolCallTelemetry | None:
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
    raw_decision = state.get("decision")
    decision = raw_decision if isinstance(raw_decision, str) else None
    raw_approval_type = state.get("approvalType")
    approval_type = raw_approval_type if isinstance(raw_approval_type, str) else None
    raw_approval_source = state.get("approvalSource")
    approval_source = (
        raw_approval_source if isinstance(raw_approval_source, str) else None
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
        decision=cast(Literal["execute", "skip"] | None, decision),
        approval_type=cast(Literal["always", "never", "ask"] | None, approval_type),
        approval_source=cast(
            Literal["config", "smart", "user", "bypass", "never"] | None,
            approval_source,
        ),
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


async def _config_read_response(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    harness_files: HarnessFilesManager,
) -> ConfigReadResponse:
    config = orchestrator.config
    skills = SkillManager(
        config_getter=lambda: config, harness_files=harness_files
    ).available_skills
    return ConfigReadResponse(
        config=project_config_view(
            config,
            active_model_pinned=active_model_is_pinned(orchestrator),
            image_fallback=True,
        ),
        skills_count=sum(
            1 for skill in skills.values() if skill.source is not SkillSource.BUILTIN
        ),
        hooks_count=len(
            (
                await asyncio.to_thread(load_hooks_from_fs, harness_files=harness_files)
            ).hooks
        ),
        mcp_servers_total=len(config.mcp_servers),
        mcp_servers_enabled=sum(
            1 for server in config.mcp_servers if not server.disabled
        ),
    )


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


def _harness_deferred_content_block(
    block: SessionContentBlock,
) -> harness_session_protocol.ContentBlock:
    match block:
        case SessionTextContentBlock():
            return harness_session_protocol.TextContentBlock(text=block.text)
        case SessionImageContentBlock():
            return harness_session_protocol.ImageContentBlock(
                uri=block.uri, media_type=block.media_type, alt_text=block.alt_text
            )
        case SessionResourceLinkContentBlock():
            return harness_session_protocol.ResourceLinkContentBlock(
                uri=block.uri,
                name=block.name,
                title=block.title,
                description=block.description,
                media_type=block.media_type,
                size=block.size,
            )
        case SessionEmbeddedResourceContentBlock():
            return harness_session_protocol.EmbeddedResourceContentBlock(
                uri=block.uri,
                media_type=block.media_type,
                text=block.text,
                blob=block.blob,
            )
        case _:
            assert_never(block)


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


@dataclass(frozen=True, slots=True)
class _SessionPins:
    """What a session runs, in the ``PublicSession`` fields that already say so.

    Picking the default back writes an empty pin rather than clearing it, so
    every value here is normalised to ``None`` for "follows the default".
    """

    model: str | None = None
    reasoning_effort: str | None = None
    agent: AgentSummary | None = None


def _pinned_agent(agents: AgentManager, name: str | None) -> AgentSummary | None:
    profile = None if name is None else agents.available_agents.get(name)
    return None if profile is None else project_agent_summary(profile)


async def _stored_session_pins(
    host: UnifiedHarnessSessionBackendHost, session_id: str, agents: AgentManager
) -> _SessionPins:
    """The pins of a session that is not loaded, read without resuming it."""
    model, agent_name, reasoning_effort = await asyncio.gather(
        _harness_call(host.session_pin(session_id, SessionPin.ACTIVE_MODEL)),
        _harness_call(host.session_pin(session_id, SessionPin.AGENT_NAME)),
        _harness_call(host.session_pin(session_id, SessionPin.REASONING_EFFORT)),
    )
    return _SessionPins(
        model=model or None,
        reasoning_effort=reasoning_effort or None,
        agent=_pinned_agent(agents, agent_name),
    )


_NO_SESSION_PINS = _SessionPins()


def _read_response(
    snapshot: HarnessSessionSnapshot,
    cwd: str | None,
    *,
    event_id: int | None = None,
    metadata: SessionMetadata | None = None,
    pins: _SessionPins = _NO_SESSION_PINS,
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
    harness_retrying = getattr(snapshot.state, "retrying", None)
    return SessionReadResponse(
        state=PublicSessionState(
            event_id=last_event_id,
            session=_public_session(
                snapshot.state.session, cwd, metadata=metadata, pins=pins
            ),
            history=history,
            turns=(
                [_public_turn(snapshot.state.latest_turn)]
                if snapshot.state.latest_turn is not None
                else []
            ),
            turn_queue=_public_turn_queue(snapshot.state.turn_queue),
            retrying=(
                PublicRetryState(
                    turn_id=harness_retrying.turn_id,
                    category=PublicRetryCategory(harness_retrying.category),
                    detail=harness_retrying.detail,
                )
                if harness_retrying is not None
                else None
            ),
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


def _vibe_token_usage(usage: HarnessTokenUsage | None) -> VibeTokenUsage | None:
    """Map harness token usage into the client model, tolerating field skew.

    The harness runtime upgrades independently of the client — a self-hosted
    install or a ``--with-editable`` dev harness can run ahead and add usage
    fields (e.g. ``cachedInputTokens``). This reads harness output, so it
    ignores unknown fields per ADR 0014 rather than letting one new field fail
    the whole session read. Known fields still validate strictly.
    """
    if usage is None:
        return None
    return validate_backend_wire(
        VibeTokenUsage, usage.model_dump(mode="json", by_alias=True)
    )


def _public_session(
    session: HarnessPublicSession,
    cwd: str | None,
    *,
    harness: Literal["legacy", "unified"] = "unified",
    metadata: SessionMetadata | None = None,
    pins: _SessionPins = _NO_SESSION_PINS,
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
    elif getattr(status, "type", None) == "archived":
        public_status = VibeArchivedSessionStatus()
    else:
        public_status = VibeIdleSessionStatus()
    token_usage = _vibe_token_usage(session.token_usage)
    context_usage = _vibe_token_usage(session.context_usage)
    return PublicSession(
        id=session.id,
        root_session_id=session.root_session_id,
        parent_session_id=session.parent_session_id,
        title=session.title,
        preview=session.preview,
        status=public_status,
        created_at=session.created_at,
        updated_at=session.updated_at,
        bumped_at=_metadata_bumped_at_ms(metadata),
        pinned_at=_metadata_pinned_at_ms(metadata),
        cwd=cwd,
        model=pins.model,
        reasoning_effort=pins.reasoning_effort,
        agent=pins.agent,
        token_usage=token_usage,
        context_usage=context_usage,
        harness=harness,
    )


def _public_unified_sessions(
    storage_root: str, items: Sequence[HarnessSessionListItem]
) -> list[PublicSession]:
    # Without pins: a listing feeds a session list, not a composer, and reading
    # one costs a full Harness catalogue parse per row.
    return [
        _public_session(
            item.session,
            item.cwd,
            harness="unified",
            metadata=_read_unified_session_metadata(
                _unified_session_dir(storage_root, item.session.id)
            ),
        )
        for item in items
    ]


def _legacy_public_sessions(
    config: VibeConfigSchema, cwd: str | None
) -> list[PublicSession]:
    return [
        _legacy_public_session(session, config, harness="legacy")
        for session in list_local_resume_sessions(config, cwd)
    ]


def _legacy_public_session(
    session: ResumeSessionInfo,
    config: VibeConfigSchema,
    *,
    harness: Literal["legacy", "unified"],
) -> PublicSession:
    """Project a legacy ``ResumeSessionInfo`` into a ``PublicSession`` row.

    Legacy sessions store timestamps as ISO strings; ``PublicSession`` expects
    int milliseconds. ``time_ms`` parses the ISO format and falls back to
    ``now_ms()`` on parse failure, so a malformed timestamp never breaks the
    listing.
    """
    return PublicSession(
        id=session.session_id,
        root_session_id=None,
        parent_session_id=session.parent_session_id,
        title=session.title,
        # Reading the transcript for a preview no caller will show is the
        # legacy half's dominant cost, and every consumer renders
        # ``title or preview``. The legacy picker short-circuits the same way
        # in ``resume_sessions.session_latest_messages``.
        preview=(
            ""
            if session.title
            else SessionLoader.get_first_user_message(
                session.session_id, config.session_logging
            )
        ),
        status=VibeIdleSessionStatus(),
        created_at=time_ms(session.start_time or session.updated_at),
        updated_at=time_ms(session.updated_at),
        bumped_at=optional_time_ms(session.bumped_at),
        pinned_at=optional_time_ms(session.pinned_at),
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
                    bumped_at=metadata.bumped_at,
                    pinned_at=metadata.pinned_at,
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
    return validate_backend_wire(
        PublicTurnQueue, raw.model_dump(mode="json", by_alias=True)
    )


def _public_turn_error(error: object | None) -> PublicError | None:
    if error is None:
        return None
    raw_error = cast(Any, error)
    public_error = validate_backend_wire(
        PublicError, raw_error.model_dump(mode="json", by_alias=True)
    )
    # The Harness has its own error vocabulary; clients only know TurnErrorCode.
    if mapped := _HARNESS_ERROR_CODES.get(str(public_error.code)):
        public_error = public_error.model_copy(update={"code": mapped})
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


@dataclass(slots=True)
class _HostShell:
    """The Host's own side of a session whose event stream the Harness owns.

    ``pending`` counts what has been queued but not yet handed to the Client, so
    a request can wait for its own events to land before it answers.
    """

    controller: ShellController
    queue: asyncio.Queue[SessionBackendEvent] = field(default_factory=asyncio.Queue)
    pending: int = 0


class _ManualShellEffect:
    """The ``shell`` history entry behind one manual `!` command.

    ``EventProjector`` already owns the shape of an effect entry and the patches
    that advance it -- ``TurnController`` drives it exactly this way for the
    legacy backend. One projector per command is enough here, because a manual
    command is the only thing the Host ever writes into a Unified timeline.
    """

    def __init__(self, projector: EventProjector, entry_id: str) -> None:
        self._projector = projector
        self._entry_id = entry_id
        self._output: list[str] = []

    @property
    def output_text(self) -> str:
        return "".join(self._output)

    def start(self, detail: EffectDetail) -> HistoryEntryAdded:
        self._projector.start_effect(self._entry_id, title="shell", detail=detail)
        return HistoryEntryAdded(self._entry)

    def append_output(self, chunk: str) -> HistoryEntryUpdated:
        self._output.append(chunk)
        previous = self._entry
        return self._updated(
            previous, self._projector.append_effect_output(self._entry_id, chunk)
        )

    def complete(self, state: EffectState) -> HistoryEntryUpdated:
        previous = self._entry
        return self._updated(
            previous, self._projector.complete_effect(self._entry_id, state)
        )

    @property
    def _entry(self) -> PublicHistoryEntry:
        return self._projector.history[-1]

    def _updated(
        self, previous: PublicHistoryEntry, update: ProjectedUpdate
    ) -> HistoryEntryUpdated:
        params = cast(HistoryEntryUpdatedParams, update.params)
        return HistoryEntryUpdated(
            previous=previous, entry=self._entry, patch=params.patch
        )


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


def _retrying_event(session_id: str, retrying: PublicRetryState) -> SessionBackendEvent:
    params = TurnRetryingParams(
        session_id=session_id, category=retrying.category, detail=retrying.detail
    )
    return SessionBackendEvent(
        event=TurnRetrying(params),
        method="turn/retrying",
        params=params,
        session_id=session_id,
    )


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
        method="turn/queueUpdated",
        params=params,
        session_id=session_id,
        event_id=event_id,
    )


def _mcp_tool_filter(config: VibeConfigSchema) -> MCPToolFilter | None:
    # The same global globs the connector catalogue reads.
    enabled = tuple(config.enabled_tools)
    disabled = tuple(config.disabled_tools)
    if not enabled and not disabled:
        return None
    return MCPToolFilter(enabled_globs=enabled, disabled_globs=disabled)


def _harness_mcp_catalog(
    catalog: ResolvedMCPCatalog, tool_filter: MCPToolFilter | None
) -> HarnessResolvedMCPCatalog:
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
        tool_filter=tool_filter,
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
        self._plugin_server_names: frozenset[str] = frozenset()

    def update_plugin_server_names(self, catalog: ResolvedMCPCatalog) -> None:
        from vibe.app_server._runtime import plugin_owned_names

        self._plugin_server_names = plugin_owned_names(catalog)

    async def resolve(
        self, reference: HarnessMCPAuthorizationRef
    ) -> HarnessMCPAuthorizationSnapshot | HarnessMCPAuthorizationRequired:
        result = await self._provider.resolve(
            _app_authorization_ref(reference, self._plugin_server_names)
        )
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
            _app_authorization_ref(reference, self._plugin_server_names),
            observed_connection_revision=observed_connection_revision,
            reason=cast(Any, reason),
        )
        return _harness_authorization_result(result)


def _app_authorization_ref(
    reference: HarnessMCPAuthorizationRef,
    plugin_server_names: frozenset[str] = frozenset(),
) -> MCPAuthorizationRef:
    owner: MCPCatalogOwner = (
        "plugin" if reference.server_name in plugin_server_names else "config"
    )
    return MCPAuthorizationRef(
        server_name=reference.server_name,
        server_fingerprint=reference.server_fingerprint,
        kind=reference.kind,
        descriptor_revision=reference.descriptor_revision,
        owner=owner,
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


# Vibe publishes an MCP tool as ``{alias}_{remote name}`` and a connector tool as
# ``connector_{alias}_{remote name}``, both halves raw, and configures permissions
# under that name. The Runtime routes the same tool by identifiers it normalized
# out of those halves, so the route cannot be turned back into the name -- only
# the snapshot that built it holds both, and only for as long as it is accepted.
def _published_mcp_names(snapshot: HarnessMCPRouteSnapshot) -> dict[str, str]:
    return {
        f"{tool.group_name}.{tool.programmatic_name}": (
            f"{tool.server_name}_{tool.remote_name}"
        )
        for group in snapshot.groups
        for tool in group.tools
    }


def _published_connector_names(
    snapshot: HarnessConnectorRouteSnapshot,
) -> dict[str, str]:
    return {
        f"{tool.group_name}.{tool.programmatic_name}": (
            f"connector_{tool.alias}_{tool.remote_name}"
        )
        for group in snapshot.groups
        for tool in group.tools
    }


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
