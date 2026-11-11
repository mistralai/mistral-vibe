"""Translation between the Vibe app-server port and the Unified Harness."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
import contextlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import cache, partial
import getpass
from http import HTTPStatus
import json
import os
from pathlib import Path
import sys
import time
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Literal,
    Never,
    Protocol,
    assert_never,
    cast,
    get_args,
)
from uuid import uuid4

from pydantic import JsonValue, ValidationError

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
    TurnQueueEditAbandonParams as HarnessTurnQueueEditAbandonParams,
    TurnQueueEditCancelParams as HarnessTurnQueueEditCancelParams,
    TurnQueueEditSaveParams as HarnessTurnQueueEditSaveParams,
    TurnQueueEditStartParams as HarnessTurnQueueEditStartParams,
    TurnQueueReadParams as HarnessTurnQueueReadParams,
    TurnQueueRemoveParams as HarnessTurnQueueRemoveParams,
    TurnQueueReplaceParams as HarnessTurnQueueReplaceParams,
    TurnQueueResumeParams as HarnessTurnQueueResumeParams,
    TurnQueueUpdatedEvent as HarnessTurnQueueUpdatedEvent,
)
from mistralai_vibe_local_harness.vibe import (
    CLASSIFICATION_EVENT_TYPE,
    MCP_DISCOVERY_PENDING,
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
    HarnessConfigurationRejectedError,
    HarnessNotImplementedError,
    HarnessReplayDivergenceError,
    HarnessSessionBusyError,
    HarnessSessionError,
    HarnessSessionForkResult,
    HarnessSessionListItem,
    HarnessSessionNotFoundError,
    HarnessSessionReadResult,
    HarnessSessionSubscription,
    LegacySourceLoader,
    LegacySourceResolver,
    LocalRuntimeAdapterConfig,
    MalformedToolCallsTelemetry,
    MCPAuthorizationProvider as HarnessMCPAuthorizationProvider,
    MCPAuthorizationRef as HarnessMCPAuthorizationRef,
    MCPAuthorizationRequired as HarnessMCPAuthorizationRequired,
    MCPAuthorizationSnapshot as HarnessMCPAuthorizationSnapshot,
    MCPDescriptorCachePolicy as HarnessMCPDescriptorCachePolicy,
    MCPDiscoveryFlights as HarnessMCPDiscoveryFlights,
    MCPHTTPTransportPolicy as HarnessMCPHTTPTransportPolicy,
    MCPRouteSnapshot as HarnessMCPRouteSnapshot,
    MCPToolFilter,
    PreparedRuntimeInput as HarnessPreparedRuntimeInput,
    ProviderCredentialProvider,
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
    SessionConfig,
    SessionWorkspace,
    SharedCompletionBudget,
    TurnMentionStats as HarnessTurnMentionStats,
    TurnStartRequest as HarnessTurnStartRequest,
    UnifiedHarnessSessionBackend,
    UnifiedHarnessSessionBackendHost,
)
from mistralai_vibe_local_harness.vibe._foreign_hooks import (
    HookProcess,
    HostHookResult,
    run_host_hook,
)
from mistralai_vibe_local_harness.vibe._mcp_models import (
    MCPToolDescriptor as HarnessMCPToolDescriptor,
)
from mistralai_vibe_local_harness.vibe._mcp_naming import build_route_snapshot
from mistralai_vibe_local_harness.vibe._storage import (
    SessionLease,
    SessionPin,
    sha256_json,
)
from mistralai_vibe_local_harness.vibe._subagents._models import ChildSessionRecord
from mistralai_vibe_local_harness.vibe.completion_profile import (
    IMAGES_REPLACED,
    PROCESS_CATALOGUE_CACHE,
    CompletionWarning,
    RuntimeWarningSink,
)
from mistralai_vibe_local_harness.vibe.plugins import SessionPluginBinding
from vibe.app_server._account import AccountController, AccountGateway
from vibe.app_server._admin_config import (
    OrgConfigReport,
    OrgConfigReportSlot,
    OrgConfigSource,
    load_admin_layer,
    report_admin_config_outcome,
)
from vibe.app_server._approval_permissions import approval_grant_permissions
from vibe.app_server._auto_approve_option import resolve_start_auto_approve
from vibe.app_server._cached_layers import oidc_tenant_unresolved
from vibe.app_server._client_provided_tools import ClientToolCaller, ClientToolExecutor
from vibe.app_server._completion_attribution import (
    CompletionAffinityHolder,
    CompletionAttributionHolder,
    CompletionAttributionSource,
    build_completion_attribution,
    request_call_type,
)
from vibe.app_server._completion_facts import (
    CatalogueSnapshot,
    ModelFactsError,
    SavedCatalogue,
    compile_agent_route_facts,
)
from vibe.app_server._config_errors import USER_CONFIG_ERRORS, user_config_error
from vibe.app_server._config_introspect import (
    HIDDEN_SETTINGS,
    POPULAR_SETTINGS,
    build_field_wires,
    collect_layer_values,
    effective_config_response,
)
from vibe.app_server._config_paths import model_thinking_path
from vibe.app_server._config_write import (
    config_write_ops_to_patches,
    config_write_targets,
    model_after_write,
    model_config_write_ops,
    models_a_write_changes,
)
from vibe.app_server._connector_mcp_apps import (
    SessionMcpApps,
    connector_effect_tool_name,
)
from vibe.app_server._context_breakdown import ModelRequestLog, build_context_breakdown
from vibe.app_server._continue_target import (
    NOTHING_TO_CONTINUE,
    TerminalSeat,
    continue_target,
)
from vibe.app_server._deferred_config import DeferredConfiguration
from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._effect_output_cap import capped_effect_entry
from vibe.app_server._enter_worktree import (
    ENTER_WORKTREE_TOOL_NAME,
    EnteredWorktree,
    EnterWorktreeArgs,
    EnterWorktreeError,
)
from vibe.app_server._error_message_cap import bounded_error_message
from vibe.app_server._experiments_service import (
    ExperimentsEvalFailedError,
    ExperimentsKey,
    ExperimentsService,
)
from vibe.app_server._feedback import FeedbackController
from vibe.app_server._host import config_schema_response
from vibe.app_server._host_hooks import (
    HOOK_MESSAGE_CODE,
    HostHook,
    HostHooks,
    Notification,
    SessionEndReason,
    SessionHooks,
    SessionStartContexts,
    SessionStartSource,
    callback_notification,
    turn_notification,
)
from vibe.app_server._identity import (
    IdentityController,
    IdentityGateway,
    PlatformCredentialProviderFactory,
)
from vibe.app_server._launch_context import client_launch_context
from vibe.app_server._legacy_import import LegacySessionStore
from vibe.app_server._local_projects import (
    LocalProject,
    LocalProjectStore,
    LocalProjectStoreUnreadable,
    session_project,
)
from vibe.app_server._loop_prompt import (
    is_loop_prompt,
    prepare_loop_queue,
    prepare_loop_turn,
    project_loop_preview,
    project_loop_queue,
    scheduled_loop_display,
)
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._model import ProtocolModel, validate_backend_wire, validate_wire
from vibe.app_server._model_catalog_service import ModelCatalogKey, ModelCatalogService
from vibe.app_server._narration import NarrationContext, NarrationService
from vibe.app_server._open_sessions import OpenSessions
from vibe.app_server._org_config_service import OrgConfigKey, OrgConfigService
from vibe.app_server._patch import apply_json_patch, make_json_patch
from vibe.app_server._plugin_mcp import PluginMCPCatalog
from vibe.app_server._plugins import (
    PLUGIN_HOOKS_RELOAD_NOTICE,
    PluginReloadUnavailableError,
    SessionPlugins,
    UnifiedPluginProvider,
    pinned_plugin_hook,
    plugin_change_context,
    plugin_hook_signature,
    plugin_reload_notices,
)
from vibe.app_server._projection import (
    project_agent_summary,
    project_config_view,
    project_debug_logs,
    project_installed_skill_summaries,
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
from vibe.app_server._provider_auth import read_provider_auth
from vibe.app_server._pull_request_cache import Read, ReadFailure, pull_request_cache
from vibe.app_server._registry_skill_sync import RegistrySkillSync
from vibe.app_server._remote_follower import FollowedValue, RemoteFollower
from vibe.app_server._review import ReviewRequestHandler
from vibe.app_server._root_session import rebind_history_with_checkpoint
from vibe.app_server._session_artifacts import (
    ArtifactRequestHandler,
    SessionArtifactService,
    to_public_artifact,
    to_public_worktree,
)
from vibe.app_server._session_backend_port import (
    ConnectorAuthRequest,
    ConnectorConnectRequest,
    MCPAuthorizationProvider,
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationSnapshot,
    MCPCatalogOwner,
    ResolvedConnector,
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
from vibe.app_server._session_backend_services import (
    SessionBackendPlatformCredentials,
    SessionBackendServices,
)
from vibe.app_server._session_commands import (
    Accepted,
    CommandResult,
    DirectCommands,
    SessionCommands,
)
from vibe.app_server._session_lease_poll import LeaseHolder, watch_session_leases
from vibe.app_server._session_message import (
    handed_task_text,
    is_session_message,
    project_session_message_preview,
    public_session_message_content,
    session_message_display,
    session_message_header,
)
from vibe.app_server._session_model import (
    active_model_is_pinned,
    active_model_override_write_requested,
    clear_session_active_model_override,
    model_thinking_is_session_writable,
    set_session_active_model_override,
    set_session_reasoning_effort_override,
    with_session_active_model_write,
)
from vibe.app_server._session_peers import SessionPeerError, SessionPeers, TurnStarts
from vibe.app_server._session_project_match import ProjectFolders
from vibe.app_server._session_seen_state import (
    SessionSeenState,
    should_mark_session_unseen,
)
from vibe.app_server._session_spawn import (
    SessionSettings,
    SessionSpawner,
    SessionSpawnOrigin,
    session_settings,
    spawn_models,
)
from vibe.app_server._shell import (
    ShellConflictError,
    ShellController,
    capped_shell_effect_state,
    manual_shell_context,
    manual_shell_output_limit,
    resolve_workspace_cwd,
    shell_effect_cancelled,
    shell_effect_detail,
    shell_effect_error,
    shell_effect_state,
)
from vibe.app_server._skill_invocation import (
    invoked_skill_names,
    invoked_skill_payloads,
    mentioned_skill_names,
    public_session_preview,
    skill_invocation_display_names,
    with_skill_invocation_display,
    without_skill_invocation_display,
)
from vibe.app_server._skill_store_cleanup import SkillStoreCleanup
from vibe.app_server._skill_watch import (
    SkillWatchRoots,
    skill_watch_roots,
    watch_skill_changes,
)
from vibe.app_server._skills import (
    RegistryVersions,
    host_skills,
    installed_session_skills,
    resolve_session_skills,
    resolved_registry_versions,
)
from vibe.app_server._skills_service import SkillsController
from vibe.app_server._state import history_page
from vibe.app_server._subagent_runs import (
    SubagentPricing,
    SubagentSteps,
    read_subagent_runs,
)
from vibe.app_server._turn_input import (
    session_content_blocks_from_vibe,
    vibe_content_blocks,
)
from vibe.app_server._unified_harness_snapshots import (
    RewindFileSnapshots,
    SessionRewindFileSnapshots,
    UnavailableRewindFileSnapshots,
)
from vibe.app_server._unified_history_projection import (
    normalize_effect_output,
    project_history_entries,
    project_history_entry,
    project_raw_history_entry,
    with_approval_display,
    with_available_path_scopes,
)
from vibe.app_server._unified_permissions import (
    ProvidedToolNames,
    UnifiedPermissionResolver,
)
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    ScheduledPrompt,
    UnifiedScheduledLoops,
    read_stored_loops,
)
from vibe.app_server._utils import (
    _REFUSED_CREDENTIAL_STATUSES,
    iso_from_time_ms,
    now_ms,
    optional_time_ms,
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
from vibe.app_server._worktree_effects import (
    WorktreeEffect,
    WorktreeProgress,
    WorktreeProgressFeed,
    publishing_progress,
)
from vibe.app_server._worktree_housekeeping import WorktreeHousekeeping
from vibe.app_server._worktree_session import SessionWorktrees, WorktreeResolution
from vibe.app_server.config import ProxySettingsView
from vibe.app_server.connector_catalog import (
    CONNECTOR_CATALOG_FETCH_TIMEOUT_S,
    ConnectorCatalogError,
    ConnectorCatalogService,
    FollowedCatalog,
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
    BudgetStopReason,
    CompletedEffectState,
    ConnectorCounts,
    ContentBlock,
    EffectDetail,
    EffectState,
    FailedEffectState,
    FailedSessionStatus as VibeFailedSessionStatus,
    GenericEffectDetail,
    IdleSessionStatus as VibeIdleSessionStatus,
    ImageContentBlock,
    JsonPatchOperation,
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
    PluginInfo,
    PreparedPrompt,
    PublicBackgroundProcess,
    PublicCallbackEntry,
    PublicCheckpointEntry,
    PublicChildSession,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicRetryCategory,
    PublicRetryState,
    PublicSession,
    PublicSessionProject,
    PublicSessionState,
    PublicSessionWorktree,
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
    UserInputCallbackDetail,
    WorktreeEffectDetail,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    INTEGRATIONS_UNAVAILABLE_WARNING,
    PEER_REFUSAL_ARCHIVED,
    PEER_REFUSAL_BUSY,
    PEER_REFUSAL_PAUSED,
    AccountReadParams,
    AccountReadResponse,
    AgentInstallParams,
    AgentsListResponse,
    AgentSwitchParams,
    ArtifactCreatedParams,
    ArtifactsListParams,
    ArtifactsListResponse,
    ArtifactsMarkViewedParams,
    ArtifactsSyncParams,
    ArtifactUpdatedParams,
    BackgroundProcessOutputAvailableResponse,
    BackgroundProcessOutputParams,
    BackgroundProcessOutputResponse,
    BackgroundProcessOutputUnavailableResponse,
    BackgroundProcessStopParams,
    BackgroundProcessStopResponse,
    CallbackResult,
    CallbackResultError,
    CallbackResultParams,
    CallbackResultResponse,
    ChildSessionUpdatedParams,
    ConfigEffectiveReadParams,
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
    ContextBreakdown,
    ContextInjectParams,
    ContextInjectResponse,
    DiagnosticsLogsReadParams,
    DiagnosticsLogsReadResponse,
    EmptyResponse,
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
    PeerRefusal,
    PluginInfoParams,
    PluginInfoResponse,
    PluginReloadParams,
    PluginReloadResponse,
    ProtocolErrorCode,
    ProviderAuthReadParams,
    ProviderAuthReadResponse,
    PublicSessionArtifact,
    RuntimeMutationResponse,
    RuntimeMutationStatus,
    RuntimeReadParams,
    RuntimeReadResponse,
    RuntimeSnapshot,
    RuntimeUpdatedParams,
    ServerWarningParams,
    SessionArchiveParams,
    SessionArchiveResponse,
    SessionCompactParams,
    SessionCompactResponse,
    SessionContentBlock,
    SessionContextReadParams,
    SessionContextReadResponse,
    SessionContinueParams,
    SessionDeleteParams,
    SessionEmbeddedResourceContentBlock,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryClearParams,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionImageContentBlock,
    SessionKind,
    SessionLeaseHolder,
    SessionListParams,
    SessionListResponse,
    SessionLogReadParams,
    SessionLogReadResponse,
    SessionMarkAsSeenParams,
    SessionMessageSender,
    SessionMoveParams,
    SessionMoveResponse,
    SessionMoveTarget,
    SessionMoveToLocalProject,
    SessionOpenParams,
    SessionOptions,
    SessionPinParams,
    SessionPinResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionReadyReadResponse,
    SessionReadyWaitParams,
    SessionReadyWaitResponse,
    SessionRelocateParams,
    SessionRelocateResponse,
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
    SessionSpawnCheckout,
    SessionSpawnDirectory,
    SessionSpawnManagedWorktree,
    SessionStartParams,
    SessionStopParams,
    SessionStopResponse,
    SessionSubagentsInterruptParams,
    SessionSubagentsInterruptResponse,
    SessionSubagentsReadParams,
    SessionSubagentsReadResponse,
    SessionSubagentsWaitParams,
    SessionSubagentsWaitResponse,
    SessionTextContentBlock,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    SessionUpdatedParams,
    ShellRunParams,
    ShellRunResponse,
    StartedSessionsArchived,
    StartedSessionSkipped,
    StartedSessionSkipReason,
    StatsUpdatedParams,
    SubagentInterruptOutcome,
    SubagentInterruptResult,
    SubagentRef,
    TelemetryRecordParams,
    TurnCompletedParams,
    TurnContextInputEntry,
    TurnEnqueueParams,
    TurnEnqueueResponse,
    TurnInterruptParams,
    TurnInterruptResponse,
    TurnQueueEditAbandonParams,
    TurnQueueEditAbandonResponse,
    TurnQueueEditCancelParams,
    TurnQueueEditCancelResponse,
    TurnQueueEditSaveParams,
    TurnQueueEditSaveResponse,
    TurnQueueEditStartParams,
    TurnQueueEditStartResponse,
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
    WorkspacePromptPrepareParams,
    WorkspacePromptPrepareResponse,
    WorkspaceTrustDecisionParams,
)
from vibe.core.agents.install import (
    AgentInstallError,
    plan_installed_agents_change,
    verify_installed_agents_change,
    with_agent_installed,
)
from vibe.core.agents.manager import FALLBACK_AGENTS, AgentManager, AgentSelectionError
from vibe.core.config import MCPServer, MissingAPIKeyError, VibeConfigSchema
from vibe.core.config._setting_keys import UnusedConfigKeysError
from vibe.core.config.admin_config import AdminConfigApplyResult, AdminConfigOutcome
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layer import ConfigStorageError, LayerImplementationError
from vibe.core.config.layers.admin import admin_layer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.model_catalog import (
    ModelCatalogLayer,
    set_catalog_and_reload,
)
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.models import ModelConfig
from vibe.core.config.orchestrator import ConfigOrchestrator, ConfigPatchValidationError
from vibe.core.config.vibe_schema import outside_workdir_denied
from vibe.core.experiments.active import ExperimentName, ExperimentSurface
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.session import resolve_plan_attributes
from vibe.core.experiments.wait import (
    ExperimentsWait,
    experiments_eval_runs,
    experiments_wait_needed,
)
from vibe.core.git.errors import GitError
from vibe.core.git.worktree import ManagedWorktree, PreparedWorktree, WorktreeRepository
from vibe.core.hooks.config import load_hooks_from_fs
from vibe.core.hooks.models import HookType
from vibe.core.identity_cache import IdentityCache
from vibe.core.log_reader import LogReader
from vibe.core.loop import LoopError
from vibe.core.model_catalog import (
    ModelCatalogResponse,
    ModelCatalogWait,
    model_catalog_wait_needed,
)
from vibe.core.proxy_setup import (
    SUPPORTED_PROXY_VARS,
    ProxySetupError,
    get_current_proxy_settings,
    set_proxy_var,
    unset_proxy_var,
)
from vibe.core.session.artifacts import (
    SessionArtifact,
    WorktreeArtifact,
    sanitize_artifacts_in_raw_metadata,
)
from vibe.core.session.metadata import persist_metadata
from vibe.core.session.saved_sessions import (
    delete_saved_session,
    update_saved_session_pin,
)
from vibe.core.session.session_loader import METADATA_FILENAME, SessionLoader
from vibe.core.skills.registry import refresh_registry_skills
from vibe.core.telemetry.send import (
    SubagentOperation,
    SubagentOutcome,
    SubagentProfileSource,
    TelemetryClient,
)
from vibe.core.telemetry.session import SessionTelemetry
from vibe.core.telemetry.types import AttachmentKind, LaunchContext
from vibe.core.tools.builtins.skill import already_loaded_message, skill_content_marker
from vibe.core.trusted_folders import TrustedFoldersManager, has_agents_md_file
from vibe.core.types import (
    ScheduledLoop as CoreScheduledLoop,
    SessionMetadata,
    SessionProject,
)
from vibe.core.utils.concurrency import cancel_pending
from vibe.observability.logging import logger
from vibe.observability.sentry import capture_cli_exception
from vibe.setup.auth.whoami import WhoAmICache, WhoAmIResult, resolve_user_plan
from vibe.user_content import UserDisplayContent
from vibe.utils import AgentEntrypoint
from vibe.utils.io import FileSignature, file_signature, read_safe
from vibe.utils.mcp import format_tool_display_description

if TYPE_CHECKING:
    from vibe.app_server._sandbox_skills import SandboxSessionSkills

    # The Session API path imports these where it uses them, so a Session on
    # the direct path does not pay for them at startup.
    from vibe.app_server._session_api_commands import SessionApiCommands
    from vibe.app_server._session_api_path import SessionApiPath


def _report(exc: BaseException, boundary: str) -> None:
    """Reports a swallowed failure that leaves session state inconsistent."""
    capture_cli_exception(exc, tags={"vibe_boundary": f"session.{boundary}"})


@dataclass(frozen=True, slots=True)
class UnifiedSessionSettings:
    """Session-local overrides that never reach a persisted config layer."""

    max_turns: int | None = None
    max_tokens: int | None = None
    # What ``plugin/reload`` re-pinned the session onto. ``None`` derives from
    # the resolve the context made at open.
    plugins: SessionPlugins | None = None
    # Whether the session runs on the Session API, fixed when it opens.
    # ``None`` reads the switch from the config. The path cannot change while
    # the session is open, so a switch turned off then (a rollback, say)
    # reaches only sessions opened later.
    session_api: bool | None = None


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
    registry_versions: RegistryVersions
    # What compiling the active route's completion profile had to guess.
    profile_warnings: tuple[CompletionWarning, ...] = ()
    # The catalogue's answer for the active model, which the session saves.
    completion_catalogue: CatalogueSnapshot | None = None

    # The Vibe attribution the runtime reads. Scoped to the derivation, not the
    # context, so a forked session cannot rebind the source session's. The
    # adapter binds it when it adopts the derivation.
    completion_attribution: CompletionAttributionHolder = field(
        default_factory=lambda: CompletionAttributionHolder()
    )
    # Cache routing is deliberately separate from request metadata: forks keep
    # their child session id in telemetry while sharing their root's affinity.
    completion_affinity: CompletionAffinityHolder = field(
        default_factory=lambda: CompletionAffinityHolder()
    )

    def __post_init__(self) -> None:
        # The runtime reaches request metadata and cache affinity through
        # ``adapter_config``, so point it at this derivation's holders here.
        object.__setattr__(
            self,
            "adapter_config",
            replace(
                self.adapter_config,
                completion_metadata=self.completion_attribution.metadata,
                affinity_id=self.completion_affinity.affinity_id,
            ),
        )

    @property
    def session_config(self) -> SessionConfig:
        return SessionConfig(core=self.core_config, local=self.adapter_config)


type UnifiedRuntimeDeriver = Callable[
    [UnifiedSessionSettings], UnifiedRuntimeDerivation
]


@dataclass(frozen=True, slots=True)
class UnifiedConfigCandidate:
    """A config not installed on the session yet, with the agents read
    against it: what a commit derives from, start to finish.
    """

    orchestrator: ConfigOrchestrator[VibeConfigSchema]
    agents: AgentManager


@dataclass(frozen=True, slots=True)
class _OrgConfigCandidate:
    result: AdminConfigApplyResult
    admin: dict[str, Any]
    agents: AgentManager
    left_mode: str | None


class UnifiedConfigPreflight(Protocol):
    """Answers the candidate's derivation when it derived one, so the adapter
    can ask the live Session whether its context fits the model the candidate
    runs. ``candidate`` derives everything from a config not installed yet;
    without it only ``config`` differs from the session's.
    """

    def __call__(
        self,
        config: VibeConfigSchema,
        settings: UnifiedSessionSettings,
        candidate: UnifiedConfigCandidate | None = None,
        /,
    ) -> Awaitable[UnifiedRuntimeDerivation | None]: ...


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


class MalformedToolCallsQueue:
    """Session-scoped buffer for the runtime's malformed tool call reports.

    Same contract as ``RequestSentQueue``: the runtime's
    ``malformed_tool_calls_sink`` appends from the completion's context, and the
    adapter drains on the app loop and forwards ``vibe.tool_call.detected``.
    """

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        self._pending: list[MalformedToolCallsTelemetry] = []

    def record(self, payload: MalformedToolCallsTelemetry) -> None:
        self._pending.append(payload)

    def drain(self) -> list[MalformedToolCallsTelemetry]:
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

    __slots__ = ("_open", "_task")

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._open = asyncio.Event()
        self._open.set()

    def track(self, task: asyncio.Task[None]) -> None:
        self._task = task

    @contextlib.contextmanager
    def held(self) -> Iterator[None]:
        """Closed while a move cancels the eval and starts it again."""
        self._open.clear()
        try:
            yield
        finally:
            self._open.set()

    async def wait(self) -> None:
        await self._open.wait()
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


class _CandidateRefusedError(Exception):
    """The session cannot run under a candidate config: it never reaches Core.

    ``shared`` when the config itself is at fault, so no session on its
    context can run it.
    """

    def __init__(
        self,
        message: str,
        *,
        shared: bool,
        result: AdminConfigApplyResult | None = None,
    ) -> None:
        super().__init__(message)
        self.shared = shared
        self.result = result


class _NoOrgConfig:
    """What a session follows once no credential names an org: no org config."""

    def held(self) -> str:
        return ""

    def check(self) -> None:
        return None


@dataclass(slots=True)
class OrgConfigKeySlot:
    key: OrgConfigKey | None = None


@dataclass(slots=True)
class CompletionBudgetSlot:
    """The run's cumulative completion budget, read by every derivation.

    A context rebuilt for a live session adopts the slot of the one it replaces,
    so spend recorded before the rebuild still counts and the root session and
    its subagents keep one authority.
    """

    budget: SharedCompletionBudget | None = None

    def adopt(self, other: CompletionBudgetSlot) -> None:
        self.budget = other.budget


@dataclass(slots=True)
class WorkspaceSlot:
    """Where the session runs now, which ``enter_worktree`` moves mid-session.

    ``entered`` are the worktrees the session made its own: they join its
    listed roots, where a checkout it left does not, so a checkout whose
    project files are untrusted is never read through a listed root.
    """

    workspace: SessionWorkspace
    entered: tuple[Path, ...] = ()

    @property
    def moved(self) -> bool:
        return bool(self.entered)


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
    # Resolved once by an async call at session open, so they cannot be
    # recomputed by the synchronous ``derive``. A ``plugin/reload`` hands its
    # re-pinned set to ``derive`` through ``UnifiedSessionSettings.plugins``.
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
    mcp_discovery_flights: HarnessMCPDiscoveryFlights
    mcp_enable_system_trust_store: bool
    org_config_service: OrgConfigService
    experiments_service: ExperimentsService
    model_catalog_service: ModelCatalogService
    skill_store: SkillStoreCleanup | None = None
    """None in a process without a background worker."""
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
    preflight: UnifiedConfigPreflight | None = None
    # Compiled user hooks for this session: Core bindings + Runtime handlers. The
    # Host registers the handlers on every lifecycle op (so a hook's command is
    # re-read live) and the bindings are supplied on start (then persisted, so the
    # set that fires stays frozen for the session's life).
    hooks: CompiledHooks = field(default_factory=CompiledHooks)
    # How many of the session's ``hooks.toml`` post_agent hooks declare a ``match``,
    # and how many of those select the main agent; reported on ``vibe.new_session``.
    post_agent_scoped_hooks_count: int = 0
    post_agent_main_hooks_count: int = 0
    # How many hooks of each type the session runs, reported on ``vibe.new_session``.
    hook_type_counts: Mapping[HookType, int] = field(default_factory=dict)
    # The hooks the adapter fires itself (notification, session_start, session_end),
    # and the process-wide state they share: the background hooks a closing app
    # server waits for, and the session_start context owed to each session.
    session_hooks: SessionHooks = field(default_factory=SessionHooks)
    host_hooks: HostHooks = field(default_factory=HostHooks)
    session_start_contexts: SessionStartContexts = field(
        default_factory=SessionStartContexts
    )
    account_gateway: AccountGateway | None = None
    identity_gateway: IdentityGateway | None = None
    platform_credentials: PlatformCredentialProviderFactory | None = None
    # The session's credential for a provider by name, as its model calls
    # resolve it: what side calls (narration, image description) send. None
    # outside a process: they read the API key.
    provider_credentials: Callable[[str], ProviderCredentialProvider] | None = None
    # The pair ``AgentLoop`` owns on the legacy path, so the account and
    # identity hosts can name a cache without naming a backend. A session that
    # sends no eval resolves its telemetry attributes through them.
    identity_cache: IdentityCache = field(default_factory=IdentityCache)
    whoami_cache: WhoAmICache = field(default_factory=WhoAmICache)
    # Never evaluates: it holds the eval ``ExperimentsService`` answered for this
    # context's sessions, and the attributes telemetry segments by.
    experiment_manager: ExperimentManager = field(default_factory=ExperimentManager)
    # Mutable holder the runtime's Mistral adapter writes the provider
    # correlation id into (via ``correlation_id_sink``); telemetry forwarding
    # reads it. Frozen field, mutable object.
    correlation: CorrelationIdHolder = field(default_factory=CorrelationIdHolder)
    # Buffer the runtime's completion adapter appends each request's shape into
    # (via ``request_sent_sink``); the adapter drains it on the app loop and
    # forwards ``vibe.request_sent``. Frozen field, mutable object.
    request_sent: RequestSentQueue = field(default_factory=RequestSentQueue)
    # Buffer the runtime's completion adapter appends each malformed tool call
    # report into (via ``malformed_tool_calls_sink``); drained next to
    # ``request_sent`` and forwarded as ``vibe.tool_call.detected``.
    malformed_tool_calls: MalformedToolCallsQueue = field(
        default_factory=MalformedToolCallsQueue
    )
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
    # The tools a client of this process runs itself, if it serves any.
    client_tools: ClientToolExecutor = field(default_factory=ClientToolExecutor)
    # Each subagent's agent model calls, counted off the request-sent sink the
    # session shares with its subagents, for ``session/subagents/read``.
    subagent_steps: SubagentSteps = field(default_factory=SubagentSteps)
    # The sizes of each session's newest model input, recorded off the model
    # request sink the session shares with its subagents, for
    # ``session/context/read``.
    model_requests: ModelRequestLog = field(default_factory=ModelRequestLog)
    # The catalogue answer a reopened session saved, read by every derivation.
    # Frozen field, mutable object.
    saved_catalogue: SavedCatalogue = field(default_factory=SavedCatalogue)
    session_spawner: SessionSpawner | None = None
    session_peers: SessionPeers | None = None
    # Synchronizes admin config operations on the shared orchestrator across
    # adapters created during rewind/clear. Frozen field, mutable lock.
    admin_config_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The key this context's org config is held under: read before any org
    # config applied, since the org config can change it. Frozen field,
    # mutable slot: /reload moves it when the credential or endpoint moved.
    org_config_key: OrgConfigKeySlot = field(default_factory=OrgConfigKeySlot)
    # Read by ``derive``, so a later configuration push keeps a moved session
    # where it moved. Frozen field, mutable slot.
    workspace: WorkspaceSlot | None = None
    # What the build did with the org config, for the first session that runs
    # under this context to publish. Frozen field, mutable slot.
    org_config_report: OrgConfigReportSlot = field(default_factory=OrgConfigReportSlot)
    sandbox_skills: SandboxSessionSkills | None = None
    """So the skills a sandboxed session lists rank its project's own as its derivations
    do.
    """
    completion_budget: CompletionBudgetSlot = field(
        default_factory=CompletionBudgetSlot
    )


class SessionContextBuilder(Protocol):
    def __call__(
        self,
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: AgentEntrypoint = "cli",
        org_config: OrgConfigSource,
    ) -> Awaitable[UnifiedSessionContext]: ...


@dataclass(frozen=True, slots=True)
class UnifiedReadContext:
    """What a session read needs. Deliberately cannot express a runtime."""

    storage_root: str
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    harness_files: HarnessFilesManager
    legacy_source_loader: LegacySourceLoader
    legacy_source_resolver: LegacySourceResolver
    # A factory, not a manager: only a session with a pinned agent pays for the
    # agent scan, and most reads have none.
    build_agents: Callable[[], AgentManager]

    @property
    def config(self) -> VibeConfigSchema:
        return self.config_orchestrator.config


class ReadContextBuilder(Protocol):
    def __call__(self, options: SessionOptions) -> Awaitable[UnifiedReadContext]: ...


class _PinTarget(Protocol):
    """What restoring a session's pins writes to."""

    @property
    def config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]: ...

    @property
    def agents(self) -> AgentManager: ...

    @property
    def saved_catalogue(self) -> SavedCatalogue: ...


class _ReadPinTarget:
    """A read context's orchestrator, with its agents built if a pin needs them."""

    def __init__(self, context: UnifiedReadContext) -> None:
        self._context = context
        self._agents: AgentManager | None = None
        # A read builds no derivation, so the restored catalogue answer is not read.
        self.saved_catalogue = SavedCatalogue()

    @property
    def config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        return self._context.config_orchestrator

    @property
    def agents(self) -> AgentManager:
        if self._agents is None:
            self._agents = self._context.build_agents()
        return self._agents


@dataclass(frozen=True, slots=True)
class _MergedListingKey:
    cwd: str | None
    cwds: tuple[str, ...] | None
    root_session_id: str | None
    parent_session_id: str | None
    pinned: bool | None
    include_archived: bool


@dataclass(frozen=True, slots=True)
class _MergedListing:
    """One cursor walk's view of the merged unified + legacy session list."""

    key: _MergedListingKey
    sessions: list[PublicSession]
    legacy: LegacySessionStore
    storage_root: str


@dataclass(frozen=True, slots=True)
class _ListedRow:
    item: HarnessSessionListItem
    metadata_signature: FileSignature
    loops_signature: FileSignature
    public: PublicSession


@dataclass(slots=True)
class _ListedRows:
    """Rows reused while their sidecars keep the same file signature."""

    storage_root: str | None = None
    rows: dict[str, _ListedRow] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _SessionMetadataProjection:
    session_id: str
    patch: tuple[JsonPatchOperation, ...]


@dataclass(frozen=True, slots=True)
class _RelocationDestination:
    target: Path
    checkout: Path
    changes_worktree: bool


@dataclass(frozen=True, slots=True)
class _PreparedRelocation:
    backend: UnifiedHarnessBackendAdapter | None
    previous_cwd: Path
    destination: _RelocationDestination
    context: UnifiedSessionContext
    derivation: UnifiedRuntimeDerivation
    trust_store: TrustedFoldersManager
    git_trust_store: TrustedFoldersManager


@dataclass(frozen=True, slots=True)
class _Unchanged:
    """Marks a nullable metadata field the caller is not touching."""


_UNCHANGED: Final = _Unchanged()
# How old cached MCP tools may get before an open refreshes them in the
# background. Under the cache TTL, so tools stay offered while refreshing.
_MCP_DESCRIPTOR_REFRESH_AFTER_S: Final = 6 * 60 * 60
# The sweep needs every match before it can merge, sort and filter pins, so it
# asks for them in one call.
_SESSION_LISTING_PAGE = sys.maxsize
# The lease the Harness's maintenance holds; a session open waits on it.
_MAINTENANCE_LEASE: Final = "process-output-cleanup"
_MOVE_HELD_ELSEWHERE: Final = "Another connection or process holds the session."
# A started session reports back from inside its turn, so its starter's archive
# right after the report finds it finishing that turn.
_PEER_ARCHIVE_SETTLE_SECONDS = 10.0
_PEER_ARCHIVE_POLL_SECONDS = 0.05
_HISTORY_PAGE_WINDOW = 500
# A turn can end, or a queued one start, between joining and starting one.
_SESSION_MESSAGE_DELIVERY_ATTEMPTS: Final = 20
_SESSION_MESSAGE_RETRY_SECONDS: Final = 0.25
_HISTORY_WINDOW_SESSIONS = 64
_RELOCATION_HISTORY_LIMIT = 200
# Settings only delivery surfaces read, so writing them cannot change a running
# turn; ``config/write`` accepts them mid-turn (ADR 0012).
_TURN_INDEPENDENT_CONFIG_PATHS: Final = frozenset({
    "/theme",
    "/voice_mode_enabled",
    "/narrator_enabled",
    "/log_level",
})


def _is_turn_independent_write(ops: Sequence[ConfigWriteOpWire]) -> bool:
    return bool(ops) and all(op.path in _TURN_INDEPENDENT_CONFIG_PATHS for op in ops)


async def _client_tool_caller(
    host: UnifiedHarnessSessionBackendHost, session_id: str
) -> ClientToolCaller:
    """Name the session that calls a client tool: the root, or a subagent of it."""
    read = await host.read(
        HarnessSessionReadParams(session_id=session_id, history_limit=0)
    )
    session = read.snapshot.state.session
    root_session_id = session.root_session_id or session.id
    caller = ClientToolCaller(
        session_id=session.id,
        root_session_id=root_session_id,
        parent_session_id=session.parent_session_id,
    )
    if session.parent_session_id is None:
        return caller
    record = next(
        (
            record
            # Records live on the parent, which is live while its child runs.
            for record in host.child_session_records(session.parent_session_id)
            if record.child_session_id == session.id
        ),
        None,
    )
    if record is None:
        return caller
    return replace(caller, name=record.agent_name, agent_type=record.agent_type)


# A subagent in one of these has a turn running; ``session/subagents/wait``
# waits for none to be left.
_RUNNING_CHILD_STATES = frozenset({"running", "deleting_running"})
_MESSAGEABLE_CHILD_STATES = frozenset({"idle", "turn_failed"})
_GONE_CHILD_STATES = frozenset({
    "tombstone",
    "creation_failed",
    "creation_cleanup_pending",
    "deleting_idle",
    "deleting_running",
})


def _child_is_gone(record: ChildSessionRecord) -> bool:
    state = record.state
    if state.type == "turn_failed":
        # A failed Turn the child cannot recover from takes no more messages.
        return not getattr(state, "reusable", True)
    return state.type in _GONE_CHILD_STATES


_INTERRUPT_OUTCOMES = {
    "interrupted": SubagentInterruptOutcome.INTERRUPTED,
    "not_running": SubagentInterruptOutcome.NOT_RUNNING,
    "failed": SubagentInterruptOutcome.FAILED,
}


_SKILL_WATCH_RETRY_INITIAL_S = 1.0
_SKILL_WATCH_RETRY_MAX_S = 60.0

_TURN_END_PR_DISCOVERY_INTERVAL_S = 60.0

_CHILD_LOOPS_CHECK_INTERVAL_S = 1.0


def _image_input(derivation: UnifiedRuntimeDerivation) -> bool:
    """Whether the model the derivation runs reads images."""
    return (
        "image" in derivation.core_config.settings.context.model_facts.input_modalities
    )


def _log_completion_warning(warning: CompletionWarning) -> None:
    logger.warning("Completion settings: %s (%s)", warning.message, warning.code)


@dataclass(eq=False)
class _ArtifactSubscription:
    """A client's subscription to the PR reads of the stored sessions it
    shows, and where those sessions are stored.
    """

    storage_root: str | None = None
    # The announcements in flight.
    announcements: set[asyncio.Task[None]] = field(default_factory=set)


def _announced(
    announcements: set[asyncio.Task[None]],
) -> Callable[[asyncio.Task[None]], None]:
    def done(task: asyncio.Task[None]) -> None:
        announcements.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "Pull request announcement failed", exc_info=task.exception()
            )

    return done


def _unified_session_dir(storage_root: str, session_id: str) -> Path:
    return Path(storage_root) / "unified" / session_id


def _read_unified_session_metadata(session_dir: Path) -> SessionMetadata | None:
    try:
        raw = json.loads(read_safe(session_dir / METADATA_FILENAME).text)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return SessionMetadata.model_validate(
            sanitize_artifacts_in_raw_metadata(raw, session_dir=session_dir)
        )
    except ValidationError:
        return None


def _read_session_project(storage_root: str, session_id: str) -> SessionProject | None:
    metadata = _read_unified_session_metadata(
        _unified_session_dir(storage_root, session_id)
    )
    return metadata.project if metadata is not None else None


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


def _metadata_archived_at(
    previous: SessionMetadata | None, archived_at: datetime | None | _Unchanged
) -> str | None:
    previous_archived_at = previous.archived_at if previous is not None else None
    if isinstance(archived_at, _Unchanged):
        return previous_archived_at
    if archived_at is None:
        return None
    if previous_archived_at is not None:
        return previous_archived_at
    return archived_at.astimezone(UTC).isoformat()


def _build_unified_session_metadata(
    session: HarnessPublicSession,
    cwd: str | None,
    *,
    previous: SessionMetadata | None = None,
    bumped_at: datetime | None = None,
    pinned_at: datetime | None | _Unchanged = _UNCHANGED,
    archived_at: datetime | None | _Unchanged = _UNCHANGED,
    project: SessionProject | None = None,
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
        archived_at=_metadata_archived_at(previous, archived_at),
        unseen_at=previous.unseen_at if previous is not None else None,
        seen_at=previous.seen_at if previous is not None else None,
        experiments=previous.experiments if previous is not None else None,
        # The config carry-over is load-bearing: the host adapter path
        # (rename/pin/archive on a closed session) has no access to the
        # session's model pin, so it relies on the live adapter having
        # written the model into config on the first persist.
        config=previous.config if previous is not None else None,
        # Only the live adapter knows the path; the host paths carry it over.
        runtime_path=previous.runtime_path if previous is not None else None,
        import_provenance=previous.import_provenance if previous is not None else None,
        created_worktree=previous.created_worktree if previous is not None else None,
        # Only an unstamped session takes a stamp: a session never changes project here.
        project=(
            previous.project
            if previous is not None and previous.project is not None
            else project
        ),
        # The artifact service owns this field between metadata persists (it
        # writes meta.json directly when a PR/MR is detected); carrying it over
        # keeps the next full persist from wiping the recorded artifacts.
        artifacts=previous.artifacts if previous is not None else [],
        worktrees=previous.worktrees if previous is not None else [],
    )


async def _persist_unified_session_metadata(
    session_dir: Path,
    session: HarnessPublicSession,
    cwd: str | None,
    *,
    previous: SessionMetadata | None = None,
    bumped_at: datetime | None = None,
    pinned_at: datetime | None | _Unchanged = _UNCHANGED,
    archived_at: datetime | None | _Unchanged = _UNCHANGED,
    project: SessionProject | None = None,
) -> SessionMetadata:
    metadata = _build_unified_session_metadata(
        session,
        cwd,
        previous=previous,
        bumped_at=bumped_at,
        pinned_at=pinned_at,
        archived_at=archived_at,
        project=project,
    )
    return await _write_unified_session_metadata(session_dir, metadata)


async def _write_unified_session_metadata(
    session_dir: Path, metadata: SessionMetadata
) -> SessionMetadata:
    await asyncio.to_thread(session_dir.mkdir, mode=0o700, parents=True, exist_ok=True)
    await persist_metadata(metadata.model_dump(mode="json"), session_dir)
    return metadata


def _metadata_bumped_at_ms(metadata: SessionMetadata | None) -> int | None:
    return optional_time_ms(metadata.bumped_at) if metadata is not None else None


def _metadata_pinned_at_ms(metadata: SessionMetadata | None) -> int | None:
    return optional_time_ms(metadata.pinned_at) if metadata is not None else None


def _metadata_archived_at_ms(metadata: SessionMetadata | None) -> int | None:
    return optional_time_ms(metadata.archived_at) if metadata is not None else None


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
        self, *, console_base_url: str, principal: str | None, whoami: WhoAmIResult
    ) -> None:
        if principal is not None:
            self.context.whoami_cache.populate(
                base_url=console_base_url, principal=principal, result=whoami
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

    async def clear_account_whoami(self, *, principal: str | None) -> None:
        if principal is not None:
            self.context.whoami_cache.invalidate(principal)
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
    def credentials(self) -> PlatformCredentialProviderFactory | None:
        return self._context.platform_credentials

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
        """Every skill file and registry pin, one per name, scope and source, then
        the plugin skills; each marked active when its name resolves to it, and
        an active one shown in the version the session runs.
        """
        skills = installed_session_skills(
            lambda: self._context.config_orchestrator.config,
            harness_files=self._context.harness_files,
            sandbox=self._context.sandbox_skills,
        )
        orchestrator = self._context.config_orchestrator
        installed = project_installed_skill_summaries(
            skills, orchestrator.config, writable_disabled_skills(orchestrator)
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


class _UnifiedFeedbackHost:
    """Presents the Unified backend as a ``FeedbackHost``."""

    def __init__(
        self,
        *,
        config: Callable[[], VibeConfigSchema],
        telemetry: TelemetryClient,
        require_session: Callable[[str], None],
        history: Callable[[], Awaitable[list[PublicHistoryEntry]]],
    ) -> None:
        self._config = config
        self._telemetry = telemetry
        self._require_session = require_session
        self._history = history

    def require_session(self, session_id: str) -> None:
        self._require_session(session_id)

    def telemetry_active(self) -> bool:
        return self._telemetry.is_active()

    def is_active_model_mistral(self) -> bool:
        return self._config().is_active_model_mistral()

    async def history(self) -> list[PublicHistoryEntry]:
        return await self._history()


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
type _WorktreeResolver = Callable[[SessionOptions], Awaitable[WorktreeResolution]]


@dataclass(frozen=True, slots=True)
class _SessionContextScope:
    options: SessionOptions
    rebuild_context: bool
    trust_grants_to_revoke: tuple[Path, ...]


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


def _raise_session_config_error(
    exc: ConfigStorageError
    | LayerImplementationError
    | UnusedConfigKeysError
    | ValidationError,
) -> Never:
    """Answer a config the user can fix as a configuration error."""
    error = user_config_error(exc)
    if error is None:
        raise exc
    raise SessionBackendError(error.code, error.message, error.data) from exc


def adapt_harness_host(  # noqa: PLR0913 - one collaborator per concern, all keyword-only
    host: object,
    build_session_context: SessionContextBuilder,
    services: SessionBackendServices | None = None,
    *,
    build_read_context: ReadContextBuilder | None = None,
    launch_context_getter: LaunchContextGetter | None = None,
    release_storage: Callable[[], None] | None = None,
    owns_host: bool = True,
    pin_session_options: Callable[[SessionOptions], SessionOptions] | None = None,
    worktree_housekeeping: WorktreeHousekeeping | None = None,
    host_hooks: HostHooks | None = None,
    open_sessions: OpenSessions | None = None,
) -> UnifiedHarnessBackendHostAdapter:
    return UnifiedHarnessBackendHostAdapter(
        cast(UnifiedHarnessSessionBackendHost, host),
        build_session_context,
        services,
        build_read_context=build_read_context,
        launch_context_getter=launch_context_getter,
        release_storage=release_storage,
        owns_host=owns_host,
        pin_session_options=pin_session_options,
        worktree_housekeeping=worktree_housekeeping,
        host_hooks=host_hooks,
        open_sessions=open_sessions,
    )


def _configure_host_connectors(
    host: UnifiedHarnessSessionBackendHost,
    context: UnifiedSessionContext,
    catalog: ResolvedConnectorCatalog,
    selection: ResolvedConnectorSelection,
) -> None:
    headers_resolver = _connector_headers_resolver(context)
    host.configure_connectors(
        _harness_connector_catalog(catalog),
        _harness_connector_selection(selection),
        lambda: HarnessConnectorGatewayClient(
            base_url=context.connector_base_url,
            # The resolver supplies the credential; the gateway's own header
            # is never sent when one is set.
            api_key="",
            enable_system_trust_store=context.mcp_enable_system_trust_store,
            headers_resolver=headers_resolver,
        ),
        gateway_authority_digest=sha256_json({
            "base_url": context.connector_base_url,
            "enable_system_trust_store": context.mcp_enable_system_trust_store,
        }),
    )


def _connector_headers_resolver(
    context: UnifiedSessionContext,
) -> Callable[[str], Awaitable[Mapping[str, str]]]:
    """The connection's credential for each gateway call, resolved per call.

    Without a bound selection (a context built outside a process), the API key.
    """
    from vibe.core.platform_auth._mistral import api_key_credentials

    factory = context.platform_credentials or api_key_credentials
    provider = context.config_orchestrator.config.get_mistral_provider()
    credentials = factory(
        provider.api_key_env_var if provider is not None else "MISTRAL_API_KEY"
    )

    async def resolve(url: str) -> Mapping[str, str]:
        from vibe.core.platform_auth import (
            PlatformCredentialRequired,
            PlatformCredentialUnavailable,
        )
        from vibe.core.platform_auth._mistral import resolve_snapshot

        snapshot = await resolve_snapshot(credentials, url)
        if isinstance(snapshot, PlatformCredentialRequired):
            raise PlatformCredentialUnavailable(snapshot)
        return dict(snapshot.headers)

    return resolve


def _without_plugin_servers(catalog: ResolvedMCPCatalog) -> ResolvedMCPCatalog:
    return replace(
        catalog,
        servers=tuple(
            server
            for server in catalog.servers
            if server.authorization.owner != "plugin"
        ),
    )


async def _configured_mcp_catalog(context: UnifiedSessionContext) -> ResolvedMCPCatalog:
    """The configured MCP servers the session's config resolves to now; plugin
    servers follow the plugins, not the config.
    """
    if context.mcp_catalog_service is None:
        return _without_plugin_servers(context.mcp_catalog)
    return cast(
        ResolvedMCPCatalog,
        await context.mcp_catalog_service.resolve_catalog(context.config_orchestrator),
    )


class UnifiedHarnessBackendHostAdapter:  # noqa: PLR0904 - implements app-server host operations
    """Vibe's session Host backed by the Unified Harness Runtime."""

    def __init__(  # noqa: PLR0913 - one collaborator per concern, all keyword-only
        self,
        host: UnifiedHarnessSessionBackendHost,
        build_context: SessionContextBuilder,
        services: SessionBackendServices | None = None,
        *,
        build_read_context: ReadContextBuilder | None = None,
        launch_context_getter: LaunchContextGetter | None = None,
        release_storage: Callable[[], None] | None = None,
        owns_host: bool = True,
        pin_session_options: Callable[[SessionOptions], SessionOptions] | None = None,
        worktree_housekeeping: WorktreeHousekeeping | None = None,
        host_hooks: HostHooks | None = None,
        open_sessions: OpenSessions | None = None,
    ) -> None:
        self._host = host
        self._owns_host = owns_host
        # Every channel's Host, this one included, so an archive reaches a
        # session another channel holds open. Alone without it.
        self._open_sessions = open_sessions or OpenSessions()
        self._open_sessions.register(self)
        self._build_context = build_context
        # The background hooks this app server's sessions start; its shutdown
        # waits for them, whichever sessions are still open by then.
        self._host_hooks = host_hooks or HostHooks()
        # What the process imposes on every session it opens, whatever the
        # client asked: ``--agent-socket``'s sandbox workspace.
        self._pin_session_options = pin_session_options
        # Supplied by the composition root. Without it a listing falls back to
        # building a whole session context, which is correct but pays for
        # plugins, MCP and connectors it never reads.
        self._build_read_context = build_read_context
        self._services = services
        self._launch_context_getter = launch_context_getter
        self._release_storage = release_storage
        self._telemetry_clients: set[TelemetryClient] = set()
        self._adapters: dict[str, UnifiedHarnessBackendAdapter] = {}
        # Cold-path artifact writes (a channel with no session attached)
        # build a fresh service per call; this serializes each stored
        # session's load→mark→persist so concurrent marks cannot lose each
        # other's updates.
        self._stored_artifact_locks: dict[str, asyncio.Lock] = {}
        # The channel's artifacts/subscribe: it owns the watches of the
        # stored sessions the client lists.
        self._artifact_subscription: _ArtifactSubscription | None = None
        self._history_windows = _HistoryWindows()
        self._worktree_housekeeping = worktree_housekeeping
        self._worktrees = SessionWorktrees(
            launch_context_getter=self._launch_context_getter,
            housekeeping=worktree_housekeeping,
        )
        self._merged_listing: _MergedListing | None = None
        # Serialize this process's read-then-write of one closed session's metadata.
        self._closed_metadata_locks: dict[str, asyncio.Lock] = {}
        self._listed_rows = _ListedRows()

    @property
    def harness_kind(self) -> SessionBackendKind:
        return "unified"

    def _pinned[OpenParamsT: SessionOpenParams](
        self, params: OpenParamsT
    ) -> OpenParamsT:
        if self._pin_session_options is None:
            return params
        return params.model_copy(
            update={"agent_config": self._pin_session_options(params.agent_config)}
        )

    async def start(self, params: SessionStartParams) -> SessionLifecycleResult:
        params = self._pinned(params)
        if params.agent_config.worktree is None:
            result, _ = await self._start_resolved(params, params.agent_config)
            return result

        options = params.agent_config.model_copy(update={"worktree": None})
        result, started = await self._start_resolved(params, options)
        backend = cast(UnifiedHarnessBackendAdapter, result.backend)
        worktree = params.agent_config.worktree
        # The move rebuilds from these, so it keeps the agent the start resolved.
        requested = params.agent_config.model_copy(
            update={"agent": started.agent, "auto_approve": started.auto_approve}
        )
        progress = (
            WorktreeProgress(
                name=worktree.name if worktree.kind == "create" else None,
                feed=WorktreeProgressFeed(asyncio.get_running_loop()),
            )
            if worktree.kind in {"auto", "create"}
            else None
        )
        backend.defer_turns(
            self._worktrees.raise_behind(
                backend.session_id,
                backend._joining_session_dir(),
                lambda moved: self._move_session(backend, moved),
                requested,
                options,
                announce=lambda moved, prepared: self._announce_worktree(
                    backend.session_id, _session_cwd(moved), prepared
                ),
                on_progress=progress.feed.report if progress is not None else None,
                trust_store=backend._worktree_trust_store,
                copy_on_write=backend.config.worktree_copy_on_write,
            ),
            worktree_progress=progress,
        )
        return result

    async def _start_context(
        self, options: SessionOptions
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation, SessionOptions]:
        """Build a new session's context, with auto-approve resolved against it.

        The bypass on a built-in permission mode becomes the ``auto-approve``
        agent, which needs a context built for it: the bypass and the agent are
        both read when the context is built.
        """
        context, derivation = await self._lifecycle_context(
            options, require_api_key=True, org_config=OrgConfigSource.ENSURED
        )
        resolved = resolve_start_auto_approve(options, context.agents)
        if resolved is options:
            return context, derivation, options
        if options.trust_workspace and derivation.adapter_config.sandbox is None:
            # Each host build grants the cwd's session trust: keep only the
            # rebuild's. A sandboxed build grants none, so it has none to undo.
            context.harness_files.trust_store.revoke_session_trust(
                Path(_session_cwd(options))
            )
        first_report = context.org_config_report.take()
        with contextlib.suppress(Exception):
            await context.experiment_manager.aclose()
        context, derivation = await self._lifecycle_context(
            resolved, require_api_key=True, org_config=OrgConfigSource.CACHED
        )
        context.org_config_report.inherit(first_report)
        return context, derivation, resolved

    async def _move_session(
        self, backend: UnifiedHarnessBackendAdapter, options: SessionOptions
    ) -> None:
        previous = backend.cwd
        cwd = _session_cwd(options)
        git_trust = await asyncio.to_thread(
            _worktree_git_trust, Path(previous or cwd), backend._worktree_trust_store
        )
        context, _ = await self._lifecycle_context(options, require_api_key=False)
        # Pushing the context is the commit point, because it is what retargets
        # the tools. Everything after it is bookkeeping over a session that has
        # already moved, so a failure there is logged rather than raised: raising
        # would trigger startup cleanup for the worktree the tools now use.
        await backend.adopt_context(context)
        backend._worktree_trust_store = git_trust
        # Adoption is the holder commit point. Release the old checkout before
        # the next await so shutdown cancellation cannot observe the target cwd
        # while leaving this session's holder behind on the source. The
        # lifecycle's post-move release is intentionally idempotent. A session
        # that started in the worktree it joins keeps the hold it just took.
        if previous is not None and Path(previous).resolve() != Path(cwd).resolve():
            self._worktrees.release(
                Path(previous),
                backend.session_id,
                trust_store=context.harness_files.trust_store,
            )
        try:
            if previous is not None and options.trust_workspace:
                context.harness_files.trust_store.revoke_session_trust(Path(previous))
            await self._announce_cwd(backend.session_id, cwd)
        except Exception as exc:
            logger.warning("Failed to settle a moved session", exc_info=exc)

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

    async def _announce_worktree(
        self, session_id: str, cwd: str, prepared: PreparedWorktree | None
    ) -> None:
        """Announce the prepared worktree before the move that adopts it.

        A new notification, additive to the post-move `/cwd` announce: a client
        waiting out the preparation (the CLI's pre-TUI gate) can proceed the
        moment the worktree exists, while the context rebuild that retargets
        the tools still runs. The worktree rides along so a client that quits
        before its first turn still knows what to ask about at exit; the
        transcript effect only lands with a deferred turn. Best effort: a lost
        announce costs an early footer, not the session.
        """
        if self._services is None or prepared is None:
            return
        worktree = PublicSessionWorktree(
            name=prepared.name,
            branch=prepared.branch,
            path=str(prepared.root),
            created=prepared.created,
        )
        await self._services.notify(
            "session/updated",
            SessionUpdatedParams(
                event_id=0,
                session_id=session_id,
                patch=[
                    JsonPatchOperation(op="replace", path="/cwd", value=cwd),
                    JsonPatchOperation(
                        op="replace",
                        path="/worktree",
                        value=worktree.model_dump(mode="json", by_alias=True),
                    ),
                ],
                emitted_at=now_ms(),
            ),
        )

    async def _start_resolved(
        self, params: SessionStartParams, options: SessionOptions
    ) -> tuple[SessionLifecycleResult, SessionOptions]:
        resolution = await self._worktrees.resolve_for_start(options)
        try:
            context, derivation, started = await self._start_context(resolution.options)
            context = (
                await self._plant_connector_catalog(context, cached_only=True)
                or context
            )
            session = await _harness_call(
                self._host.start(
                    HarnessSessionStartParams(history_limit=params.history_limit),
                    cwd=_session_cwd(resolution.options),
                    ephemeral=True,
                    hook_bindings=context.hooks.bindings,
                    hook_handlers=context.hooks.handlers,
                    started_by_session_id=params.started_by_session_id,
                )
            )
            backend = self._adapter(
                session,
                context,
                derivation,
                scheduled_loops_enabled=not resolution.options.headless,
                project=(
                    SessionProject.model_validate(params.project.model_dump())
                    if params.project is not None
                    else None
                ),
            )
            await self._prepare_opened_backend(
                backend,
                backend.restore_scheduled_loops(),
                worktree_resolution=resolution,
            )
            if started.agent is not None:
                await backend.pin_started_agent()
            if (
                params.started_by_session_id is not None
                and params.first_turn is not None
            ):
                backend.expect_handed_task(max(params.hops, 1))
            if params.started_by_session_id is None:
                await backend.seat_terminal(
                    _terminal_seat(options, context), record_now=False
                )
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise
        # Fresh start emits new_session/ready once the background experiment eval
        # resolves, so those events carry user_plan/experiment_attributes — the
        # only path that emits both, mirroring the legacy AgentLoop. A warm-up
        # session counts as one only once it is used.
        if params.kind is SessionKind.EPHEMERAL:
            backend.announce_on_first_use()
            self._start_experiments(backend, after=backend.record_ready)
        else:
            self._start_experiments(backend, after=backend._emit_fresh_start_telemetry)
        return SessionLifecycleResult(
            backend=backend,
            after_response=self._after_open(
                backend,
                session_start=None
                if params.kind is SessionKind.EPHEMERAL
                else SessionStartSource.STARTUP,
            ),
        ), started

    async def resume(self, params: SessionResumeParams) -> SessionLifecycleResult:
        return await self._or_warm_ups_ready(self._resume(params))

    async def continue_latest(
        self, params: SessionContinueParams
    ) -> SessionLifecycleResult:
        return await self._or_warm_ups_ready(self._continue_latest(params))

    async def _or_warm_ups_ready(
        self, opening: Awaitable[SessionLifecycleResult]
    ) -> SessionLifecycleResult:
        try:
            return await opening
        except Exception:
            self._warm_ups_ready()
            raise

    async def _resume(self, params: SessionResumeParams) -> SessionLifecycleResult:
        params = self._pinned(params)
        self._worktrees.reject_input(params.agent_config)
        context, derivation = await self._lifecycle_context(
            params.agent_config,
            require_api_key=True,
            org_config=OrgConfigSource.ENSURED,
        )
        _require_session_logging(context)
        seat = _terminal_seat(params.agent_config, context)
        context, derivation, resolution = await self._pin_for_resume(
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
            backend._unlanded_worktree_candidate = True
            await self._prepare_opened_backend(
                backend,
                backend.restore_scheduled_loops(),
                worktree_resolution=resolution,
            )
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise
        await backend.reload_switched_plugins()
        await backend.seat_terminal(
            self._seat_for_session(seat, backend.cwd), record_now=True
        )
        self._start_experiments(backend, after=self._launch_ready(backend))
        return SessionLifecycleResult(
            backend=backend,
            after_response=self._after_open(
                backend, session_start=SessionStartSource.RESUME
            ),
        )

    async def _continue_latest(
        self, params: SessionContinueParams
    ) -> SessionLifecycleResult:
        # Resolve the latest session once and resume it by id, rather than pinning
        # cwd/hooks off one listing and letting Host.continue_latest list again: a
        # session created between the two lists would otherwise be resumed with hooks
        # compiled for a different project's cwd (and a clean rebind would persist them
        # on the wrong session). The first _context configures the Host (storage/legacy),
        # which the catalogue sweep and session_cwd need. The target is the
        # terminal's last session in the invocation's directory, else the latest
        # there, matched as session/list matches it.
        params = self._pinned(params)
        self._worktrees.reject_input(params.agent_config)
        context, derivation = await self._lifecycle_context(
            params.agent_config,
            require_api_key=True,
            org_config=OrgConfigSource.ENSURED,
        )
        _require_session_logging(context)
        seat = _terminal_seat(params.agent_config, context)
        listing = SessionListParams(cwd=_session_cwd(params.agent_config))
        sessions, latest_id = await self._sweep_unified_sessions(
            listing,
            params.agent_config,
            context.storage_root,
            cwd_matches=self._cwd_matcher(listing),
        )
        eligible = {session.id for session in _continue_candidates(sessions)}
        target_id = await continue_target(seat, eligible, latest_id)
        if target_id is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND,
                "No session to continue",
                {"harnessCode": NOTHING_TO_CONTINUE},
            )
        context, derivation, resolution = await self._pin_for_resume(
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
            backend._unlanded_worktree_candidate = True
            await self._prepare_opened_backend(
                backend,
                backend.restore_scheduled_loops(),
                worktree_resolution=resolution,
            )
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise
        await backend.reload_switched_plugins()
        await backend.seat_terminal(seat, record_now=True)
        self._start_experiments(backend, after=self._launch_ready(backend))
        return SessionLifecycleResult(
            backend=backend,
            after_response=self._after_open(
                backend, session_start=SessionStartSource.RESUME
            ),
        )

    async def _resume_harness(
        self, session_id: str, history_limit: int, context: UnifiedSessionContext
    ) -> tuple[UnifiedHarnessSessionBackend, UnifiedSessionContext]:
        """Configure connector routes before recovery and retry old stores if needed.

        A store written before capability baselines were journalled replays its
        connector-bearing inputs against whatever capabilities the resume supplies,
        so it diverges unless the catalog is in place first. The eager pass reads
        the cached catalog only, so a resume never waits on the connector service;
        the retry below fetches it, for the divergences a Harness new enough to
        flag ``capabilities_required`` reports.
        """

        async def resume() -> UnifiedHarnessSessionBackend:
            return await self._host.resume(
                session_id,
                history_limit=history_limit,
                hook_bindings=context.hooks.bindings,
                hook_handlers=context.hooks.handlers,
            )

        resolved = await self._plant_connector_catalog(context, cached_only=True)
        if resolved is not None:
            context = resolved
        try:
            return await resume(), context
        except HarnessReplayDivergenceError as exc:
            if not exc.details or exc.details.get("capabilities_required") is not True:
                raise
            resolved = await self._plant_connector_catalog(context, cached_only=False)
            if resolved is None:
                raise
            context = resolved
            return await resume(), context

    async def _plant_connector_catalog(
        self, context: UnifiedSessionContext, *, cached_only: bool
    ) -> UnifiedSessionContext | None:
        """Plant a connector catalog in ``context`` and the Host, before a session opens.

        A start opens on the cached catalog, so its first turn has the
        connectors the last fetch found; a resume needs them to replay a legacy
        store's journal. ``cached_only`` never reaches the service.
        """
        service = context.connector_catalog_service
        if service is None or context.connector_catalog is not None:
            return None
        try:
            catalog = (
                await service.read_cached_catalog(context.config_orchestrator)
                if cached_only
                else await service.resolve_catalog(context.config_orchestrator)
            )
            if catalog is None:
                return None
            selection = service.resolve_selection(context.config_orchestrator, catalog)
            resolved = replace(
                context, connector_catalog=catalog, connector_selection=selection
            )
            self._configure_connectors(resolved, catalog, selection)
        except ConnectorCatalogError:
            logger.warning("Connector catalog is unavailable to open the session on")
            return None
        except Exception:
            # The caller re-raises the divergence when this returns None. Letting an
            # unexpected failure out instead would replace a replay error the resume
            # already reports with one about connectors.
            logger.exception("Failed to configure connectors to open the session on")
            return None
        return resolved

    async def fork(self, params: SessionForkParams) -> SessionForkResult:
        options = params.agent_config or SessionOptions()
        self._worktrees.reject_input(options)
        source = self._adapters.get(params.source_session_id)
        if source is not None and source._closed:
            source = None

        async def fork_at_source_boundary() -> tuple[
            UnifiedSessionContext,
            UnifiedRuntimeDerivation,
            WorktreeResolution,
            HarnessSessionForkResult,
        ]:
            # A fork continues the source session's work, so it keeps the org
            # config the source runs under (the cached copy) rather than
            # fetching a newer one, which would also hold the source's turns
            # behind the fetch.
            context, derivation = await self._lifecycle_context(
                options, require_api_key=True
            )
            context, derivation, resolution = await self._pin_for_fork(
                options,
                params.source_session_id,
                context,
                derivation,
                require_api_key=True,
                settings=None if source is None else source._settings,
                trust_store=None if source is None else source._worktree_trust_store,
            )
            try:
                result = await _harness_call(
                    self._host.fork(
                        params.source_session_id,
                        entry_id=params.entry_id,
                        history_limit=params.history_limit,
                        session_config=derivation.session_config,
                        hook_bindings=context.hooks.bindings,
                        hook_handlers=context.hooks.handlers,
                    )
                )
            except BaseException:
                await self._worktrees.cleanup(resolution)
                raise
            return context, derivation, resolution, result

        if source is not None:
            await source._await_deferred_setup()
            async with source._fork_boundary():
                (
                    context,
                    derivation,
                    resolution,
                    result,
                ) = await fork_at_source_boundary()
        else:
            context, derivation, resolution, result = await fork_at_source_boundary()
        try:
            fork_local = result.session.adapter_config
            if fork_local is not None:
                # The Harness folds unsaved live overrides into the target
                # workspace configuration. Adopt that exact pair so later
                # app-server derivations start from what the fork is running.
                derivation = replace(
                    derivation,
                    core_config=result.session._fork_configuration_over(
                        derivation.core_config
                    ),
                    adapter_config=fork_local,
                )
            backend = self._adapter(
                result.session,
                context,
                derivation,
                scheduled_loops_enabled=not options.headless,
                settings=None if source is None else source._settings,
                project=(
                    source._project
                    if source is not None
                    else await asyncio.to_thread(
                        _read_session_project,
                        context.storage_root,
                        params.source_session_id,
                    )
                ),
            )
            if source is not None:
                backend._worktree_trust_store = await asyncio.to_thread(
                    _worktree_git_trust,
                    Path(source.cwd or _session_cwd(options)),
                    source._worktree_trust_store,
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
        backend._emit_session_branched_telemetry(
            source_session_id=params.source_session_id,
            root_session_id=(
                snapshot.state.session.root_session_id or params.source_session_id
            ),
        )
        backend._emit_worktree_created_telemetry(resolution)
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
                self._after_open(
                    backend,
                    notices=_fork_worktree_notices(resolution),
                    session_start=SessionStartSource.FORK,
                )
                if attached is not None
                else None
            ),
        )

    async def list(self, params: SessionListParams) -> SessionListResponse:
        key = _MergedListingKey(
            cwd=params.cwd,
            cwds=tuple(params.cwds) if params.cwds is not None else None,
            root_session_id=params.root_session_id,
            parent_session_id=params.parent_session_id,
            pinned=params.pinned,
            include_archived=params.include_archived,
        )
        listing = self._merged_listing
        # A cursor continues a walk whose first page already swept both stores,
        # so rebuilding here would pay for the whole merge once per page. It
        # would also re-sort live data mid-walk: a session whose ``updated_at``
        # advances moves ahead of the cursor into the region already passed,
        # and the walk never returns it. Hold the first page's merge instead
        # and let the walk read a stable snapshot of it.
        if params.session_ids is not None:
            # Asked by id: answered on its own, so a walk the cache holds for
            # another caller goes on undisturbed.
            listing = await self._build_merged_listing(params, key)
        elif params.cursor is None or listing is None or listing.key != key:
            listing = await self._build_merged_listing(params, key)
            self._merged_listing = listing

        merged = listing.sessions
        start = _merged_cursor_index(merged, params.cursor)
        page = await asyncio.to_thread(
            listing.legacy.with_previews, merged[start : start + params.limit]
        )
        unified_ids = [session.id for session in page if session.harness == "unified"]
        return SessionListResponse(
            items=page,
            next_cursor=(
                _encode_merged_cursor(page[-1])
                if start + len(page) < len(merged) and page
                else None
            ),
            previous_cursor=None,
            pull_requests=(
                _public_pull_requests(
                    await asyncio.to_thread(
                        _listed_pull_requests, listing.storage_root, unified_ids
                    )
                )
                if "pullRequests" in params.include
                else None
            ),
        )

    async def _read_context(self, options: SessionOptions) -> UnifiedReadContext:
        """The single door every session read goes through."""
        if self._build_read_context is not None:
            try:
                context = await self._build_read_context(options)
            except USER_CONFIG_ERRORS as exc:
                _raise_session_config_error(exc)
            except InvalidConfigError as exc:
                # A session read loads the config a start does, so it reports a
                # config that breaks a schema rule the same way.
                raise SessionBackendError.configuration(exc) from exc
            self._configure_read_host(context)
            return context

        session_context, _ = await self._lifecycle_context(
            options, require_api_key=False
        )
        return UnifiedReadContext(
            storage_root=session_context.storage_root,
            config_orchestrator=session_context.config_orchestrator,
            harness_files=session_context.harness_files,
            legacy_source_loader=session_context.legacy_source_loader,
            legacy_source_resolver=session_context.legacy_source_resolver,
            build_agents=lambda: session_context.agents,
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

    def _seat_for_session(
        self, seat: TerminalSeat | None, session_cwd: str | None
    ) -> TerminalSeat | None:
        """A session reopened from another folder is that folder's continue target."""
        if seat is None or session_cwd is None:
            return seat
        resolved = str(Path(session_cwd).expanduser().resolve())
        here = self._session_cwd_matches(session_cwd, Path(seat.cwd))
        return seat if here else replace(seat, cwd=resolved)

    def _cwd_matcher(
        self, params: SessionListParams
    ) -> Callable[[str | None], bool] | None:
        requested_cwds = frozenset(
            Path(raw).expanduser().resolve() for raw in _requested_cwds(params)
        )
        # A single managed worktree, asked for by its own path, is the one
        # shape the stores can narrow themselves. Everything else is swept and
        # filtered here: neither store answers a worktree-aware question, and
        # neither answers about several checkouts at once.
        # None: the stores narrow it. A matcher filters here, so an empty
        # request matches nothing rather than everything. It is cached because
        # sessions share few cwds and each match resolves a path and a
        # worktree record.
        pushed_down = (
            params.cwds is None
            and len(requested_cwds) <= 1
            and all(self._worktrees.is_managed(cwd) for cwd in requested_cwds)
        )
        if pushed_down:
            return None
        return cache(
            partial(self._session_cwd_matches_any, requested_cwds=requested_cwds)
        )

    async def _build_merged_listing(
        self, params: SessionListParams, key: _MergedListingKey
    ) -> _MergedListing:
        cwd_matches = self._cwd_matcher(params)
        options = SessionOptions(
            cwd=params.cwd or next(iter(_requested_cwds(params)), None)
        )
        context = await self._read_context(options)
        legacy = LegacySessionStore(context.config)

        if params.session_ids is None:
            # One store is a host round trip and the other a worker-thread
            # filesystem scan, so reading them in sequence would add the smaller
            # to the larger for nothing.
            (unified_items, _latest_id), legacy_items = await asyncio.gather(
                self._sweep_unified_sessions(
                    params, options, context.storage_root, cwd_matches
                ),
                self._list_legacy_sessions(params, legacy, cwd_matches),
            )
        else:
            # A legacy import gets a new id, so only an id the Harness did not
            # find can be a legacy one.
            unified_items, _latest_id = await self._sweep_unified_sessions(
                params, options, context.storage_root, cwd_matches
            )
            found = {session.id for session in unified_items}
            legacy_items = (
                await self._list_legacy_sessions(params, legacy, cwd_matches)
                if not found.issuperset(params.session_ids)
                else []
            )

        merged = await asyncio.to_thread(
            _merge_listed_sessions, params, unified_items, legacy_items
        )
        if context.config.experimental_enable_local_projects:
            merged = await self._with_folder_projects(merged, context)

        return _MergedListing(
            key=key, sessions=merged, legacy=legacy, storage_root=context.storage_root
        )

    async def _with_folder_projects(
        self, sessions: list[PublicSession], context: UnifiedReadContext
    ) -> list[PublicSession]:
        """Each session no local project claims in or under a local project's folder, stamped with it."""
        # Only a session moved out explicitly is never matched by folder.
        if not any(
            session.project is None or session.project.kind != "none"
            for session in sessions
        ):
            return sessions
        store = LocalProjectStore()
        try:
            projects = await asyncio.to_thread(store.list_projects)
            project_ids = await asyncio.to_thread(store.project_ids)
        except LocalProjectStoreUnreadable:
            logger.warning("Sessions listed unstamped: local projects are unreadable")
            return sessions
        stamps = await asyncio.to_thread(
            _folder_stamps, projects, project_ids, sessions
        )
        if not stamps:
            return sessions
        # Without logging the storage root is a throwaway, not the saved sessions.
        if context.config.session_logging.enabled:
            await self._stamp_sessions(
                context.storage_root,
                {
                    session.id: (_stored_session_project(session), stamps[session.id])
                    for session in sessions
                    # The legacy store is read-only: its sessions match on every listing.
                    if session.id in stamps and session.harness == "unified"
                },
            )
        return [
            session.model_copy(
                update={"project": _public_session_project(stamps[session.id])}
            )
            if session.id in stamps
            else session
            for session in sessions
        ]

    async def _stamp_sessions(
        self,
        storage_root: str,
        stamps: Mapping[str, tuple[SessionProject | None, SessionProject]],
    ) -> None:
        """Best effort, replacing only the listed stamp: a held session waits for a later listing."""
        closed: dict[str, tuple[SessionProject | None, SessionProject]] = {}
        for session_id, (listed, stamp) in stamps.items():
            adapter = self._adapters.get(session_id)
            if adapter is not None and not adapter._closed:
                await adapter._stamp_project(stamp, expected=listed)
            else:
                closed[session_id] = (listed, stamp)
        if not closed:
            return
        # Held first, so a process opening one of these sessions waits rather than fails.
        maintenance = SessionLease(Path(storage_root), _MAINTENANCE_LEASE)
        try:
            await asyncio.to_thread(maintenance.acquire)
        except HarnessSessionBusyError:
            return
        try:
            for session_id, (listed, stamp) in closed.items():
                await self._stamp_closed_session(
                    storage_root, session_id, stamp, expected=listed
                )
        finally:
            await asyncio.to_thread(maintenance.release)

    async def _stamp_closed_session(
        self,
        storage_root: str,
        session_id: str,
        stamp: SessionProject,
        *,
        expected: SessionProject | None,
    ) -> None:
        async with self._closed_metadata_lock(session_id):
            # An open session would save its metadata over the stamp, so none may
            # open until it is saved; one already open elsewhere is left for later.
            lease = SessionLease(Path(storage_root), session_id)
            try:
                await asyncio.to_thread(lease.acquire)
            except HarnessSessionBusyError:
                return
            try:
                await self._write_closed_stamp(
                    storage_root, session_id, stamp, expected=expected
                )
            except Exception:
                logger.warning("Could not stamp a session's project", exc_info=True)
            finally:
                await asyncio.to_thread(lease.release)

    async def _write_closed_stamp(
        self,
        storage_root: str,
        session_id: str,
        stamp: SessionProject,
        *,
        replace: bool = False,
        expected: SessionProject | None = None,
    ) -> None:
        session_dir = _unified_session_dir(storage_root, session_id)
        previous = await asyncio.to_thread(_read_unified_session_metadata, session_dir)
        if (
            previous is not None
            and previous.project is not None
            and not replace
            and previous.project != expected
        ):
            return
        if previous is not None:
            await _write_unified_session_metadata(
                session_dir, previous.model_copy(update={"project": stamp})
            )
            return
        read = await _harness_call(
            self._host.read(
                HarnessSessionReadParams(session_id=session_id, history_limit=1)
            )
        )
        await _persist_unified_session_metadata(
            session_dir, read.snapshot.state.session, read.cwd, project=stamp
        )

    def _closed_metadata_lock(self, session_id: str) -> asyncio.Lock:
        return self._closed_metadata_locks.setdefault(session_id, asyncio.Lock())

    async def _sweep_unified_sessions(
        self,
        params: SessionListParams,
        options: SessionOptions,
        storage_root: str,
        cwd_matches: Callable[[str | None], bool] | None,
    ) -> tuple[list[PublicSession], str | None]:
        """Every unified session matching the request, and the latest of them."""
        items: list[PublicSession] = []
        cursor: str | None = None
        latest_id: str | None = None
        while True:
            harness_cwd = (
                None
                if cwd_matches is not None
                else _session_cwd(options)
                if params.cwd is not None
                else None
            )
            result = await _harness_call(
                self._host.list(
                    limit=_SESSION_LISTING_PAGE,
                    cursor=cursor,
                    cwd=harness_cwd,
                    root_session_id=params.root_session_id,
                    parent_session_id=params.parent_session_id,
                    session_ids=params.session_ids,
                )
            )
            items.extend(
                await asyncio.to_thread(
                    self._project_listed_rows,
                    storage_root,
                    result.items,
                    cwd_matches,
                    params.pinned,
                    complete=cursor is None
                    and harness_cwd is None
                    and params.root_session_id is None
                    and params.parent_session_id is None
                    and params.session_ids is None,
                )
            )
            if latest_id is None:
                latest_id = result.continue_session_id
            if result.next_cursor is None or not result.items:
                break
            cursor = result.next_cursor
        continue_candidates = _continue_candidates(items)
        if latest_id not in {session.id for session in continue_candidates}:
            latest = max(
                continue_candidates,
                key=lambda session: (session.updated_at, session.id),
                default=None,
            )
            latest_id = latest.id if latest is not None else None
        return items, latest_id

    def _project_listed_rows(
        self,
        storage_root: str,
        items: Sequence[HarnessSessionListItem],
        cwd_matches: Callable[[str | None], bool] | None,
        pinned: bool | None,
        *,
        complete: bool,
    ) -> list[PublicSession]:
        """Pins live in Vibe's sidecar, so they are filtered here. ``complete``
        means ``items`` is every stored session, which lets it prune the cache.
        """
        listed = self._listed_rows
        if listed.storage_root != storage_root:
            listed.storage_root = storage_root
            listed.rows = {}
        if complete:
            listed.rows = {
                item.session.id: row
                for item in items
                if (row := listed.rows.get(item.session.id)) is not None
            }
        rows: list[PublicSession] = []
        for item in items:
            if cwd_matches is not None and not (
                cwd_matches(item.cwd) or cwd_matches(item.origin_cwd)
            ):
                continue
            row = _project_listed_row(
                storage_root, item, listed.rows.get(item.session.id)
            )
            listed.rows[item.session.id] = row
            if pinned is not None and (row.public.pinned_at is not None) is not pinned:
                continue
            rows.append(row.public)
        return rows

    async def _list_legacy_sessions(
        self,
        params: SessionListParams,
        store: LegacySessionStore,
        cwd_matches: Callable[[str | None], bool] | None,
    ) -> list[PublicSession]:
        """Every legacy session matching the request, in one filesystem read.

        The reads and projections come from the read-only compatibility
        module; only the filters shared with the unified listing stay here.
        A secondary source: if the read fails the picker degrades to
        unified-only rather than blocking the primary listing. Lineage filters
        (root/parent) are unified-only, because legacy sessions carry no
        ``root_session_id``.
        """
        if params.root_session_id is not None or params.parent_session_id is not None:
            return []
        try:
            sessions = await asyncio.to_thread(
                store.list_sessions, None if cwd_matches is not None else params.cwd
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
        if cwd_matches is None:
            return sessions
        return [session for session in sessions if cwd_matches(session.cwd)]

    def _session_cwd_matches_any(
        self, session_cwd: str | None, requested_cwds: frozenset[Path]
    ) -> bool:
        if session_cwd is None or not requested_cwds:
            return False
        cwd = Path(session_cwd).expanduser().resolve()
        if cwd in requested_cwds:
            return True
        mapping = self._worktrees.retained_repository_mapping(cwd)
        if mapping is None:
            return False
        return any(
            requested.is_relative_to(mapping.root)
            and mapping.cwd.is_relative_to(requested)
            for requested in requested_cwds
        )

    def _session_cwd_matches(
        self, session_cwd: str | None, requested_cwd: Path
    ) -> bool:
        return self._session_cwd_matches_any(session_cwd, frozenset({requested_cwd}))

    async def watch_leases(
        self,
    ) -> AsyncGenerator[dict[str, SessionLeaseHolder | None]]:
        context = await self._read_context(SessionOptions())
        async with contextlib.aclosing(
            watch_session_leases(Path(context.storage_root))
        ) as leases:
            async for changes in leases:
                yield {
                    session_id: _lease_holder(holder)
                    for session_id, holder in changes.items()
                }

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        adapter = self._adapters.get(params.session_id)
        if adapter is not None and not adapter._closed:
            return await adapter.read(params)
        context = await self._read_context(SessionOptions())
        try:
            result = await _harness_call(
                self._host.read(_harness_probe_read_params(params))
            )
            snapshot = _without_probe(result.snapshot, params.history_limit)
            metadata = await asyncio.to_thread(
                _read_unified_session_metadata,
                _unified_session_dir(context.storage_root, snapshot.state.session.id),
            )
            pins = await _stored_session_pins(
                self._host, snapshot.state.session.id, context.build_agents
            )
            scheduled_loops = await asyncio.to_thread(
                read_stored_loops,
                _unified_session_dir(context.storage_root, snapshot.state.session.id)
                / "scheduled-loops.json",
            )
            return _read_response(
                snapshot,
                result.cwd,
                metadata=metadata,
                pins=pins,
                scheduled_loops=scheduled_loops,
                mcp_apps=None,
                started_by_session_id=result.started_by_session_id,
            )
        except SessionBackendError as exc:
            if exc.code is not ProtocolErrorCode.NOT_FOUND:
                raise
        # The unified store has no record of this session. If it is a legacy
        # session (listed by the merged ``list`` but not yet imported), read it
        # directly from the legacy store so the picker preview works.
        legacy_response = await asyncio.to_thread(
            LegacySessionStore(context.config_orchestrator.config).read_session, params
        )
        if legacy_response is not None:
            return legacy_response
        raise SessionBackendError(
            ProtocolErrorCode.NOT_FOUND, f"Session not found: {params.session_id}"
        )

    async def list_history(
        self, params: SessionHistoryListParams
    ) -> SessionHistoryListResponse:
        adapter = self._adapters.get(params.session_id)
        workspace_roots = (
            adapter._workspace_roots()
            if adapter is not None and not adapter._closed
            else ()
        )
        scheduled_loops = (
            adapter._scheduled_loops.snapshot()
            if adapter is not None and not adapter._closed
            else ()
        )
        mcp_apps = (
            adapter._mcp_apps if adapter is not None and not adapter._closed else None
        )

        async def read(history_limit: int) -> PublicSessionState:
            result = await _harness_call(
                self._host.read(
                    HarnessSessionReadParams(
                        session_id=params.session_id, history_limit=history_limit + 1
                    )
                )
            )
            return _read_response(
                _without_probe(result.snapshot, history_limit),
                result.cwd,
                workspace_roots=workspace_roots,
                scheduled_loops=scheduled_loops,
                mcp_apps=mcp_apps,
                started_by_session_id=result.started_by_session_id,
            ).state

        return await self._history_windows.list(params, read)

    async def list_artifacts(
        self, params: ArtifactsListParams
    ) -> ArtifactsListResponse:
        """The session's artifacts, answered without resuming a runtime.

        A session attached on this channel answers from its live service; a
        stored session answers from its persisted metadata, the way ``read``
        answers without a runtime. A session with no persisted artifacts
        answers empty — the namespace exists so clients do not branch on the
        session's attachment state.
        """
        adapter = self._adapters.get(params.session_id)
        if adapter is not None and not adapter._closed:
            result = await adapter._artifact_handler().dispatch(
                "artifacts/list", params.model_dump(mode="json", by_alias=True)
            )
            return cast(ArtifactsListResponse, result.response)
        context = await self._read_context(SessionOptions())
        service = await self._stored_artifact_service(params.session_id, context)
        # A client lists the sessions it shows: keep their PRs fresh.
        self._watch_stored(params.session_id, service, context)
        return ArtifactsListResponse(
            artifacts=[to_public_artifact(a) for a in service.list_artifacts()],
            worktrees=[to_public_worktree(w) for w in service.list_worktrees()],
        )

    async def mark_artifacts_viewed(
        self, params: ArtifactsMarkViewedParams
    ) -> EmptyResponse:
        """Mark artifacts viewed without resuming a runtime.

        An attached session answers through its live service, so the update
        is announced on the session's event stream; a stored session updates
        ``meta.json`` directly and the next attach loads it (``load``).
        """
        adapter = self._adapters.get(params.session_id)
        if adapter is not None and not adapter._closed:
            result = await adapter._artifact_handler().dispatch(
                "artifacts/markViewed", params.model_dump(mode="json", by_alias=True)
            )
            return cast(EmptyResponse, result.response)
        # The service is built per call, so the lock — not the service —
        # carries the serialization across load→mark→persist.
        lock = self._stored_artifact_locks.get(params.session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._stored_artifact_locks[params.session_id] = lock
        async with lock:
            service = await self._stored_artifact_service(
                params.session_id, await self._read_context(SessionOptions())
            )
            await service.mark_viewed(params.artifact_ids)
        return EmptyResponse()

    async def sync_artifacts(self, params: ArtifactsSyncParams) -> EmptyResponse:
        """Read the session's PRs/MRs from their provider at once, after the
        cheap check (no full read when nothing changed).

        The reads arrive as ``artifacts/updated``: on the session's event
        stream when it is attached, to ``artifacts/subscribe`` otherwise.
        """
        adapter = self._adapters.get(params.session_id)
        if adapter is not None and not adapter._closed:
            adapter.refresh_pull_requests(full=False)
            return EmptyResponse()
        context = await self._read_context(SessionOptions())
        service = await self._stored_artifact_service(params.session_id, context)
        if self._watch_stored(params.session_id, service, context):
            pull_request_cache().refresh(
                (key for key, _details in service.watched()), full=False
            )
        return EmptyResponse()

    def _watch_stored(
        self,
        session_id: str,
        service: SessionArtifactService,
        context: UnifiedReadContext,
    ) -> bool:
        """Keep a stored session's PRs fresh for the client's subscription.

        Without a subscription on this channel, nothing would receive the
        reads: nothing is watched. With PR monitoring off, every session the
        subscription watches is dropped, in case the setting was turned off
        since they were listed. The setting comes from the host's read
        context, so a stored session's own project config does not apply
        here (VIBE-5613).
        """
        subscription = self._artifact_subscription
        if subscription is None:
            return False
        if not context.config_orchestrator.config.enable_pr_monitoring:
            pull_request_cache().unwatch_all(subscription)
            return False
        subscription.storage_root = context.storage_root
        pull_request_cache().watch(subscription, session_id, service.watched())
        return True

    def subscribe_artifacts(
        self, notify: Callable[[str, ArtifactUpdatedParams], None]
    ) -> Callable[[], None]:
        """Announce each new read of the PRs/MRs of the stored sessions the
        client lists or syncs (``list_artifacts``, ``sync_artifacts``).

        The sessions attached in this process announce on their own event
        stream. Returns the unsubscribe function, which drops the watches.
        """
        cache = pull_request_cache()
        previous = self._artifact_subscription
        if previous is not None:
            cache.release(previous)
        subscription = _ArtifactSubscription()
        self._artifact_subscription = subscription

        async def announce(session_id: str, external_key: str) -> None:
            storage_root = subscription.storage_root
            if storage_root is None:
                return
            # The artifact as stored now: a viewed mark or another process
            # may have changed it since it was listed.
            service = await asyncio.to_thread(
                _stored_session_artifacts, storage_root, session_id
            )
            for artifact in service.list_artifacts():
                if artifact.external_key == external_key:
                    notify(
                        "artifacts/updated",
                        ArtifactUpdatedParams(
                            event_id=0,
                            session_id=session_id,
                            emitted_at=now_ms(),
                            artifact=to_public_artifact(artifact),
                        ),
                    )

        def read(update: Read) -> None:
            if isinstance(update.entry, ReadFailure):
                return
            task = asyncio.create_task(
                announce(update.session_id, update.entry.external_key)
            )
            subscription.announcements.add(task)
            task.add_done_callback(_announced(subscription.announcements))

        cache.own(subscription, read, attached=False)

        def unsubscribe() -> None:
            cache.release(subscription)
            for task in subscription.announcements:
                task.cancel()
            if self._artifact_subscription is subscription:
                self._artifact_subscription = None

        return unsubscribe

    async def _stored_artifact_service(
        self, session_id: str, context: UnifiedReadContext
    ) -> SessionArtifactService:
        """A cold read/write surface over the session's persisted metadata.

        The same lifecycle the live adapter runs — load from ``meta.json``,
        best-effort read-modify-write persist — minus the notification
        surface and telemetry: a stored session has no attached client on
        this channel to announce to.
        """
        return await asyncio.to_thread(
            _stored_session_artifacts, context.storage_root, session_id
        )

    async def read_config(self, params: ConfigReadParams) -> ConfigReadResponse:
        """The configuration a stored session runs, without resuming it.

        A client renders its pickers from this long before it attaches, so the
        answer is the session's rather than the host's: the configuration a
        resume would build, put on the same pins. Built from the read context,
        which loads no plugin, MCP server or connector: a picker must not wait
        on the network to show a model.
        """
        if params.session_id is None:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, "A session id is required"
            )
        # The first build is what configures the Host's storage, so the stored
        # cwd is only knowable after it. A session kept elsewhere is answered
        # from a context rebuilt against its own, the way a resume is.
        options = SessionOptions(cwd=params.cwd)
        context = await self._read_context(options)
        stored_cwd = await _harness_call(self._host.session_cwd(params.session_id))
        if stored_cwd is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {params.session_id}"
            )
        if stored_cwd != _session_cwd(options):
            context = await self._read_context(_with_session_cwd(options, stored_cwd))
        await self._restore_session_pins(params.session_id, _ReadPinTarget(context))
        return await _config_read_response(
            context.config_orchestrator, context.harness_files
        )

    async def relocate(
        self, params: SessionRelocateParams, attached: SessionBackend | None
    ) -> SessionRelocateResponse:
        """Move one root Session to a counterpart checkout without changing its id."""
        backend = self._relocation_backend(params.session_id, attached)
        if backend is None:
            return await self._relocate_reserved(params, None)
        async with backend.relocation_reservation():
            return await self._relocate_reserved(params, backend)

    async def _relocate_reserved(
        self,
        params: SessionRelocateParams,
        backend: UnifiedHarnessBackendAdapter | None,
    ) -> SessionRelocateResponse:
        previous_cwd, trust_store = await self._relocation_source(
            params.session_id, backend
        )
        destination = await self._relocation_destination(params.cwd, previous_cwd)
        if destination.target == previous_cwd:
            return await self._unchanged_relocation(params.session_id, backend)
        prepared = await self._prepare_relocation(
            params.session_id, backend, previous_cwd, destination, trust_store
        )
        try:
            relocated = await self._commit_relocation(params.session_id, prepared)
        except BaseException:
            await self._rollback_relocation(params.session_id, prepared)
            raise
        response = await self._finish_relocation(params.session_id, prepared, relocated)
        self._merged_listing = None
        return SessionRelocateResponse(state=response.state)

    async def move(
        self, params: SessionMoveParams, attached: SessionBackend | None
    ) -> SessionMoveResponse:
        """Restamp one root Session and move it to its new project's primary folder."""
        context = await self._read_context(SessionOptions())
        if not context.config.experimental_enable_local_projects:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                "Moving a session needs local projects",
            )
        stamp, folder = await asyncio.to_thread(_move_target, params.project)
        backend = self._relocation_backend(params.session_id, attached)
        # The legacy store is read-only: opening a legacy session imports it.
        if backend is None and await self._only_in_legacy_store(
            params.session_id, context
        ):
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                f"Open the session before moving it: {params.session_id}",
            )
        if backend is None:
            await self._move_reserved(
                params.session_id, None, stamp, folder, context.storage_root
            )
        else:
            async with backend.relocation_reservation():
                await self._move_reserved(
                    params.session_id, backend, stamp, folder, context.storage_root
                )
        self._merged_listing = None
        read = await self._unchanged_relocation(params.session_id, backend)
        return SessionMoveResponse(state=read.state)

    async def _only_in_legacy_store(
        self, session_id: str, context: UnifiedReadContext
    ) -> bool:
        unified_dir = _unified_session_dir(context.storage_root, session_id)
        if await asyncio.to_thread(unified_dir.is_dir):
            return False
        return await asyncio.to_thread(
            LegacySessionStore(context.config).has_session, session_id
        )

    async def _move_reserved(
        self,
        session_id: str,
        backend: UnifiedHarnessBackendAdapter | None,
        stamp: SessionProject,
        folder: Path | None,
        storage_root: str,
    ) -> None:
        adapter = backend or self._adapters.get(session_id)
        open_here = adapter is not None and not adapter._closed
        # Held elsewhere, it would take the new folder but not the new stamp.
        if not open_here:
            await self._require_unheld(storage_root, session_id)
        # A projectless session keeps its folder until it gets a scratch one (DSK-1822).
        if folder is not None:
            previous_cwd, trust_store = await self._relocation_source(
                session_id, backend
            )
            target, changes_worktree = await asyncio.to_thread(
                self._worktrees.relocation_target, str(folder), previous_cwd
            )
            if target != previous_cwd:
                if not await asyncio.to_thread(target.is_dir):
                    raise SessionBackendError(
                        ProtocolErrorCode.INVALID_PARAMS, f"Not a directory: {target}"
                    )
                # Unlike a relocation, the folder is the project's, not a checkout
                # of the session's repository.
                destination = _RelocationDestination(
                    target=target, checkout=target, changes_worktree=changes_worktree
                )
                prepared = await self._prepare_relocation(
                    session_id, backend, previous_cwd, destination, trust_store
                )
                try:
                    relocated = await self._commit_relocation(session_id, prepared)
                except BaseException:
                    await self._rollback_relocation(session_id, prepared)
                    raise
                await self._finish_relocation(session_id, prepared, relocated)
        if adapter is not None and open_here:
            await adapter._move_to_project(stamp)
            return
        await self._move_closed_session(storage_root, session_id, stamp)

    @staticmethod
    async def _require_unheld(storage_root: str, session_id: str) -> None:
        lease = SessionLease(Path(storage_root), session_id)
        try:
            await asyncio.to_thread(lease.acquire)
        except HarnessSessionBusyError as exc:
            raise _peer_refusal(PEER_REFUSAL_BUSY, _MOVE_HELD_ELSEWHERE) from exc
        await asyncio.to_thread(lease.release)

    async def _move_closed_session(
        self, storage_root: str, session_id: str, stamp: SessionProject
    ) -> None:
        async with self._closed_metadata_lock(session_id):
            # An open session would save its metadata over the new stamp.
            lease = SessionLease(Path(storage_root), session_id)
            try:
                await asyncio.to_thread(lease.acquire)
            except HarnessSessionBusyError as exc:
                raise _peer_refusal(PEER_REFUSAL_BUSY, _MOVE_HELD_ELSEWHERE) from exc
            try:
                await self._write_closed_stamp(
                    storage_root, session_id, stamp, replace=True
                )
            finally:
                await asyncio.to_thread(lease.release)

    async def _relocation_source(
        self, session_id: str, backend: UnifiedHarnessBackendAdapter | None
    ) -> tuple[Path, TrustedFoldersManager]:
        if backend is not None:
            return (
                self._required_relocation_cwd(session_id, backend.cwd),
                backend._context.harness_files.trust_store,
            )
        lookup_context, _ = await self._lifecycle_context(
            SessionOptions(), require_api_key=False
        )
        cwd = await _harness_call(self._host.session_cwd(session_id))
        if cwd is None:
            # ``session_cwd`` deliberately returns ``None`` both for an unknown
            # Session and for an old store with no workspace. Let ``read``
            # distinguish those cases before reporting the latter as invalid.
            await _harness_call(
                self._host.read(
                    HarnessSessionReadParams(session_id=session_id, history_limit=0)
                )
            )
        return (
            self._required_relocation_cwd(session_id, cwd),
            lookup_context.harness_files.trust_store,
        )

    async def _relocation_destination(
        self, requested: str, previous_cwd: Path
    ) -> _RelocationDestination:
        target, changes_worktree = await asyncio.to_thread(
            self._worktrees.relocation_target, requested, previous_cwd
        )
        if target == previous_cwd:
            return _RelocationDestination(
                target=target, checkout=target, changes_worktree=False
            )
        if not await asyncio.to_thread(target.is_dir):
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, f"Not a directory: {target}"
            )
        checkout = await asyncio.to_thread(
            self._worktrees.relocation_checkout, previous_cwd, target
        )
        if checkout is None:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                f"Not a worktree of this session's repository: {target}",
            )
        return _RelocationDestination(
            target=target, checkout=checkout, changes_worktree=changes_worktree
        )

    async def _unchanged_relocation(
        self, session_id: str, backend: UnifiedHarnessBackendAdapter | None
    ) -> SessionRelocateResponse:
        params = SessionReadParams(
            session_id=session_id, history=PageRequest(limit=_RELOCATION_HISTORY_LIMIT)
        )
        read = (
            await backend.read(params)
            if backend is not None
            else await self.read(params)
        )
        return SessionRelocateResponse(state=read.state)

    async def _prepare_relocation(
        self,
        session_id: str,
        backend: UnifiedHarnessBackendAdapter | None,
        previous_cwd: Path,
        destination: _RelocationDestination,
        trust_store: TrustedFoldersManager,
    ) -> _PreparedRelocation:
        if destination.changes_worktree:
            await asyncio.to_thread(
                self._worktrees.hold,
                destination.target,
                session_id,
                None,
                None if backend is None else backend._joining_session_dir(),
            )
        try:
            trust_store.trust_for_session(destination.checkout)
            options = self._relocation_options(
                backend, previous_cwd, destination.target
            )
            context, _ = await self._lifecycle_context(options, require_api_key=True)
            await self._restore_session_pins(session_id, context)
            if backend is not None:
                context.completion_budget.adopt(backend._context.completion_budget)
                await _run_carried_variants(backend._context, context)
            settings = (
                backend._settings if backend is not None else UnifiedSessionSettings()
            )
            derivation = await asyncio.to_thread(context.derive, settings)
            # Do repository I/O before Runtime commits. Adoption must publish
            # matching context and derivation without a cancellation point.
            git_trust = await asyncio.to_thread(
                _relocated_worktree_git_trust,
                previous_cwd,
                destination.target,
                trust_store if backend is None else backend._worktree_trust_store,
                context.harness_files.trust_store,
            )
            self._host.configure_runtime(
                derivation.core_config, adapter_config=derivation.adapter_config
            )
            return _PreparedRelocation(
                backend=backend,
                previous_cwd=previous_cwd,
                destination=destination,
                context=context,
                derivation=derivation,
                trust_store=trust_store,
                git_trust_store=git_trust,
            )
        except BaseException as exc:
            try:
                trust_store.revoke_session_trust(destination.checkout)
            except BaseException as rollback_error:
                exc.add_note(f"Failed to revoke destination trust: {rollback_error}")
            if destination.changes_worktree:
                try:
                    await asyncio.to_thread(
                        self._worktrees.release,
                        destination.target,
                        session_id,
                        trust_store=trust_store,
                    )
                except BaseException as rollback_error:
                    exc.add_note(
                        f"Failed to release destination hold: {rollback_error}"
                    )
            await self._restore_host_after_failed_relocation(backend)
            raise

    async def _commit_relocation(
        self, session_id: str, prepared: _PreparedRelocation
    ) -> HarnessSessionReadResult:
        timestamp = now_ms()
        checkpoint = PublicCheckpointEntry(
            id=f"checkpoint:relocation:{uuid4()}",
            session_id=session_id,
            created_at=timestamp,
            updated_at=timestamp,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            kind="relocation",
            message=f"Moved to {prepared.destination.target}",
            details={
                "cwd": str(prepared.destination.target),
                "previousCwd": str(prepared.previous_cwd),
            },
        )
        return await _harness_call(
            self._host.relocate(
                session_id,
                config=prepared.derivation.session_config,
                checkpoint=cast(
                    dict[str, JsonValue],
                    checkpoint.model_dump(mode="json", by_alias=True),
                ),
                history_limit=_RELOCATION_HISTORY_LIMIT,
                hook_bindings=prepared.context.hooks.bindings,
                hook_handlers=prepared.context.hooks.handlers,
            )
        )

    async def _rollback_relocation(
        self, session_id: str, prepared: _PreparedRelocation
    ) -> None:
        with contextlib.suppress(Exception):
            prepared.trust_store.revoke_session_trust(prepared.destination.checkout)
        if prepared.destination.changes_worktree:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self._worktrees.release,
                    prepared.destination.target,
                    session_id,
                    trust_store=prepared.trust_store,
                )
        await self._restore_host_after_failed_relocation(prepared.backend)

    async def _restore_host_after_failed_relocation(
        self, backend: UnifiedHarnessBackendAdapter | None
    ) -> None:
        """Restore attached-session templates after temporary destination preparation."""
        source = backend or next(
            (adapter for adapter in self._adapters.values() if not adapter._closed),
            None,
        )
        if source is None or source._closed:
            return
        try:
            context = source._context
            derivation = await source.derive_configuration()
            self._host.configure_runtime(
                derivation.core_config, adapter_config=derivation.adapter_config
            )
            self._host.configure_plugins(
                context.plugin_provider, context.requested_plugins
            )
            context.provided_names.register(VIBE_TOOL_GROUP, VIBE_PROVIDED_TOOL_NAMES)
            self._host.configure_provided_tool_executor(
                [VIBE_TOOL_GROUP],
                context.vibe_tools.executor_factory(
                    context.storage_root,
                    scheduled_loops=self.cron_scheduler,
                    session_spawner=context.session_spawner,
                    session_origin=self.spawn_origin,
                    session_peers=context.session_peers,
                    hops_of=self.message_hops,
                    sender_of=self.sender_of,
                    starter_of=self.started_by,
                    turn_starts=TurnStarts(
                        of=self.turn_started_sessions,
                        count=self.count_turn_started_session,
                    ),
                    enter_worktree=self.enter_worktree,
                    max_todos=lambda: resolved_max_todos(
                        context.config_orchestrator.config.tools.get(TODO_TOOL_NAME)
                    ),
                ),
                mode=VIBE_PROVIDED_TOOL_MODES,
            )
            self._publish_mcp_catalog(context, context.mcp_catalog)
            self._configure_connectors(
                context, source._connector_catalog, source._connector_selection
            )
        except Exception:
            logger.warning(
                "Failed to restore Host configuration after rejected relocation",
                exc_info=True,
            )

    async def _finish_relocation(
        self,
        session_id: str,
        prepared: _PreparedRelocation,
        relocated: HarnessSessionReadResult,
    ) -> SessionReadResponse:
        backend = prepared.backend
        if backend is None:
            # A detached relocation has no live loop store.
            response = _read_response(
                relocated.snapshot,
                relocated.cwd,
                scheduled_loops=(),
                started_by_session_id=relocated.started_by_session_id,
                mcp_apps=None,
            )
            await self._persist_detached_relocation_metadata(
                prepared.context, session_id, relocated
            )
            with contextlib.suppress(Exception):
                prepared.trust_store.revoke_session_trust(prepared.destination.checkout)
            if prepared.destination.changes_worktree:
                try:
                    await asyncio.to_thread(
                        self._worktrees.release,
                        prepared.previous_cwd,
                        session_id,
                        trust_store=prepared.trust_store,
                    )
                except Exception:
                    logger.warning(
                        "Failed to release the previous worktree after relocation",
                        exc_info=True,
                    )
            await self._restore_host_after_failed_relocation(None)
            return response

        # The move cancels the eval in flight and starts it again below: until
        # then a /whoami reconcile on the new context waits for the retry.
        with prepared.context.experiments_init_gate.held():
            return await self._finish_adopted_relocation(
                session_id, prepared, relocated, backend
            )

    async def _finish_adopted_relocation(
        self,
        session_id: str,
        prepared: _PreparedRelocation,
        relocated: HarnessSessionReadResult,
        backend: UnifiedHarnessBackendAdapter,
    ) -> SessionReadResponse:
        await backend.adopt_relocated_context(
            prepared.context, prepared.derivation, git_trust=prepared.git_trust_store
        )
        try:
            await self._refresh_relocated_integrations(backend, prepared.context)
        except Exception:
            logger.warning(
                "Failed to finish integration refresh after session relocation",
                exc_info=True,
            )
        await backend._persist_session_metadata_best_effort()
        response = backend._read_response(relocated.snapshot)
        if prepared.destination.changes_worktree:
            try:
                await asyncio.to_thread(
                    self._worktrees.release,
                    prepared.previous_cwd,
                    session_id,
                    trust_store=prepared.trust_store,
                )
            except Exception:
                logger.warning(
                    "Failed to release the previous worktree after relocation",
                    exc_info=True,
                )
        try:
            backend.replace_relocation_trust(prepared.destination.checkout)
        except Exception:
            logger.warning(
                "Failed to rotate session trust after relocation", exc_info=True
            )
        try:
            self._start_experiments(backend)
        except Exception:
            logger.warning(
                "Failed to start background refresh after session relocation",
                exc_info=True,
            )
        backend.schedule_pr_discovery()
        return response

    async def _persist_detached_relocation_metadata(
        self,
        context: UnifiedSessionContext,
        session_id: str,
        relocated: HarnessSessionReadResult,
    ) -> None:
        """Refresh the Vibe-owned sidecar after the Harness commit."""
        session_dir = _unified_session_dir(context.storage_root, session_id)
        try:
            async with self._closed_metadata_lock(session_id):
                await _persist_unified_session_metadata(
                    session_dir,
                    relocated.snapshot.state.session,
                    relocated.cwd,
                    previous=await asyncio.to_thread(
                        _read_unified_session_metadata, session_dir
                    ),
                )
        except Exception:
            logger.warning(
                "Failed to persist Unified session metadata after relocation",
                exc_info=True,
            )

    @staticmethod
    async def _refresh_relocated_integrations(
        backend: UnifiedHarnessBackendAdapter, context: UnifiedSessionContext
    ) -> None:
        """Rebind live integration routes after relocation has committed."""
        try:
            await backend.reconfigure_mcp(
                context.mcp_catalog, force_remote_discovery=False
            )
            await backend.initialize_integrations()
        except Exception:
            logger.warning(
                "Failed to refresh MCP routes after session relocation", exc_info=True
            )
        if (
            context.connector_catalog is not None
            and context.connector_selection is not None
        ):
            try:
                await backend.reconfigure_connectors(
                    context.connector_catalog, context.connector_selection, force=False
                )
            except Exception:
                logger.warning(
                    "Failed to refresh connector routes after session relocation",
                    exc_info=True,
                )
        backend._start_connector_resolve()

    @staticmethod
    def _relocation_backend(
        session_id: str, attached: SessionBackend | None
    ) -> UnifiedHarnessBackendAdapter | None:
        if attached is None:
            return None
        if not isinstance(attached, UnifiedHarnessBackendAdapter):
            raise TypeError("Unified relocation requires a Unified Harness session")
        if attached.session_id != session_id:
            raise TypeError("Attached relocation session does not match the request")
        return attached

    @staticmethod
    def _required_relocation_cwd(session_id: str, cwd: str | None) -> Path:
        if cwd is None:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                f"Session has no working directory: {session_id}",
            )
        return Path(cwd).expanduser().resolve()

    @staticmethod
    def _relocation_options(
        backend: UnifiedHarnessBackendAdapter | None, previous_cwd: Path, target: Path
    ) -> SessionOptions:
        if backend is None:
            return SessionOptions(cwd=str(target))
        extra_roots = [
            str(root)
            for root in backend._adapter_config.workspace.roots
            if root not in {previous_cwd, target}
        ]
        return SessionOptions(
            cwd=str(target),
            workspace_roots=extra_roots,
            headless=not backend._scheduled_loops_enabled,
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
                async with self._closed_metadata_lock(params.session_id):
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
        async with self._closed_metadata_lock(params.session_id):
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

    async def archive(self, params: SessionArchiveParams) -> SessionArchiveResponse:
        response = await self._archive_one(params)
        if not params.archived or not params.include_started_sessions:
            return response
        return response.model_copy(
            update={
                "started_sessions": await self._archive_started_sessions(
                    params.session_id
                )
            }
        )

    async def _archive_one(
        self, params: SessionArchiveParams
    ) -> SessionArchiveResponse:
        held = await self._open_sessions.archive(params)
        if held is not None:
            self._merged_listing = None
            return held
        return await self._archive_stored(params)

    async def _archive_started_sessions(
        self, starter_id: str
    ) -> StartedSessionsArchived:
        """Archives, down the chain, the sessions ``starter_id`` started, each
        requiring idle. A pinned one, one in another project, a working one or
        one that fails stays, with the sessions it started.
        """
        # Archived ones too: the starter is, by now.
        listing = await self._build_merged_listing(
            SessionListParams(include_archived=True),
            _MergedListingKey(
                cwd=None,
                cwds=None,
                root_session_id=None,
                parent_session_id=None,
                pinned=None,
                include_archived=True,
            ),
        )
        by_id = {session.id: session for session in listing.sessions}
        started_by: dict[str, list[PublicSession]] = {}
        for session in listing.sessions:
            # One archived already is left out with the sessions it started.
            if (
                session.started_by_session_id is not None
                and session.archived_at is None
            ):
                started_by.setdefault(session.started_by_session_id, []).append(session)
        project = by_id[starter_id].project if starter_id in by_id else None
        result = StartedSessionsArchived()
        visited = {starter_id}
        level = [starter_id]
        while level:
            children = [
                child
                for starter in level
                for child in started_by.get(starter, [])
                if child.id not in visited
            ]
            visited.update(child.id for child in children)
            outcomes = await asyncio.gather(
                *(self._archive_started_session(child, project) for child in children)
            )
            level = []
            for child, skipped in zip(children, outcomes, strict=True):
                if skipped is None:
                    result.archived.append(child.id)
                    level.append(child.id)
                else:
                    result.skipped.append(
                        StartedSessionSkipped(session_id=child.id, reason=skipped)
                    )
        return result

    async def _archive_started_session(
        self, session: PublicSession, project: PublicSessionProject | None
    ) -> StartedSessionSkipReason | None:
        """None once archived, otherwise why it stays."""
        if session.pinned_at is not None:
            return "pinned"
        if (
            project is not None
            and session.project is not None
            and session.project != project
        ):
            return "other_project"
        try:
            await self._archive_one(
                SessionArchiveParams(
                    session_id=session.id, archived=True, require_idle=True
                )
            )
        except SessionBackendError as exc:
            if (
                exc.code is ProtocolErrorCode.CONFLICT
                and isinstance(exc.data, dict)
                and exc.data.get("refusal") == PEER_REFUSAL_BUSY
            ):
                return "busy"
            logger.warning("Could not archive a started session", exc_info=exc)
            return "failed"
        except Exception as exc:
            logger.warning("Could not archive a started session", exc_info=exc)
            return "failed"
        return None

    async def archive_open(
        self, params: SessionArchiveParams
    ) -> SessionArchiveResponse | None:
        """None when this Host does not hold the session open."""
        archived_at = datetime.now(UTC) if params.archived else None
        adapter = self._adapters.get(params.session_id)
        if adapter is not None and not adapter._closed:
            async with await adapter._archive_gate_once_settled(params):
                if params.require_idle and adapter._session.has_active_work:
                    raise _peer_refusal(PEER_REFUSAL_BUSY, "The session is working.")
                metadata = await adapter._persist_session_metadata(
                    archived_at=archived_at,
                    pinned_at=None if params.archived else _UNCHANGED,
                )
            adapter._publish_archive_metadata(metadata)
            self._merged_listing = None
            await self._schedule_archived_worktree(
                params,
                [str(held) for held in adapter._held_worktrees()],
                adapter._context.harness_files.trust_store,
            )
            return SessionArchiveResponse(
                archived_at=_metadata_archived_at_ms(metadata)
            )
        return None

    async def _archive_stored(
        self, params: SessionArchiveParams
    ) -> SessionArchiveResponse:
        archived_at = datetime.now(UTC) if params.archived else None
        adapter = self._adapters.get(params.session_id)
        if adapter is not None:
            storage_root = adapter._context.storage_root
            trust_store = adapter._context.harness_files.trust_store
            self._configure_storage_root(storage_root)
        else:
            listing = self._merged_listing
            listed_session = (
                next(
                    (
                        session
                        for session in listing.sessions
                        if session.id == params.session_id
                    ),
                    None,
                )
                if listing is not None
                else None
            )
            live_adapter = next(
                (
                    candidate
                    for candidate in self._adapters.values()
                    if not candidate._closed
                ),
                None,
            )
            if listed_session is None and live_adapter is not None:
                trust_store = live_adapter._context.harness_files.trust_store
                storage_root = live_adapter._context.storage_root
                self._configure_storage_root(storage_root)
                options = None
            else:
                options = SessionOptions(
                    cwd=listed_session.cwd if listed_session is not None else None
                )
                context = await self._read_context(options)
                trust_store = context.harness_files.trust_store
                storage_root = context.storage_root
            stored_cwd = await _harness_call(self._host.session_cwd(params.session_id))
            if stored_cwd is None:
                raise SessionBackendError(
                    ProtocolErrorCode.NOT_FOUND,
                    f"Session not found: {params.session_id}",
                )
            if options is not None and stored_cwd != _session_cwd(options):
                context = await self._read_context(
                    _with_session_cwd(options, stored_cwd)
                )
                storage_root = context.storage_root

        session_dir = _unified_session_dir(storage_root, params.session_id)
        snapshot = await _harness_call(
            self._host.read(
                HarnessSessionReadParams(session_id=params.session_id, history_limit=1)
            )
        )
        # The lease, held only while the archive is saved, keeps a process from
        # opening the session from the metadata before it, so none queues a turn
        # on it or saves the archive away. A process that already holds it runs
        # the session: refused when idle is required, otherwise saved as before.
        # Taken before the lease, so a stamp this process holds it for is waited on.
        async with self._closed_metadata_lock(params.session_id):
            lease = SessionLease(Path(storage_root), params.session_id)
            try:
                await asyncio.to_thread(lease.acquire)
            except HarnessSessionBusyError as exc:
                if params.require_idle:
                    raise _peer_refusal(
                        PEER_REFUSAL_BUSY, "Another process runs the session."
                    ) from exc
            try:
                metadata = await _persist_unified_session_metadata(
                    session_dir,
                    snapshot.snapshot.state.session,
                    snapshot.cwd,
                    previous=await asyncio.to_thread(
                        _read_unified_session_metadata, session_dir
                    ),
                    archived_at=archived_at,
                    pinned_at=None if params.archived else _UNCHANGED,
                )
            finally:
                await asyncio.to_thread(lease.release)
        self._merged_listing = None
        await self._schedule_archived_worktree(
            params,
            [
                *(w.path for w in metadata.worktrees),
                *([snapshot.cwd] if snapshot.cwd is not None else []),
            ],
            trust_store,
        )
        return SessionArchiveResponse(archived_at=_metadata_archived_at_ms(metadata))

    async def _schedule_archived_worktree(
        self,
        params: SessionArchiveParams,
        worktrees: Sequence[str],
        trust_store: TrustedFoldersManager,
    ) -> None:
        if not params.archived:
            return
        for cwd in dict.fromkeys(worktrees):
            await asyncio.to_thread(
                self._worktrees.schedule_reap, Path(cwd), trust_store=trust_store
            )

    async def mark_as_seen(self, params: SessionMarkAsSeenParams) -> EmptyResponse:
        adapter = self._adapters.get(params.session_id)
        if adapter is not None and not adapter._closed:
            metadata = await adapter._mark_session_seen(datetime.now(UTC))
            adapter._publish_seen_state(metadata)
            return EmptyResponse()

        context = await self._read_context(SessionOptions())
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
            legacy = SessionLoader.find_session_by_id(
                params.session_id, context.config_orchestrator.config.session_logging
            )
            if legacy is None:
                raise
            return EmptyResponse()
        async with self._closed_metadata_lock(params.session_id):
            previous = await asyncio.to_thread(
                _read_unified_session_metadata, session_dir
            )
            metadata = _build_unified_session_metadata(
                snapshot.snapshot.state.session, snapshot.cwd, previous=previous
            )
            seen_state = SessionSeenState.from_metadata(previous).mark_seen(
                datetime.now(UTC)
            )
            await _write_unified_session_metadata(
                session_dir, seen_state.apply_to(metadata)
            )
        return EmptyResponse()

    async def delete(self, params: SessionDeleteParams) -> EmptyResponse:
        context = await self._read_context(SessionOptions())
        live = self._adapters.get(params.session_id)
        if live is not None:
            if not live._announced.is_set():
                # A start whose first turn failed: the client never got this
                # session, so it does not end for the hooks either.
                live.abandon_session_hooks()
            elif ending := live.end_session_hooks(SessionEndReason.OTHER):
                # Before the Harness deletes the transcript and releases the
                # plugin files they read. Bounded by the hooks' own timeouts.
                await asyncio.wait(ending)
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
        else:
            self._listed_rows.rows.pop(params.session_id, None)
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
        entry = await source._rewound_entry(params.entry_id)
        # Let the snapshots see every write of the settled turn before planning.
        await source.flush_events()
        source_anchors = _user_entry_ids(
            (await source._read_state_containing(params.entry_id)).history or []
        )
        derivation = await source.derive_configuration()
        self._host.configure_runtime(
            derivation.core_config, adapter_config=derivation.adapter_config
        )
        result = await _harness_call(
            self._host.fork(
                params.session_id,
                entry_id=params.entry_id,
                include_entry=False,
                history_limit=history_limit,
                # The source's current hooks, not the ones it persisted: a source
                # attached live may predate them (session_start's builtin).
                hook_bindings=source._context.hooks.bindings,
                hook_handlers=source._context.hooks.handlers,
            )
        )
        # Only once the fork landed: a failed fork leaves the disk untouched.
        restore_errors, restored_paths = (
            source._rewind_snapshots.restore(entry.id)
            if params.restore_files
            else ([], [])
        )
        backend = self._adapter(
            result.session,
            source._context,
            derivation,
            scheduled_loops_enabled=source._scheduled_loops_enabled,
            launch_context=source._launch_context,
            user_plan=source._user_plan,
            settings=source._settings,
            project=source._project,
        )
        await self._prepare_opened_backend(
            backend, backend.replace_scheduled_loops(await source.scheduled_loops())
        )
        await backend.seat_terminal(source.terminal_seat, record_now=False)
        # The fork runs on the source's context: an eval of its own would land
        # in the manager the source runs on too.
        if source._experiments_settled:
            backend._experiments_settled = True
            backend._start_model_catalog_fetch()
        else:
            self._start_experiments(backend)
        state = (
            await backend.read(
                SessionReadParams(
                    session_id=backend.session_id,
                    history=PageRequest(limit=history_limit),
                )
            )
        ).state
        # The fork inherits the snapshots of the turns its transcript keeps,
        # re-keyed onto its own entry ids (inherited entries lose their turn
        # id); both transcripts end at the rewound boundary, so they align from
        # the tail. The source keeps all of its snapshots.
        kept = source_anchors[: source_anchors.index(entry.id)]
        renamed = dict(
            # The two pages can hold different depths; only the aligned tail maps.
            zip(
                reversed(kept),
                reversed(_user_entry_ids(state.history or [])),
                strict=False,
            )
        )
        backend._rewind_snapshots = source._rewind_snapshots.forked(entry.id, renamed)
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
                message=entry.text,
                restore_errors=restore_errors,
                restored_paths=restored_paths,
                state=state.model_copy(update={"history": history}),
                session_log=await backend._session_log_summary(),
            ),
            # A rewind continues the conversation in a new session forked from it.
            after_response=self._after_open(
                backend, session_start=SessionStartSource.FORK
            ),
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
        derivation = await source.derive_configuration()
        self._host.configure_runtime(
            derivation.core_config, adapter_config=derivation.adapter_config
        )
        # The replacement carries the source's pinned plugins forward verbatim:
        # history/clear replaces the Core, not the pins.
        # Re-pinning here would digest the installed trees against the request
        # resolved at process start, so any plugin upgraded underneath the
        # running CLI — the shipped `vibe` builtin included — failed /clear
        # with `plugin_pin_mismatch`.
        plugin_lock = await source._session.read_plugin_lock()
        session = await _harness_call(
            self._host.start(
                HarnessSessionStartParams(history_limit=history_limit),
                cwd=source.cwd,
                ephemeral=True,
                hook_bindings=source._context.hooks.bindings,
                hook_handlers=source._context.hooks.handlers,
                plugin_lock=plugin_lock,
                started_by_session_id=source._session.started_by_session_id,
            )
        )
        backend = self._adapter(
            session,
            source._context,
            derivation,
            scheduled_loops_enabled=source._scheduled_loops_enabled,
            launch_context=source._launch_context,
            user_plan=source._user_plan,
            settings=source._settings,
            project=source._project,
        )
        # A cleared conversation starts with no schedules, like a new session.
        # The source keeps its own on disk, so resuming it restores them.
        await self._prepare_opened_backend(backend, backend.restore_scheduled_loops())
        await backend.seat_terminal(source.terminal_seat, record_now=False)
        # The clear_history replacement skips _start_experiments (it reuses
        # the source's resolved manager), so the catalog fetch needs its own
        # kick here: the source's in-flight fetch died with the source.
        backend._start_model_catalog_fetch()
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
        # The replacement Harness session holds none of the carried entries, so
        # no cursor could page older ones from it.
        history = history[-history_limit:]
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
        # Once the eval landed on the manager both share: a move after /clear
        # keeps it.
        backend._experiments_settled = source._experiments_settled
        # `/clear` starts a new root: legacy `_reset_session(keep_parent=False)`
        # leaves parent_session_id unset (unlike rewind, which chains to the old
        # session). The replacement's cached parent is already None from the open.
        backend._emit_new_session_telemetry()
        result = SessionHistoryClearResult(
            backend=backend,
            state=replacement_state.model_copy(
                update={"history": history, "history_before_cursor": None}
            ),
            session_log=await backend._session_log_summary(),
            # The replacement's session_start waits for the source's session_end.
            after_response=self._after_open(
                backend, session_start=SessionStartSource.CLEAR
            ),
            on_failure=lambda: source.end_with(SessionEndReason.OTHER),
        )
        # The server closes the source once it holds the replacement: that
        # close ends it with ``clear``. A clear failing keeps the source going.
        source.end_with(SessionEndReason.CLEAR)
        return result

    def mark_client_disconnected(self) -> None:
        """Report the coming close of every session as a lost client."""
        for adapter in self._adapters.values():
            adapter._client_disconnected = True

    async def shutdown(self) -> None:
        self._open_sessions.unregister(self)
        # `session/stop` arrives here rather than at the session adapter, so a
        # release left only to the adapter would never run on the ordinary way
        # out. Settle deferred worktree moves before reading adapter cwd: their
        # cancellation cleanup releases a prepared target that was announced but
        # not yet adopted, while a completed move leaves cwd pointing at the
        # target for the ordinary release below.
        await asyncio.gather(
            *(
                adapter._cancel_deferred_setup_task()
                for adapter in self._adapters.values()
            ),
            return_exceptions=True,
        )
        # Releasing a worktree never reaps it here. A requested reap snapshots
        # and deletes the whole checkout, which takes minutes for a large one,
        # and the client is waiting on this shutdown to acknowledge its close.
        # The requester sends its reap again once the close is acknowledged,
        # and the worktree prune completes any reap still left.
        self._release_worktrees()
        # The user quits: every main session ends with ``exit``, and their
        # session_end hooks start together, before the slower teardown below.
        for adapter in tuple(self._adapters.values()):
            adapter.end_session_hooks(SessionEndReason.EXIT)
        # The Host's hooks still running (session_end, notification,
        # session_start), deleted sessions' included, get one shared grace, then
        # are stopped. It runs alongside the adapter shutdowns, which stop the
        # running turns at once and start no new hook.
        host_hooks_drain = asyncio.ensure_future(self._host_hooks.drain())
        errors: list[BaseException] = []
        # Vibe owns schedulers, host shells and deferred setup in addition to
        # the Harness session. They must stop even when the OS process lives on.
        # A parent also closes its children: serialize the drain, as the Harness
        # host does, so child shutdown cannot be entered concurrently.
        for adapter in tuple(self._adapters.values()):
            try:
                await adapter.shutdown()
            except BaseException as exc:
                errors.append(exc)
        # Before the Harness close, which releases the plugin copies the hooks
        # run from.
        with contextlib.suppress(Exception):
            await host_hooks_drain
        try:
            if self._owns_host:
                await self._host.shutdown()
            else:
                await self._close_owned_sessions()
        except BaseException as exc:
            errors.append(exc)
        finally:
            # Finish lifecycle reporting even if an adapter failed its cleanup.
            for adapter in self._adapters.values():
                adapter._cancel_experiments_task()
                adapter._cancel_mcp_discovery_task()
                adapter._cancel_model_catalog_task()
                adapter._cancel_registry_sync()
                adapter._emit_session_closed_telemetry()
                if adapter._relocation_trust_root is not None:
                    adapter._context.harness_files.trust_store.revoke_session_trust(
                        adapter._relocation_trust_root
                    )
                    adapter._relocation_trust_root = None
            self._adapters.clear()
            clients = tuple(self._telemetry_clients)
            self._telemetry_clients.clear()
            await asyncio.gather(
                *(client.aclose() for client in clients), return_exceptions=True
            )
            # After the Harness close, so nothing still holds the store open.
            if self._release_storage is not None:
                await asyncio.to_thread(self._release_storage)
        if errors:
            raise BaseExceptionGroup("Failed to shut down session adapters", errors)

    async def _close_owned_sessions(self) -> None:
        errors: list[BaseException] = []
        for adapter in self._adapters.values():
            try:
                await self._host.close_session(adapter._session)
            except BaseException as exc:
                errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close Unified Harness sessions", errors)

    def _release_worktrees(self) -> None:
        for backend in self._adapters.values():
            for held in backend._held_worktrees():
                self._worktrees.release(
                    held,
                    backend.session_id,
                    trust_store=backend._context.harness_files.trust_store,
                )

    def _adapter(
        self,
        session: UnifiedHarnessSessionBackend,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        scheduled_loops_enabled: bool,
        launch_context: LaunchContext | None = None,
        user_plan: str | None = None,
        settings: UnifiedSessionSettings | None = None,
        project: SessionProject | None = None,
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
            credentials=context.platform_credentials,
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
            api=_session_api_commands(session, self._host, derivation),
            deferred_turns=session,
            host=self._host,
            services=self._services,
            telemetry_client=telemetry,
            session_replaced=self._session_replaced,
            rewrite_plugins=self._host.rewrite_session_plugins,
            scheduled_loops_enabled=scheduled_loops_enabled,
            launch_context=launch_context,
            user_plan=user_plan,
            settings=settings,
            project=project,
            # This session's own attribution. The adapter binds it into every
            # derivation it adopts, so a fork never reaches back into this one.
            completion_attribution=build_completion_attribution(
                telemetry,
                telemetry_launch_context,
                message_id_getter=lambda: message_id_holder[0],
            ),
            worktree_housekeeping=self._worktree_housekeeping,
        )
        adapter._message_id_holder = message_id_holder
        self._adapters[session.session_id] = adapter
        return adapter

    async def cron_scheduler(self, session_id: str) -> UnifiedScheduledLoops:
        """The calling agent's own schedules: a subagent never sees its root's."""
        adapter = self._adapter_of(session_id)
        if adapter is None:
            raise ValueError("Scheduled prompts are not available for this session")
        if adapter.session_id == session_id:
            return adapter.cron_scheduler()
        return await adapter.child_cron_scheduler(session_id)

    def spawn_origin(self, session_id: str) -> SessionSpawnOrigin:
        """The checkout is read from the live directory: a resumed session no
        longer reports the worktree it was set up in, but still runs there.
        """
        adapter = self._adapter_of(session_id)
        cwd = None if adapter is None else adapter.cwd
        if adapter is None or cwd is None:
            raise ValueError("This session's checkout is unknown")
        checkout: SessionSpawnCheckout = (
            SessionSpawnDirectory(path=cwd)
            if ManagedWorktree.at(Path(cwd)) is None
            else SessionSpawnManagedWorktree(path=cwd)
        )
        config = adapter._context.config_orchestrator.config
        return SessionSpawnOrigin(
            checkout=checkout,
            settings=adapter.settings(),
            models=spawn_models(config),
            model_names=config.catalog_aliases,
            hops=adapter.message_hops,
            started_by=adapter.started_by_session_id,
            turn_starts=adapter.turn_started_sessions,
            count_turn_start=adapter.count_turn_started_session,
        )

    async def enter_worktree(
        self, session_id: str, args: EnterWorktreeArgs
    ) -> EnteredWorktree:
        adapter = self._adapter_of(session_id)
        if adapter is None or adapter.session_id != session_id:
            raise EnterWorktreeError(
                "Only a top-level session can move into a worktree"
            )
        return await adapter.enter_worktree(args, announce_cwd=self._announce_cwd)

    def message_hops(self, session_id: str) -> int:
        adapter = self._adapter_of(session_id)
        if adapter is None:
            raise ValueError("This session is unknown")
        return adapter.message_hops

    def started_by(self, session_id: str) -> str | None:
        adapter = self._adapter_of(session_id)
        if adapter is None:
            raise ValueError("This session is unknown")
        return adapter.started_by_session_id

    def turn_started_sessions(self, session_id: str) -> int:
        adapter = self._adapter_of(session_id)
        if adapter is None:
            raise ValueError("This session is unknown")
        return adapter.turn_started_sessions

    def count_turn_started_session(self, session_id: str, delta: int) -> None:
        adapter = self._adapter_of(session_id)
        if adapter is None:
            raise ValueError("This session is unknown")
        adapter.count_turn_started_session(delta)

    async def sender_of(self, session_id: str) -> SessionMessageSender:
        """A subagent's message comes from the session it runs under, named as
        session lists name it: by its title, else its preview.
        """
        adapter = self._adapter_of(session_id)
        if adapter is None:
            raise SessionPeerError("This session is unknown")
        # The Harness's own read: a public read can wait on the event pump,
        # which this session's tool call may be holding up.
        session = (
            await adapter._session.read(
                _harness_read_params(
                    SessionReadParams(
                        session_id=adapter.session_id,
                        history=PageRequest(limit=1),
                        turns=None,
                    )
                )
            )
        ).snapshot.state.session
        preview = project_session_message_preview(
            project_loop_preview(public_session_preview(session.preview))
        )
        return SessionMessageSender(
            session_id=adapter.session_id, title=session.title or preview or None
        )

    def _adapter_of(self, session_id: str) -> UnifiedHarnessBackendAdapter | None:
        """The adapter of a root session, or of the root a subagent runs under."""
        for adapter in self._adapters.values():
            if adapter.session_id == session_id or self._host.references_child(
                adapter.session_id, session_id
            ):
                return adapter
        return None

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
    def _launch_ready(
        self, backend: UnifiedHarnessBackendAdapter
    ) -> Callable[[], None] | None:
        """The readiness report of a launch that resumes a session.

        A client launching on a saved session (``vibe --continue``) first opens
        an unused session ahead of need, then resumes in its place: the resumed
        session being ready is the launch being ready. vibe.ready then measures
        from the warm-up's start, and the warm-up, if kept, does not report it
        again. Any other resume reports no readiness.
        """
        warm_ups = self._unreported_warm_ups(backend)
        if not warm_ups:
            return None
        for warm_up in warm_ups:
            warm_up._ready_reported = True
        backend._init_started_monotonic = min(
            warm_up._init_started_monotonic for warm_up in warm_ups
        )
        return backend._emit_ready_telemetry

    def _unreported_warm_ups(
        self, backend: UnifiedHarnessBackendAdapter | None = None
    ) -> list[UnifiedHarnessBackendAdapter]:
        return [
            adapter
            for adapter in self._adapters.values()
            if adapter is not backend
            and adapter._announce_on_use
            and not adapter._ready_reported
        ]

    def _warm_ups_ready(self) -> None:
        """A launch whose resume failed goes on with its warm-up session: that
        one being ready is the launch being ready.
        """
        for warm_up in self._unreported_warm_ups():
            warm_up._ready_reported = True
            task = warm_up._experiments_task
            if task is None or task.done():
                warm_up._emit_ready_telemetry()
            else:
                task.add_done_callback(
                    lambda _task, warm_up=warm_up: warm_up._emit_ready_telemetry()
                )

    def _start_experiments(
        self,
        backend: UnifiedHarnessBackendAdapter,
        *,
        after: Callable[[], None] | None = None,
    ) -> None:
        # The catalog fetch needs neither client identity nor telemetry
        # services, so it runs even when the experiments eval cannot.
        backend._start_model_catalog_fetch()
        services = self._services
        if services is None:
            # No client identity to resolve experiments against; still let the
            # caller emit its lifecycle telemetry (with an empty snapshot).
            if after is not None:
                after()
            return
        client_info = services.client_info()

        async def run() -> None:
            await backend.initialize_experiments(client_launch_context(client_info))
            # Emit after the eval so new_session/ready carry the resolved
            # user_plan and experiment_attributes.
            if after is not None:
                after()

        task = asyncio.create_task(run())
        # ``task_finished`` is a *done* callback, so it must be registered, not
        # called. The backend holds the reference so the task is not collected
        # mid-flight, and cancels it on close.
        backend._experiments_task = task
        orchestrator = backend._context.config_orchestrator
        credentials = backend._context.platform_credentials
        failures = backend._context.experiments_service.failures
        needed = experiments_wait_needed(orchestrator, failures, credentials)
        # Armed for every eval, so one still running at the cap stops later
        # sessions from waiting even when this one holds nothing on it.
        wait = (
            ExperimentsWait(task, failures)
            if experiments_eval_runs(orchestrator.config, credentials)
            else None
        )
        backend._experiments_wait = wait if needed else None
        # Let a live /whoami reconcile await this init before it overwrites the
        # attribute snapshot, closing the stale-cache clobber window.
        backend._context.experiments_init_gate.track(task)
        task.add_done_callback(services.task_finished)

    def _after_open(
        self,
        backend: UnifiedHarnessBackendAdapter,
        *,
        notices: Sequence[str] = (),
        session_start: SessionStartSource | None = None,
    ) -> Callable[[], None]:
        # Read now: a --worktree start can adopt the worktree's context before
        # the response goes out, and that one reports on its own.
        org_config_report = backend._context.org_config_report

        def after_response() -> None:
            # After the response, so after the server closed the session this one
            # replaces: its session_end runs before this session_start. Before
            # anything else, so a first turn sent with the open waits for it.
            if session_start is not None:
                backend.start_session_hooks(session_start)
            backend.start_scheduled_loops()
            backend.start_skill_watch()
            backend.start_registry_sync()
            # Phase 2: discover pre-existing PRs/MRs in the background. The
            # open response is already on its way, so the UI renders the
            # conversation first and artifacts arrive later via notifications.
            backend.schedule_pr_discovery()
            backend.mark_announced()
            for notice in notices:
                backend._publish_notice(notice)
            backend.report_org_config(org_config_report.take())

        return after_response

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
            await backend._cache_session_lineage()
            # Resumed roots carry persisted children that never re-announce
            # themselves; seeding restores the subagent list before the client
            # sees the resume response. Fresh opens no-op.
            await backend.seed_persisted_child_states()
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
        await self._occupy_entered_worktrees(backend)
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
            backend._joining_session_dir(),
        )

    async def _occupy_entered_worktrees(
        self, backend: UnifiedHarnessBackendAdapter
    ) -> None:
        """Hold again the worktrees a resumed session had entered.

        Such a session is a moved one: later derivations list those
        worktrees, not the checkout it left.
        """
        if backend.cwd is None:
            return
        cwd = Path(backend.cwd)
        recorded = [Path(w.path) for w in backend._artifact_service.list_worktrees()]
        session_dir = backend._joining_session_dir()

        def hold() -> list[Path]:
            entered = [path for path in recorded if path.is_dir()]
            for worktree in entered:
                if worktree != cwd:
                    self._worktrees.hold(
                        worktree, backend.session_id, None, session_dir
                    )
            return entered

        entered = await asyncio.to_thread(hold)
        # Recorded is not landed: a close that failed to forget a dropped move
        # leaves its worktree recorded while the session stayed where it was.
        if cwd in entered and backend._context.workspace is not None:
            backend._context.workspace.entered = tuple(entered)
            backend._context.permissions.move_to(cwd, entered)

    async def _pin_for_resume(
        self,
        options: SessionOptions,
        session_id: str,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        require_api_key: bool,
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation, WorktreeResolution]:
        async def resolve_worktree(pinned: SessionOptions) -> WorktreeResolution:
            entered = await asyncio.to_thread(
                _entered_worktrees, context.storage_root, session_id
            )
            return await self._worktrees.resolve_for_start(
                _with_entered_worktrees(pinned, entered),
                trust_store=context.harness_files.trust_store,
            )

        return await self._pin_session_context(
            options,
            session_id,
            context,
            derivation,
            require_api_key=require_api_key,
            resolve_worktree=resolve_worktree,
            settings=None,
        )

    async def _pin_for_fork(
        self,
        options: SessionOptions,
        session_id: str,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        require_api_key: bool,
        settings: UnifiedSessionSettings | None,
        trust_store: TrustedFoldersManager | None = None,
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation, WorktreeResolution]:
        return await self._pin_session_context(
            options,
            session_id,
            context,
            derivation,
            require_api_key=require_api_key,
            resolve_worktree=lambda pinned: self._worktrees.resolve_for_fork(
                pinned,
                trust_store=trust_store or context.harness_files.trust_store,
                copy_on_write=(
                    context.config_orchestrator.config.worktree_copy_on_write
                ),
            ),
            settings=settings,
        )

    async def _pin_session_context(
        self,
        options: SessionOptions,
        session_id: str,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        require_api_key: bool,
        resolve_worktree: _WorktreeResolver,
        settings: UnifiedSessionSettings | None,
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation, WorktreeResolution]:
        """Rebuild the context against an existing session's stored cwd, if it differs.

        The caller must have already built ``context`` once (which configures the Host's
        storage/legacy resolver — what ``session_cwd`` needs). If the session was created
        in a different cwd than the caller's, rebuild so hooks are discovered and compiled
        against the session's own cwd; binding ids embed it, so a resume/fork otherwise
        skips every persisted hook or binds the wrong project's hooks.
        """
        stored_cwd = await _harness_call(self._host.session_cwd(session_id))
        restored = False
        if stored_cwd is not None:
            restored = await self._worktrees.restore(
                Path(stored_cwd), trust_store=context.harness_files.trust_store
            )
        pinned_options = _with_session_cwd(options, stored_cwd)
        resolution = await resolve_worktree(pinned_options)
        scope = _session_context_scope(
            options, resolution.options, stored_cwd=stored_cwd, restored=restored
        )
        resolution = replace(resolution, options=scope.options)
        try:
            if scope.rebuild_context:
                for granted_cwd in scope.trust_grants_to_revoke:
                    context.harness_files.trust_store.revoke_session_trust(granted_cwd)
                # The rebuild reads the copy the first build made sure of. The
                # report keeps the first build's fetch outcome unless the
                # rebuild has its own.
                first_report = context.org_config_report.take()
                context, derivation = await self._lifecycle_context(
                    resolution.options,
                    require_api_key=require_api_key,
                    org_config=OrgConfigSource.CACHED,
                )
                context.org_config_report.inherit(first_report)
            rederive = await self._restore_session_pins(session_id, context)
            if rederive or settings is not None:
                derivation = await asyncio.to_thread(
                    context.derive, settings or UnifiedSessionSettings()
                )
                self._host.configure_runtime(
                    derivation.core_config, adapter_config=derivation.adapter_config
                )
            return context, derivation, resolution
        except BaseException:
            await self._worktrees.cleanup(resolution)
            raise

    async def _restore_session_pins(self, session_id: str, context: _PinTarget) -> bool:
        """Put a context on the choices a session was left with.

        Every stored pin records what the session ran, picked by the user or
        recorded by its turns, and the session reopens with it. Storage and
        lookup are generic (see ``SessionPin``); only applying one is per-pin
        policy. Declaration order is restore order: the level applies to
        whichever model the model and agent pins leave active.

        A choice the current configuration no longer accepts is dropped rather
        than raised: the merged config it would produce is the one every picker
        writes through, so a pin that cannot merge would strand the session with
        nothing inside the app able to clear it. A mode whose settings clash with
        the config, which the user fixes in the file it names, is refused as a
        session start in it is.
        """
        restored = False
        stored_pins = await _harness_call(self._host.session_pins(session_id))
        for pin in SessionPin:
            value = stored_pins[pin]
            try:
                applied = value is not None and await self._apply_restored_pin(
                    pin, context, value
                )
            except (ConfigPatchValidationError, ValueError) as exc:
                logger.warning(
                    "Stored session %s %r no longer merges into the configuration; "
                    "opening the session without it",
                    pin.value,
                    value,
                    exc_info=exc,
                )
                applied = False
            logger.debug(
                "Session pin restore session=%s pin=%s stored=%r applied=%s",
                session_id,
                pin.value,
                value,
                applied,
            )
            restored = restored or applied
        return restored

    async def _apply_restored_pin(  # noqa: PLR0911 - one return per pin's case
        self, pin: SessionPin, context: _PinTarget, value: str
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
                    await context.agents.aswitch_profile(value)
                except InvalidConfigError as exc:
                    # The mode is there, but its settings clash with the config,
                    # say lean's shell timeouts with config.toml's: refused as a
                    # session start in it is, never dropped for the default.
                    raise SessionBackendError.configuration(exc) from exc
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
                # Earlier versions pinned "off" for an unset level. Under an unset
                # level, off is what the session runs anyway wherever the model
                # can stop, so the pin only matters where it would be refused.
                if (
                    value == "off"
                    and not context.config_orchestrator.config.get_active_model().thinking_is_set
                ):
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
            case SessionPin.COMPLETION_CATALOGUE:
                return _restore_saved_catalogue(context, value)
            case _:
                assert_never(pin)

    def _entrypoint(self) -> AgentEntrypoint:
        if self._services is None:
            return "cli"
        return self._services.client_info().entrypoint

    async def _lifecycle_context(
        self,
        options: SessionOptions,
        *,
        require_api_key: bool,
        org_config: OrgConfigSource = OrgConfigSource.CACHED,
    ) -> tuple[UnifiedSessionContext, UnifiedRuntimeDerivation]:
        """``org_config`` is ``ENSURED`` for session starts only: they wait for a
        first copy when the process holds none. Every other build uses the copy
        the process holds.
        """
        try:
            context = await self._build_context(
                options,
                require_api_key=require_api_key,
                entrypoint=self._entrypoint(),
                org_config=org_config,
            )
        except MissingAPIKeyError as exc:
            # The shape ACP reads to offer a sign-in instead of reporting a
            # configuration error. Identical to what the legacy backend sends.
            raise SessionBackendError(
                ProtocolErrorCode.UNAUTHORIZED,
                str(exc),
                {"provider": exc.provider_name, "env_key": exc.env_key},
            ) from exc
        except AgentSelectionError as exc:
            # The caller named an agent that cannot start: its request to fix.
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        except USER_CONFIG_ERRORS as exc:
            _raise_session_config_error(exc)
        except InvalidConfigError as exc:
            # A config that loads but breaks a schema rule (say a shell tool's
            # maximum timeout of `inf`) is the user's to fix too: say what to
            # change, not "invalid parameters".
            raise SessionBackendError.configuration(exc) from exc
        if self._services is not None:
            # The gateway seam already exists on ``SessionBackendServices``; the
            # legacy backend has always been handed both. Attaching them here
            # keeps the context builder free of the request-scoped services.
            platform_credentials = (
                self._services.platform_credentials
                if isinstance(self._services, SessionBackendPlatformCredentials)
                else None
            )
            context = replace(
                context,
                account_gateway=self._services.account_gateway(),
                identity_gateway=self._services.identity_gateway(),
                platform_credentials=platform_credentials,
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
                scheduled_loops=self.cron_scheduler,
                session_spawner=context.session_spawner,
                session_origin=self.spawn_origin,
                session_peers=context.session_peers,
                hops_of=self.message_hops,
                sender_of=self.sender_of,
                starter_of=self.started_by,
                turn_starts=TurnStarts(
                    of=self.turn_started_sessions, count=self.count_turn_started_session
                ),
                enter_worktree=self.enter_worktree,
                # Read per call, not captured: `/config` can raise or lower the cap
                # inside a session's life.
                max_todos=lambda: resolved_max_todos(
                    context.config_orchestrator.config.tools.get(TODO_TOOL_NAME)
                ),
            ),
            mode=VIBE_PROVIDED_TOOL_MODES,
        )
        # The client runs these itself, on its own authority, so they are not
        # gated again here.
        if context.client_tools.group_names:
            self._host.configure_provided_tool_executor(
                context.client_tools.group_names,
                context.client_tools.executor_factory(
                    partial(_client_tool_caller, self._host)
                ),
                mode="allow",
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
        self._publish_mcp_catalog(context, context.mcp_catalog)
        self._rebuild_mcp_catalog_on_bind(context)
        # A fresh start opens before its connector catalog resolves, so this
        # carries the empty placeholder. The Host is shared by every session:
        # writing the placeholder again on a later start/resume/continue would
        # clobber the catalog an earlier session's deferred resolve installed,
        # and an unpromoted session's first turn would then rebuild its
        # subagent ceiling from no connectors. Only the first apply installs
        # the placeholder; every later apply carries a resolved catalog.
        if (
            context.connector_catalog is not None
            or not self._host.connectors_configured
        ):
            self._configure_connectors(context, connector_catalog, connector_selection)
        return context, derivation

    def _publish_mcp_catalog(
        self, context: UnifiedSessionContext, catalog: ResolvedMCPCatalog
    ) -> None:
        context.mcp_authorization_adapter.update_plugin_server_names(catalog)
        self._host.configure_mcp(
            _harness_mcp_catalog(
                catalog, _mcp_tool_filter(context.config_orchestrator.config)
            ),
            context.mcp_authorization_adapter,
            cache_root=context.mcp_cache_root,
            cache_policy=HarnessMCPDescriptorCachePolicy(
                refresh_after_s=_MCP_DESCRIPTOR_REFRESH_AFTER_S
            ),
            http_transport_policy=HarnessMCPHTTPTransportPolicy(
                enable_system_trust_store=context.mcp_enable_system_trust_store
            ),
            open_from_cache=True,
            discovery_flights=context.mcp_discovery_flights,
        )

    def _rebuild_mcp_catalog_on_bind(self, context: UnifiedSessionContext) -> None:
        """Re-publish the merged catalog once the session's checkouts exist.

        A session resolves plugins twice: here, over the installed roots, and
        again inside ``bind``, over the checkouts the Runtime pinned. A plugin
        stdio server's ``cwd`` is its plugin root and ``cwd`` is inside the
        authorization fingerprint, so the catalog built before the pin names
        servers this session never runs. ``bind`` lands inside the pin step,
        which the Runtime orders before MCP initialization, so a session's
        first bind is where the catalog can still be corrected; a later one
        leaves the Host-wide store alone and reconfigures the session instead.
        """
        from vibe.app_server._runtime import merge_plugin_mcp_into_catalog

        async def republish(session_id: str, _bound: SessionPlugins) -> None:
            if context.plugin_provider.bound(session_id) is not None:
                return
            self._publish_mcp_catalog(
                context,
                merge_plugin_mcp_into_catalog(
                    await _configured_mcp_catalog(context),
                    context.plugin_mcp,
                    authentication=context.mcp_authentication,
                ),
            )

        context.plugin_provider.observe_binds(republish)

    def _configure_connectors(
        self,
        context: UnifiedSessionContext,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
    ) -> None:
        _configure_host_connectors(self._host, context, catalog, selection)


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
        return self._context.plugin_mcp

    def _project_mcp(self, snapshot: HarnessMCPRouteSnapshot) -> SessionMCPState:
        self._context.provided_names.register("mcp", _published_mcp_names(snapshot))
        return _session_mcp_state(
            snapshot, self.mcp_config_orchestrator, self._context.plugin_mcp
        )

    async def read_mcp(self) -> SessionMCPState:
        return self._project_mcp(await _harness_call(self._session.read_mcp()))

    async def reconfigure_mcp(
        self, configuration: ResolvedMCPCatalog, *, force_remote_discovery: bool
    ) -> SessionMCPState:
        from vibe.app_server._runtime import merge_plugin_mcp_into_catalog

        # What converge compares against: plugin servers follow the plugins.
        configured = _without_plugin_servers(configuration)
        if self._context.config_orchestrator.config.enable_mcp:
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
        self._mcp_running = configured
        return self._project_mcp(snapshot)

    async def discover_pending_mcp(self, *, refresh: bool = True) -> SessionMCPState:
        snapshot = await _harness_call(
            self._session.discover_pending_mcp(refresh=refresh)
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


class _ConnectorLoadError(Exception):
    """A background connector load that failed, with the reason to report."""


def _ready_wait_integrations(raw_params: Mapping[str, Any]) -> bool:
    """``SessionReadyWaitParams.integrations``, validated on its own.

    Dispatch carries the session id outside the params, so the full model
    would reject a request that only sets this.
    """
    field = SessionReadyWaitParams.model_fields["integrations"]
    value = raw_params.get("integrations", field.default)
    if not isinstance(value, bool):
        raise SessionBackendError(
            ProtocolErrorCode.INVALID_PARAMS, "integrations must be a boolean"
        )
    return value


def _mcp_offer(snapshot: HarnessMCPRouteSnapshot) -> object:
    """What Core is offered from ``snapshot``: its routes and their descriptors.

    The route revision leaves out descriptions, so a refresh that changed only
    those would look like no change.
    """
    return (
        snapshot.route_revision,
        tuple((source.name, source.descriptors) for source in snapshot.sources),
    )


def _is_turn_conflict(exc: SessionBackendError) -> bool:
    data = exc.data
    return isinstance(data, dict) and data.get("harnessCode") == "turn_conflict"


class _UnifiedHarnessConnectorAdapter:
    _session: UnifiedHarnessSessionBackend
    _runtime: RuntimeSnapshot
    _context: UnifiedSessionContext
    _host: UnifiedHarnessSessionBackendHost | None
    _connector_catalog: ResolvedConnectorCatalog
    _connector_selection: ResolvedConnectorSelection
    _connector_error: str | None
    _connectors_unpublished: bool
    _mcp_apps: SessionMcpApps
    _connector_follower: RemoteFollower[ResolvedConnectorCatalog] | None
    _connectors_converging: asyncio.Lock

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def connector_config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        return self._context.config_orchestrator

    def _project_connectors(
        self, snapshot: HarnessConnectorRouteSnapshot
    ) -> SessionConnectorState:
        self._publish_connector_routes(snapshot)
        return _session_connector_state(snapshot)

    def _publish_connector_routes(
        self, snapshot: HarnessConnectorRouteSnapshot
    ) -> None:
        """Name the routes Core holds, for permissions and for MCP Apps."""
        self._context.provided_names.register(
            "connector", _published_connector_names(snapshot)
        )
        self._mcp_apps.update_routes(snapshot, self._connector_catalog)

    async def read_connectors(self) -> SessionConnectorState:
        return self._project_connectors(
            await _harness_call(self._session.read_connectors())
        )

    def accepted_connector_catalog(self) -> ResolvedConnectorCatalog | None:
        return self._connector_catalog if self._connector_catalog.revision else None

    async def reconfigure_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        force: bool,
    ) -> SessionConnectorState:
        # Serialised with the boundary's apply, which would otherwise finish
        # after this one and put the session back on its older catalog.
        async with self._connectors_converging:
            if (
                not force
                and catalog == self._connector_catalog
                and selection.selection_revision
                == self._connector_selection.selection_revision
                and not self._connectors_unpublished
            ):
                # Already running, by the turn boundary or another request: a
                # second push would only rebuild Core's tools. A forced one
                # also restores connectors a turn withdrew or suspended.
                return await self.read_connectors()
            state = await self._apply_connectors(catalog, selection, push_to_core=True)
            # Core now holds every connector route, the background-loaded
            # included.
            self._connectors_unpublished = False
            return state

    async def _ready_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        over: ResolvedConnectorCatalog,
    ) -> bool:
        """Route a catalog loaded in the background, leaving Core for later;
        whether it did. Not when the session no longer runs ``over``, the
        catalog the load started on: a refresh or boundary applied a newer
        one meanwhile.

        Core refuses a capability change during a turn, so its tools are handed
        over by ``_publish_integrations`` at the next boundary. Calls dispatch
        on the new routes at once. The host and its subagents are left to
        ``_refresh_host_connectors``, so a held call waits on the routes only.

        A turn promoting the session builds its Runtime from the routes and
        refuses a change meanwhile; the routes are readied once it has, under
        the turn-start lock the promotion holds, taken before the converging
        lock as a boundary does.
        """
        try:
            async with self._connectors_converging:
                return await self._ready_connectors_over(catalog, selection, over)
        except SessionBackendError as exc:
            if not _is_turn_conflict(exc):
                raise
            async with self._session.turn_start_boundary(), self._connectors_converging:
                return await self._ready_connectors_over(catalog, selection, over)

    async def _ready_connectors_over(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        over: ResolvedConnectorCatalog,
    ) -> bool:
        if self._connector_catalog != over:
            return False
        await self._route_connectors(catalog, selection, push_to_core=False)
        # Under the lock: an apply right after it pushes to Core and clears it.
        self._connectors_unpublished = True
        return True

    async def _apply_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        push_to_core: bool,
    ) -> SessionConnectorState:
        state = await self._route_connectors(
            catalog, selection, push_to_core=push_to_core
        )
        await self._refresh_host_connectors(catalog, selection)
        return state

    async def _route_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        push_to_core: bool,
    ) -> SessionConnectorState:
        snapshot = await _harness_call(
            self._session.reconfigure_connectors(
                _harness_connector_catalog(catalog),
                _harness_connector_selection(selection),
                push_to_core=push_to_core,
            )
        )
        self._connector_catalog = catalog
        self._connector_selection = selection
        self._connector_error = None
        if (follower := self._connector_follower) is not None:
            follower.mark_loaded(catalog)
        state = self._project_connectors(snapshot)
        self._update_connector_projection(state)
        return state

    async def _refresh_host_connectors(
        self, catalog: ResolvedConnectorCatalog, selection: ResolvedConnectorSelection
    ) -> None:
        """Propagate a connector apply to the shared host and its live subagents.

        The session apply already succeeded, so a host failure is logged and
        swallowed rather than escaping into the caller (background catalog
        resolve, relocation, connector toggles).
        """
        host = self._host
        if host is None:
            return
        try:
            _configure_host_connectors(host, self._context, catalog, selection)
            await host.reconfigure_live_subagents()
        except Exception:
            logger.warning(
                "Failed to refresh host connectors for live subagents", exc_info=True
            )

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
        state, source, connector = await self._actionable_connector(
            alias,
            statuses={"needs_auth", "needs_setup"},
            conflict=f"Connector authorization is not actionable: {alias}",
        )
        return ConnectorAuthRequest(
            session_id=self.session_id,
            raw_connector_id=connector.raw_id,
            alias=alias,
            accepted_catalog_revision=state.accepted_catalog_revision,
            action=connector.auth_action,
            reason="needs_auth" if source.status == "needs_auth" else "needs_setup",
        )

    async def request_connector_connect(self, *, alias: str) -> ConnectorConnectRequest:
        _state, source, connector = await self._actionable_connector(
            alias,
            statuses={"needs_connect"},
            conflict=f"Connector does not need connecting: {alias}",
        )
        return ConnectorConnectRequest(
            session_id=self.session_id,
            raw_connector_id=connector.raw_id,
            alias=alias,
            display_name=source.display_name,
        )

    async def _actionable_connector(
        self, alias: str, *, statuses: set[str], conflict: str
    ) -> tuple[SessionConnectorState, SessionConnectorSourceState, ResolvedConnector]:
        """Resolve `alias` in the accepted catalog when its status is one of `statuses`."""
        state = await self.read_connectors()
        source = next((item for item in state.sources if item.alias == alias), None)
        if source is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Connector not found: {alias}"
            )
        if source.status not in statuses:
            raise SessionBackendError(ProtocolErrorCode.CONFLICT, conflict)
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
        return state, source, connector

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
                    connector_error=self._connector_error,
                ),
            }
        )


class _UnifiedSessionPersistence:
    _session: UnifiedHarnessSessionBackend
    _context: UnifiedSessionContext
    _metadata: SessionMetadata | None
    # Held by an archive, a message from another session and a scheduled
    # loop, so each lands wholly before or after an archive.
    _archive_gate: asyncio.Lock

    def _publish_notice(
        self, message: str, *, level: str = "warning", code: str | None = None
    ) -> None: ...

    def _persists_to_disk(self) -> bool:
        if not self._context.session_logging_enabled:
            return False
        return not bool(getattr(self._session, "ephemeral", True))


class _UnifiedSessionMetadataAdapter(_UnifiedSessionPersistence):
    """Maintains Vibe-owned sidecar metadata for Unified Harness sessions.

    The Harness remains authoritative for execution state and public session
    fields it owns. This sidecar exists for Vibe-owned metadata that the Harness
    deliberately does not know about, including pin and archive timestamps, while
    preserving a legacy-shaped ``meta.json`` for local filesystem tooling.
    """

    _storage_root: str
    _metadata_lock: asyncio.Lock
    _artifact_service: SessionArtifactService
    _api_path: SessionApiPath | None
    _project: SessionProject | None

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
            self._publish_notice(
                "The session was saved, but its metadata could not be saved."
            )

    async def _persist_session_metadata_best_effort(self) -> None:
        if not self._persists_to_disk():
            return
        try:
            await self._persist_session_metadata()
        except Exception as exc:
            logger.warning("Failed to persist Unified session metadata", exc_info=exc)

    async def _stamp_project(
        self, project: SessionProject, *, expected: SessionProject | None = None
    ) -> None:
        """Best effort, as for a closed session: a later listing retries."""
        if not self._persists_to_disk():
            return
        async with self._metadata_lock:
            # Under the lock, so a move made meanwhile is kept.
            if self._project != expected:
                return
            try:
                await self._restamp_locked(project)
            except Exception:
                logger.warning("Could not stamp a session's project", exc_info=True)

    async def _move_to_project(self, project: SessionProject) -> None:
        """A move replaces any stamp; a listing stamp replaces only the one it listed."""
        async with self._metadata_lock:
            await self._restamp_locked(project)

    async def _restamp_locked(self, project: SessionProject) -> None:
        self._project = project
        if self._metadata is not None:
            self._metadata = self._metadata.model_copy(update={"project": project})
        if self._persists_to_disk():
            await self._persist_session_metadata_locked(bumped_at=None)

    async def _persist_session_metadata(
        self,
        *,
        bumped_at: datetime | None = None,
        pinned_at: datetime | None | _Unchanged = _UNCHANGED,
        archived_at: datetime | None | _Unchanged = _UNCHANGED,
    ) -> SessionMetadata:
        async with self._metadata_lock:
            return await self._persist_session_metadata_locked(
                bumped_at=bumped_at, pinned_at=pinned_at, archived_at=archived_at
            )

    async def _mark_session_unseen(self, at: datetime) -> SessionMetadata:
        async with self._metadata_lock:
            metadata = await self._metadata_for_seen_update_locked()
            seen_state = SessionSeenState.from_metadata(metadata).mark_unseen(at)
            return await self._persist_seen_state_locked(metadata, seen_state)

    async def _mark_session_seen(self, at: datetime) -> SessionMetadata:
        async with self._metadata_lock:
            metadata = await self._metadata_for_seen_update_locked()
            seen_state = SessionSeenState.from_metadata(metadata).mark_seen(at)
            return await self._persist_seen_state_locked(metadata, seen_state)

    async def _record_unseen_transition(
        self, previous: PublicSessionState, current: PublicSessionState
    ) -> PublicSessionState:
        if not should_mark_session_unseen(previous, current):
            return current
        try:
            metadata = await self._mark_session_unseen(datetime.now(UTC))
        except Exception as exc:
            logger.warning("Failed to persist unseen local session state", exc_info=exc)
            return current
        is_unseen = SessionSeenState.from_metadata(metadata).is_unseen
        return current.model_copy(
            update={
                "session": current.session.model_copy(update={"is_unseen": is_unseen})
            }
        )

    async def _metadata_for_seen_update_locked(self) -> SessionMetadata:
        if self._metadata is not None:
            return self._metadata
        return await self._persist_session_metadata_locked(bumped_at=None)

    async def _persist_seen_state_locked(
        self, metadata: SessionMetadata, seen_state: SessionSeenState
    ) -> SessionMetadata:
        updated = seen_state.apply_to(metadata)
        if updated == metadata:
            return metadata
        return await self._store_metadata_locked(updated)

    async def _persist_session_metadata_locked(
        self,
        *,
        bumped_at: datetime | None,
        pinned_at: datetime | None | _Unchanged = _UNCHANGED,
        archived_at: datetime | None | _Unchanged = _UNCHANGED,
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
            archived_at=archived_at,
            project=self._project,
        )
        metadata = self._with_model_config(metadata)
        metadata = metadata.model_copy(
            update={
                "runtime_path": "direct" if self._api_path is None else "session_api"
            }
        )
        # The artifact service is authoritative between sidecars: a PR may have
        # been recorded while ``_metadata`` was still None (no meta.json yet),
        # so always take the live list rather than ``previous.artifacts``.
        metadata = metadata.model_copy(
            update={
                "artifacts": self._artifact_service.list_artifacts(),
                "worktrees": self._artifact_service.list_worktrees(),
            }
        )
        return await self._store_metadata_locked(metadata)

    def _with_model_config(self, metadata: SessionMetadata) -> SessionMetadata:
        """Mirror the session's model into the config snapshot.

        The Harness stores the model as a session pin; ``meta.json`` records it
        in ``config`` so the model stays discoverable from the session log
        alone, without reaching into the Harness store.
        """
        config = dict(metadata.config or {})
        model = self._session_model()
        config["active_model"] = model.alias
        config["model_name"] = model.name
        # Skip the copy when the model info is already present and unchanged.
        if config == (metadata.config or {}):
            return metadata
        return metadata.model_copy(update={"config": config})

    def _session_model(self) -> ModelConfig:
        """The session's model: its pinned one, else the active one."""
        pinned = self._session.pin(SessionPin.ACTIVE_MODEL)
        schema = self._context.config_orchestrator.config
        # Resolve the pinned alias to its model so active_model and
        # model_name always describe the same model, even when the pin
        # was written before a config write landed or refers to a
        # since-removed model.
        model = (
            schema.available_models().get(schema.resolve_model_name(pinned))
            if pinned
            else None
        )
        if model is None:
            model = schema.get_active_model()
        return model

    async def _store_metadata_locked(
        self, metadata: SessionMetadata
    ) -> SessionMetadata:
        # The artifact service owns the artifacts field between rebuilds: it
        # records PR/MR artifacts in memory as they are detected or discovered
        # and may write meta.json directly. Every full persist carries its
        # current list, so a metadata write can never drop recorded artifacts —
        # including artifacts recorded while the session was still ephemeral
        # and meta.json did not exist yet (the promotion-time persist picks
        # them up here).
        metadata = metadata.model_copy(
            update={"artifacts": self._artifact_service.list_artifacts()}
        )
        if self._persists_to_disk():
            await _write_unified_session_metadata(
                _unified_session_dir(self._storage_root, self.session_id), metadata
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

    def _publish_archive_metadata(self, metadata: SessionMetadata) -> None:
        self._publish_session_metadata(
            _SessionMetadataProjection(
                session_id=self.session_id,
                patch=(
                    JsonPatchOperation(
                        op="replace",
                        path="/archivedAt",
                        value=_metadata_archived_at_ms(metadata),
                    ),
                    JsonPatchOperation(
                        op="replace",
                        path="/pinnedAt",
                        value=_metadata_pinned_at_ms(metadata),
                    ),
                ),
            )
        )

    def _publish_seen_state(self, metadata: SessionMetadata) -> None:
        self._publish_session_metadata(
            _SessionMetadataProjection(
                session_id=self.session_id,
                patch=(
                    JsonPatchOperation(
                        op="replace",
                        path="/isUnseen",
                        value=SessionSeenState.from_metadata(metadata).is_unseen,
                    ),
                ),
            )
        )

    def _publish_session_metadata(
        self, metadata: _SessionMetadataProjection
    ) -> None: ...


class _UnifiedScheduledLoopsAdapter(_UnifiedSessionPersistence):
    _closed: bool
    _storage_root: str
    _host: UnifiedHarnessSessionBackendHost | None
    _scheduled_loops: UnifiedScheduledLoops
    # Each subagent's own schedules, keyed by its session id. A subagent only
    # ever reaches its own pool, and its loops fire as messages to it.
    _child_scheduled_loops: dict[str, UnifiedScheduledLoops]
    _child_scheduled_loops_lock: asyncio.Lock
    _scheduled_loops_enabled: bool
    _scheduled_loops_task: asyncio.Task[None] | None
    _pending_startup_notices: list[str]
    _defer_event_flush: bool
    _relocation_active: bool
    _scheduled_turn_opening: bool

    async def _publish_integrations(self) -> None: ...

    async def _wait_for_cold_integrations(self) -> None: ...

    async def _wait_for_first_turn_fetches(self) -> None: ...

    @property
    def session_id(self) -> str:
        return self._session.session_id

    _setup_abandoned = False

    @property
    def cwd(self) -> str | None:
        return self._session.cwd

    def cron_scheduler(self) -> UnifiedScheduledLoops:
        if self._closed or not self._scheduled_loops_enabled or self._setup_abandoned:
            raise ValueError("Scheduled prompts require a live interactive session")
        return self._scheduled_loops

    async def child_cron_scheduler(
        self, child_session_id: str
    ) -> UnifiedScheduledLoops:
        self.cron_scheduler()
        return await self._child_loops(child_session_id)

    async def _child_loops(self, child_session_id: str) -> UnifiedScheduledLoops:
        # One restore at a time, so two callers never quarantine the same file.
        async with self._child_scheduled_loops_lock:
            loops = self._child_scheduled_loops.get(child_session_id)
            if loops is not None:
                return loops
            loops = UnifiedScheduledLoops(
                _unified_session_dir(self._storage_root, child_session_id)
                / "scheduled-loops.json",
                persistent=self._persists_to_disk,
            )
            try:
                await loops.restore()
            except ScheduledLoopStoreError as exc:
                quarantine_path = await loops.quarantine_corrupt_store()
                logger.warning(
                    "Quarantined corrupt subagent scheduled loops", exc_info=exc
                )
                self._publish_notice(
                    "Saved scheduled loops of a subagent could not be restored. "
                    f"They were disabled and preserved at {quarantine_path}."
                )
            self._child_scheduled_loops[child_session_id] = loops
            return loops

    async def _restore_child_scheduled_loops(self) -> None:
        # Best effort: a subagent whose loops cannot be restored must not stop
        # the root's own schedules from running.
        if self._host is None:
            return
        try:
            records = self._host.child_session_records(self.session_id)
        except Exception as exc:
            logger.warning("Skipping subagent scheduled loops", exc_info=exc)
            return
        for record in records:
            path = (
                _unified_session_dir(self._storage_root, record.child_session_id)
                / "scheduled-loops.json"
            )
            if _child_is_gone(record) or not await asyncio.to_thread(path.exists):
                continue
            try:
                await self._child_loops(record.child_session_id)
            except Exception as exc:
                logger.warning("Skipping subagent scheduled loops", exc_info=exc)

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

    async def replace_scheduled_loops(
        self, loops: list[ScheduledPrompt] | list[CoreScheduledLoop]
    ) -> None:
        try:
            await self._scheduled_loops.replace([
                ScheduledPrompt.model_validate(loop, from_attributes=True)
                for loop in loops
            ])
        except ScheduledLoopStoreError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR, str(exc)
            ) from exc

    async def scheduled_loops(self) -> list[ScheduledPrompt]:
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
            self._publish_notice(
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
        await self._restore_child_scheduled_loops()
        child_loops_checked_at = -_CHILD_LOOPS_CHECK_INTERVAL_S
        while True:
            try:
                delay = await self._scheduled_loops.next_due_in()
                await asyncio.sleep(max(0.05, min(delay, 1.0)))
                # Subagent loops are checked at most once a second: a due root
                # loop waiting on a busy root wakes this loop every 50 ms, and a
                # loop waiting on a busy subagent must not follow that rate.
                now = time.monotonic()
                if now - child_loops_checked_at >= _CHILD_LOOPS_CHECK_INTERVAL_S:
                    child_loops_checked_at = now
                    await self._fire_due_child_loops()
                if self._session.active_turn_id is not None or self._defer_event_flush:
                    continue
                scheduled = await self._scheduled_loops.due()
                if scheduled is None:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduled loop failed", exc_info=exc)
                self._publish_notice(f"Scheduled loop failed: {exc}", level="error")
                continue

            try:
                await self._start_scheduled_loop(scheduled)
            except asyncio.CancelledError:
                raise
            except SessionBackendError as exc:
                if exc.code is ProtocolErrorCode.CONFLICT:
                    continue
                self._publish_notice(f"Scheduled loop failed: {exc}", level="error")
            except Exception as exc:
                logger.exception("Scheduled loop failed", exc_info=exc)
                self._publish_notice(f"Scheduled loop failed: {exc}", level="error")
            await self._mark_scheduled_loop_attempted(scheduled)

    async def _mark_scheduled_loop_attempted(
        self, scheduled: ScheduledPrompt, loops: UnifiedScheduledLoops | None = None
    ) -> None:
        loops = loops if loops is not None else self._scheduled_loops
        try:
            await loops.mark_fired(scheduled.id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Failed to reschedule scheduled loop", exc_info=exc)
            self._publish_notice(
                f"Scheduled loop could not be rescheduled: {exc}", level="error"
            )
            # The durable mutation is atomic, so a failed write leaves the loop due.
            # Advance it in memory only: sleeping here would stall the shared runner
            # and every other schedule, and leaving it due would re-fire it at once.
            await loops.defer(scheduled.id)

    async def _fire_due_child_loops(self) -> None:
        # Each pool on its own: one subagent's failure skips no other loop.
        for child_session_id, loops in list(self._child_scheduled_loops.items()):
            scheduled: ScheduledPrompt | None = None
            try:
                scheduled = await loops.due()
                if scheduled is None:
                    continue
                attempted = await self._start_child_scheduled_loop(
                    child_session_id, scheduled
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduled loop failed", exc_info=exc)
                self._publish_notice(f"Scheduled loop failed: {exc}", level="error")
                attempted = scheduled is not None
            if attempted and scheduled is not None:
                await self._mark_scheduled_loop_attempted(scheduled, loops)

    async def _start_child_scheduled_loop(
        self, child_session_id: str, scheduled: ScheduledPrompt
    ) -> bool:
        """Fire a subagent's loop as a message to it; False leaves the loop due.

        A subagent running a Turn keeps its loop due until it is idle, the way
        a busy session does. A subagent that no longer exists drops its pool.
        """
        host = self._host
        record = self._messageable_child(child_session_id)
        if host is None or record is None:
            return False
        async with self._archive_gate:
            if _metadata_archived_at_ms(self._metadata):
                return True
            # The same marker a root's fired prompt carries, so the subagent's
            # transcript shows it as a loop prompt.
            display = scheduled_loop_display(scheduled.id, now_ms())
            result = await host.message_idle_child(
                self.session_id,
                child_session_id,
                scheduled.prompt,
                content_meta={
                    "vibe.userDisplayContent": display.model_dump(
                        mode="json", by_alias=True
                    )
                },
            )
        if result.outcome == "busy":
            return False
        if result.outcome == "unavailable":
            self._publish_notice(
                f"Scheduled loop of subagent {record.agent_name} failed: {result.error}",
                level="error",
            )
        return True

    def _messageable_child(self, child_session_id: str) -> ChildSessionRecord | None:
        """The subagent's record, if a loop can message it now.

        Drops the pool of a subagent that no longer exists. A rewrite of the
        root copies, compacts or moves it as it stands, so a subagent result
        landing meanwhile would be lost or half-applied: loops wait it out.
        """
        if self._host is None:
            return None
        records = self._host.child_session_records(self.session_id)
        if not records:
            # The root is not loaded right now: nothing says its subagents
            # are gone, so their pools stay for the next tick.
            return None
        record = next(
            (
                record
                for record in records
                if record.child_session_id == child_session_id
            ),
            None,
        )
        if record is None or _child_is_gone(record):
            self._child_scheduled_loops.pop(child_session_id, None)
            return None
        if (
            record.state.type not in _MESSAGEABLE_CHILD_STATES
            or self._defer_event_flush
            or self._relocation_active
        ):
            return None
        return record

    async def _start_scheduled_loop(self, scheduled: ScheduledPrompt) -> None:
        if _metadata_archived_at_ms(self._metadata):
            return
        self._require_no_session_rewrite()
        # The loop's turn pins what it runs on (see `_settle_configuration`),
        # so it waits, as `start_turn` does, for what decides the default.
        await self._wait_for_first_turn_fetches()
        params = await self._prepared_turn_params(
            TurnStartParams(
                session_id=self.session_id,
                message=[TextContentBlock(text=scheduled.prompt)],
                user_display_content=scheduled_loop_display(scheduled.id, now_ms()),
            ),
            inject_skill=True,
        )
        # An archived session is done with: its loops stop firing, in one step
        # with the archive.
        async with self._archive_gate:
            if _metadata_archived_at_ms(self._metadata):
                return
            self._scheduled_turn_opening = True
            try:
                result = cast(
                    Any,
                    await _harness_call(
                        self._session.start_scheduled_turn(params, scheduled.id)
                    ),
                )
            finally:
                self._scheduled_turn_opening = False
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

    def _require_no_session_rewrite(self) -> None: ...

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
    # Not ``classifier_model``: it pairs with the turn's provider, so it moves
    # with the next turn and is set in every mode so no profile changes it.
    "permission_resolver",
    # Not read per call: names the profile the fields above came from.
    "active_agent",
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


def _check_saved_models(before: VibeConfigSchema, after: VibeConfigSchema) -> None:
    """Refuse a write that saves settings no profile can hold for a model.

    The session's preflight compiles only the model it runs, as it runs it, so
    a model it does not run, or a saved value its own pick masks, would land in
    config.toml and fail every later session start on that home. ``before``
    and ``after`` are the saved layers either side of the write.
    """
    for model in models_a_write_changes(before, after):
        try:
            provider = after.get_provider_for_model(model)
        except ValueError:
            continue
        compile_agent_route_facts(provider, model)


async def _leave_excluded_mode(
    agents: AgentManager, orchestrator: ConfigOrchestrator[VibeConfigSchema]
) -> str | None:
    """Move ``agents`` off a mode the org config disables, to its default.

    Returns the mode left, if any.
    """
    active = agents.active_profile.name
    if not agents.org_excludes(active):
        return None
    for candidate in (orchestrator.config.default_agent, *FALLBACK_AGENTS):
        try:
            await agents.aswitch_profile(candidate)
        except ValueError:
            continue
        return active
    return None


def _carry_experiments(
    previous: UnifiedSessionContext, incoming: UnifiedSessionContext
) -> None:
    """Keep the eval a session opened with, and its telemetry attributes,
    across a move to another context: a move is not an open.
    """
    manager = previous.experiment_manager
    if (response := manager.export_state()) is not None:
        incoming.experiment_manager.hydrate(
            response, attributes=manager.attributes(), source="move"
        )
    elif (attributes := manager.attributes()) is not None:
        incoming.experiment_manager.set_attributes(attributes)


async def _run_carried_variants(
    holder: UnifiedSessionContext, incoming: UnifiedSessionContext
) -> None:
    """Build *incoming* on the variants of the eval *holder* holds: it was
    built from the variants cache, which a refresh may have rewritten since.
    """
    if holder.experiment_manager.export_state() is None:
        return
    try:
        layer = incoming.config_orchestrator.get_layer(GrowthbookLayer.NAME)
    except KeyError:
        return
    variants = holder.experiment_manager.config_variants()
    if not isinstance(layer, GrowthbookLayer) or layer.variants == variants:
        return
    layer.set_variants(variants)
    await incoming.config_orchestrator.reload()


def _catalog_layer_of(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> ModelCatalogLayer | None:
    try:
        layer = orchestrator.get_layer(ModelCatalogLayer.NAME)
    except KeyError:
        return None
    return layer if isinstance(layer, ModelCatalogLayer) else None


def _catalog_of(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> ModelCatalogResponse | None:
    layer = _catalog_layer_of(orchestrator)
    return layer.catalog if layer is not None else None


def _raise_turn_took_over() -> Never:
    raise SessionBackendError(
        ProtocolErrorCode.CONFLICT,
        "A turn started before the configuration was applied; it applies when"
        " the turn ends.",
    )


class UnifiedHarnessBackendAdapter(  # noqa: PLR0904 - implements app-server session operations
    _UnifiedHarnessMCPAdapter,
    _UnifiedHarnessConnectorAdapter,
    _UnifiedSessionMetadataAdapter,
    _UnifiedScheduledLoopsAdapter,
):
    async def initialize_integrations(self) -> None:
        if self._mcp_running is None:
            self._mcp_running = await _configured_mcp_catalog(self._context)
        # Plugin servers are bound during ``prepare``, awaited before this, so
        # the startup notice about a waiting login can see their statuses.
        state = self._project_mcp(await _harness_call(self._session.read_mcp()))
        sources, discovery_errors = project_mcp_sources(
            self.mcp_config_orchestrator,
            state,
            plugin_sources=self._context.plugin_mcp.sources(),
        )
        projected = MCPState(sources=sources, discovery_errors=discovery_errors)
        self.update_mcp_projection(projected)
        self._warn_mcp_failures(state.discovery_errors)
        # Clients name these themselves once the session is ready.
        self._hinted_mcp_logins = set(projected.needs_auth)
        self._update_connector_projection(await self.read_connectors())
        self._start_connector_resolve()
        self._start_mcp_discovery()
        self._follow_org_config()

    def _start_mcp_discovery(self) -> None:
        """Discover the MCP servers the session opened without, and refresh old ones.

        The session opens on cached descriptors only, so an unreachable server
        never holds up showing it, and turns do not wait for it either: a turn
        runs with the tools it has, a call to a server being discovered waits
        for that server alone, and what is found reaches Core at the next turn
        boundary.
        """
        if self._mcp_discovery_task is not None and not self._mcp_discovery_task.done():
            return
        if not self._session.mcp_discovery_due:
            return
        self._mcp_discovered = asyncio.Event()
        self._mcp_discovery_task = asyncio.create_task(
            self._discover_mcp_background(self._mcp_discovered),
            name="vibe-mcp-discovery",
        )

    async def _discover_mcp_background(
        self, discovered: asyncio.Event | None = None
    ) -> None:
        # The servers with no tools yet first, alone: a turn waits for them,
        # and must not wait for a slow refresh of servers it already has.
        try:
            found = await self._discover_mcp(refresh=False)
        finally:
            if discovered is not None:
                discovered.set()
        if found:
            await self._announce_loaded_integrations()
        if self._session.mcp_discovery_due and not self._closed:
            if await self._discover_mcp(refresh=True):
                await self._announce_loaded_integrations()

    async def _discover_mcp(self, *, refresh: bool) -> bool:
        """Discover and hand Core what was found; whether the client is told."""
        try:
            before = await _harness_call(self._session.read_mcp())
            state = await self.discover_pending_mcp(refresh=refresh)
            after = await _harness_call(self._session.read_mcp())
        except Exception:
            logger.warning("Background MCP discovery failed", exc_info=True)
            return False
        if self._closed:
            return False
        sources, discovery_errors = project_mcp_sources(
            self.mcp_config_orchestrator,
            state,
            plugin_sources=self._context.plugin_mcp.sources(),
        )
        projected = MCPState(sources=sources, discovery_errors=discovery_errors)
        shown_changed = projected != self._runtime.mcp
        self.update_mcp_projection(projected)
        self._warn_mcp_failures(state.discovery_errors)
        self._hint_mcp_logins(projected.needs_auth)
        # A refresh that found the same tools, or failed and kept them, gives
        # Core nothing new; one that changed only a status still tells the
        # client.
        if _mcp_offer(after) != _mcp_offer(before):
            self._mcp_unpublished = True
            await self._publish_integrations()
            return True
        return shown_changed

    async def _publish_integrations(self) -> None:
        """Hand Core the tools loaded in the background, if it is between turns.

        Core takes a capability change only with no action pending, so during a
        turn this does nothing, and the turn ends with ``_settle_configuration``
        calling it again: the end of the turn, and the start of the next, which
        holds the Session's turn-start lock until this returns. A refusal from
        Core, or a turn starting meanwhile, keeps the tools for that next try.
        """
        async with self._integrations_publishing:
            if self._session.active_turn_id is not None:
                return
            if self._connectors_unpublished:
                try:
                    await _harness_call(self._session.push_connector_capabilities())
                except Exception:
                    logger.warning(
                        "Failed to publish loaded connector tools", exc_info=True
                    )
                else:
                    self._connectors_unpublished = False
            if self._mcp_unpublished:
                try:
                    await _harness_call(self._session.push_mcp_capabilities())
                except Exception:
                    logger.warning(
                        "Failed to publish discovered MCP tools", exc_info=True
                    )
                else:
                    self._mcp_unpublished = False

    async def _announce_loaded_integrations(self) -> None:
        """Tell the client the tools changed, once it holds this session.

        A background load can end before the response that announces the
        session, and a client cannot place a notification for a session it
        has not been told about.
        """
        await self.wait_announced()
        if not self._closed:
            await self._announce_runtime()

    def _warn_mcp_failures(self, discovery_errors: Mapping[str, str]) -> None:
        """Warn about each MCP server failure once, however often it is projected.

        Only failures the Runtime reported: a server the session does not run
        (MCP off, say) is no failure to load.
        """
        new = {
            name: error
            for name, error in discovery_errors.items()
            if self._warned_mcp_failures.get(name) != error
        }
        self._warned_mcp_failures = dict(discovery_errors)
        self._warn_integrations_unavailable([
            f"MCP server '{name}' failed to connect: {error.rstrip('.')}."
            for name, error in sorted(new.items())
        ])

    def _hint_mcp_logins(self, needs_auth: Sequence[str]) -> None:
        """Name the servers discovery found waiting for a login, once each.

        A client names the servers waiting for one when the session is ready,
        which no longer waits for discovery: a server with nothing cached is
        still loading then, and only discovery finds it needs a login.
        """
        new = sorted(set(needs_auth) - self._hinted_mcp_logins)
        self._hinted_mcp_logins |= set(new)
        if not new:
            return
        if len(new) == 1:
            line = (
                f"MCP server '{new[0]}' needs OAuth authentication. "
                f"Run `/mcp login {new[0]}` to authenticate."
            )
        else:
            line = (
                f"MCP servers need OAuth authentication: {', '.join(new)}. "
                f"Run `/mcp login {new[0]}` to start with '{new[0]}'."
            )
        self._warn_integrations_unavailable([line])

    def _warn_integrations_unavailable(self, lines: Sequence[str]) -> None:
        """Tell the client what failed to load, as a ``warning`` it shows.

        Rather than leave the failure in a projection only ``/mcp`` reads. A
        load can fail before the client subscribes, and a warning sent then
        would reach nobody, so it is held until ``subscribe``.
        """
        if not lines or self._closed:
            return
        message = " ".join(lines)
        if self._pending_integration_warnings is not None:
            self._pending_integration_warnings.append(message)
            return
        self._publish_notice(message, code=INTEGRATIONS_UNAVAILABLE_WARNING)

    async def _wait_for_integration_loads(self) -> None:
        """Wait for the background connector and MCP loads to settle.

        Each is bounded by its own fetch or startup timeout, and neither
        raises: a failure is already reported as a warning. What they found
        is handed to Core here, as no turn boundary has passed yet.
        """
        tasks = [
            task
            for task in (self._connector_resolve_task, self._mcp_discovery_task)
            if task is not None
        ]
        if tasks:
            await asyncio.wait(tasks)
        await self._publish_integrations()

    async def _wait_for_cold_integrations(self) -> None:
        """Hold a turn for the integrations the session has nothing cached for.

        A turn otherwise starts at once on the cached tools, and what loads
        meanwhile reaches Core at the next turn boundary. With nothing cached,
        that turn would run without them: it waits for that load instead,
        bounded by its fetch or startup timeout, and in all by the longest of
        them. A failed load lets it through without those tools, and a load
        that only refreshes a cache is never waited for.

        A load is waited for once: one still running when the cap ends lets
        every later turn through, which then takes its tools at a boundary.
        """
        waited: set[asyncio.Event] = set()
        # Each load is bounded by its own timeout, but several MCP servers
        # are discovered a few at a time: past the cap the turn goes out on
        # what it has. Re-read after each wait: a relocation replaces the loads.
        try:
            async with asyncio.timeout(self._cold_wait_cap_s()):
                while pending := await self._cold_integration_loads():
                    waited.update(pending)
                    await asyncio.gather(*(event.wait() for event in pending))
        except TimeoutError:
            self._cold_loads_waited_out.update(waited)
        if not waited:
            return
        if self._closed:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "The session was closed"
            )
        self._require_no_session_rewrite()
        await self._publish_integrations()

    def _cold_wait_cap_s(self) -> float:
        servers = self.mcp_config_orchestrator.config.mcp_servers
        return max(
            [CONNECTOR_CATALOG_FETCH_TIMEOUT_S]
            + [server.startup_timeout_sec for server in servers]
        )

    async def _cold_integration_loads(self) -> list[asyncio.Event]:
        # Events, not the tasks: a load sets its event before it tells the
        # client, which waits for the session to be announced, and a turn may
        # start before that.
        pending: list[asyncio.Event] = []
        connectors = self._connectors_loaded
        if (
            connectors is not None
            and not connectors.is_set()
            and connectors not in self._cold_loads_waited_out
            and not self._connector_catalog.revision
        ):
            pending.append(connectors)
        mcp = self._mcp_discovered
        if (
            mcp is not None
            and not mcp.is_set()
            and mcp not in self._cold_loads_waited_out
        ):
            snapshot = await _harness_call(self._session.read_mcp())
            if any(
                source.status == "unavailable" and source.error == MCP_DISCOVERY_PENDING
                for source in snapshot.sources
            ):
                pending.append(mcp)
        return pending

    def _publish_startup_notices(self) -> None:
        """Publish the notices held for the first subscriber, then the warnings."""
        self._notices_subscribed = True
        pending_notices, self._pending_startup_notices = (
            self._pending_startup_notices,
            [],
        )
        for message in pending_notices:
            self._publish_notice(message)
        self._flush_integration_warnings()

    def _flush_integration_warnings(self) -> None:
        """Send the warnings held for a subscriber, and send later ones at once."""
        pending, self._pending_integration_warnings = (
            self._pending_integration_warnings or [],
            None,
        )
        for message in pending:
            self._publish_notice(message, code=INTEGRATIONS_UNAVAILABLE_WARNING)

    def _start_connector_resolve(self) -> None:
        """Kick off deferred connector catalog discovery if not yet resolved.

        The catalog is intentionally absent from the session's initial
        configuration (see build_unified_session_context): the session opens
        instantly with an empty connector set, and the background resolve
        populates its routes when the catalog arrives, and Core takes them at
        the next turn boundary. Nothing waits on it but a connector call.
        """
        if self._connector_resolve_task is not None:
            return
        service = self._context.connector_catalog_service
        if service is None:
            return
        # A catalog already in the context came from the cache, possibly
        # expired (resume plants one), so it is refreshed like an empty one.
        self._connectors_loaded = asyncio.Event()
        self._connector_resolve_task = asyncio.create_task(
            self._resolve_connectors_background(service, self._connectors_loaded),
            name="vibe-connector-resolve",
        )

    async def _connector_load_failed(self, error: str) -> None:
        if self._closed:
            return
        known = [
            source.display_name
            for source in self._runtime.mcp.sources
            if source.kind is MCPSourceKind.CONNECTOR
        ]
        if self._connector_catalog.revision:
            # The session runs on a cached catalog, whose tools still work.
            self._warn_integrations_unavailable([
                f"Couldn't refresh your connectors: {error.rstrip('.')}. "
                "Using the ones loaded earlier."
            ])
            return
        self._connector_error = error
        self._update_connector_projection(await self.read_connectors())
        await self._announce_loaded_integrations()
        subject = f"connectors {', '.join(known)}" if known else "your connectors"
        self._warn_integrations_unavailable([
            f"Couldn't load {subject}: {error.rstrip('.')}. "
            "Their tools are unavailable in this session."
        ])

    async def _resolve_connectors_background(
        self, service: ConnectorCatalogService, loaded: asyncio.Event | None = None
    ) -> None:
        """Resolve the connector catalog and ready its routes in the background.

        With no catalog routed yet, a connector call made meanwhile waits for
        the routes, bounded by the catalog fetch timeout, and no longer: the
        subagents, Core and the client are told once the hold is released.
        Core gets the new tools at the next turn boundary.
        """
        hold: contextlib.AbstractAsyncContextManager[object] = contextlib.nullcontext()
        # A session on a cached catalog already routes its connectors: a call
        # runs on them rather than waiting for the refresh.
        if not self._connector_catalog.revision:
            try:
                hold = self._session.hold_connector_calls(
                    timeout_s=CONNECTOR_CATALOG_FETCH_TIMEOUT_S
                )
            except HarnessNotImplementedError:
                pass  # A Runtime without connectors has no calls to hold.
        failure: str | None = None
        readied = False
        try:
            try:
                async with hold:
                    readied = await self._load_connectors(service)
            except _ConnectorLoadError as exc:
                failure = str(exc)
            if readied:
                await self._refresh_host_connectors(
                    self._connector_catalog, self._connector_selection
                )
                await self._publish_integrations()
        finally:
            if loaded is not None:
                loaded.set()
        # From here a changed catalog reaches the session at its turn
        # boundaries, whichever session of the process fetched it.
        await self._follow_connector_catalog()
        if failure is not None:
            await self._connector_load_failed(failure)
        elif readied:
            await self._announce_loaded_integrations()

    async def _load_connectors(self, service: ConnectorCatalogService) -> bool:
        """Fetch the catalog and ready its routes; whether there were new ones."""
        started_on = self._connector_catalog
        try:
            catalog = await service.resolve_catalog(self._context.config_orchestrator)
        except ConnectorCatalogError as exc:
            logger.warning(
                "Connector catalog is unavailable during background resolve: %s",
                str(exc),
            )
            raise _ConnectorLoadError(
                str(exc) or "The connector catalog is unavailable"
            ) from exc
        except Exception as exc:
            logger.exception("Background connector catalog resolve failed")
            raise _ConnectorLoadError(
                "The connector catalog could not be loaded"
            ) from exc
        if catalog is None or self._closed:
            return False
        selection = service.resolve_selection(
            self._context.config_orchestrator, catalog
        )
        if (
            catalog.revision == self._connector_catalog.revision
            and selection.selection_revision
            == self._connector_selection.selection_revision
        ):
            return False
        try:
            return await self._ready_connectors(catalog, selection, over=started_on)
        except Exception as exc:
            logger.exception("Failed to apply deferred connector catalog")
            raise _ConnectorLoadError(
                "The connector catalog could not be applied"
            ) from exc

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        session: UnifiedHarnessSessionBackend,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        api: SessionApiCommands | None = None,
        host: UnifiedHarnessSessionBackendHost | None = None,
        services: SessionBackendServices | None = None,
        telemetry_client: TelemetryClient | None = None,
        session_replaced: SessionReplacementCallback | None = None,
        rewrite_plugins: PluginRewrite | None = None,
        deferred_turns: DeferredTurnStartCapability | None = None,
        scheduled_loops_enabled: bool = True,
        launch_context: LaunchContext | None = None,
        user_plan: str | None = None,
        settings: UnifiedSessionSettings | None = None,
        completion_attribution: CompletionAttributionSource | None = None,
        project: SessionProject | None = None,
        worktree_housekeeping: WorktreeHousekeeping | None = None,
    ) -> None:
        self._session = session
        # With the Session API, commands, events and reads go through it, and
        # the Runtime session above serves what the API has no procedure for.
        # Without it, everything goes to the Runtime session directly.
        self._api_path: SessionApiPath | None = _session_api_path(self, api)
        self._commands: SessionCommands = (
            api if api is not None else DirectCommands(session, host)
        )
        self._worktrees = SessionWorktrees(housekeeping=worktree_housekeeping)
        self._deferred_turns = deferred_turns
        session.configure_turn_settlement(self._settle_configuration)
        session.configure_waiting_turn_steer(self._user_joined_the_waiting_turn)
        self._closed = False
        # A plugin switch saved during a turn, run at its boundary.
        self._plugin_reload_parked = False
        self._plugin_reloading = asyncio.Lock()
        self._completion_attribution = completion_attribution
        # Anchor for the legacy-semantics init duration ("session start ->
        # ready"), stamped when this adapter is built so every lifecycle path
        # (fresh start, resume, fork, clear) measures from the same point.
        self._init_started_monotonic: float = time.monotonic()
        # Measured once, at readiness, so the ``vibe.ready`` event and the
        # ``session/ready/wait`` response report the same value. Mirrors the
        # legacy AgentLoop, where the duration is recorded exactly once and
        # reused by the response.
        self._init_duration_ms: int | None = None
        # The Harness exposes root lineage through async session state. The
        # affinity callback is bound now and reads this value after open-time
        # state reconciliation, falling back to this session's own id.
        self._root_session_id: str | None = None
        self._host = host
        self._services = services
        self._turns_deferred: asyncio.Event | None = None
        self._deferred_setup_task: asyncio.Task[None] | None = None
        self._deferred_setup_result: WorktreeResolution | None = None
        self._deferred_turn_claimed = False
        # Queue promotion starts a real user turn inside the Harness and never
        # calls start_turn: track the queue item being promoted so its first
        # observed TurnStarted advances bumped_at exactly once per turn.
        self._promoted_queue_item_ids: set[str] = set()
        self._bumped_queue_item_ids: set[str] = set()
        # Queued by another session: the turn each one starts is not the user's.
        # Idempotency key -> the answer to a session message already delivered,
        # by joining a turn or queueing: a repeated delivery may find the turn
        # ended, or the turn the message itself started, and must not run again.
        # Held in memory, so a delivery repeated after a restart is not caught.
        self._session_messages_delivered: dict[str, TurnEnqueueResponse] = {}
        # TurnStarted repeats while a turn runs; who started it is decided once.
        self._turn_decided_who_started: str | None = None
        self._worktree_progress: WorktreeProgress | None = None
        self._setup_abandoned = False
        self._relocation_active = False
        self._relocation_trust_root: Path | None = None
        self._context = context
        self._worktree_trust_store = context.harness_files.trust_store
        self._settings = replace(
            settings or UnifiedSessionSettings(), session_api=api is not None
        )
        # The derivation Core was last handed, which a parked change does not move.
        self._running = derivation
        self._runtime = derivation.runtime
        self._adapter_config = derivation.adapter_config
        self._model = derivation.adapter_config.model
        self._skills = derivation.skill_payloads
        self._completion_catalogue = derivation.completion_catalogue
        self._image_input = _image_input(derivation)
        self._history_image_warning: asyncio.Task[None] | None = None
        self._bind_completion_sources(derivation)
        # One sink per session: each distinct profile warning is logged and
        # shown once, however many derivations raise it again. A catalogue
        # warning is shown once per process: see ``_emit_profile_warnings``.
        self._completion_warnings = RuntimeWarningSink(_log_completion_warning)
        self._completion_warnings.subscribe(self._show_completion_warning)
        self._unshown_completion_warnings: list[str] = []
        self._storage_root = context.storage_root
        context.model_requests.bind(
            self.session_id,
            Path(self._storage_root) / "unified"
            if context.session_logging_enabled
            else None,
        )
        self._metadata = _read_unified_session_metadata(self._session_dir())
        # A stamp on disk wins: `project` only stamps a session that has none yet.
        self._project = (
            self._metadata.project
            if self._metadata is not None and self._metadata.project is not None
            else project
        )
        self._metadata_lock = asyncio.Lock()
        self._archive_gate = asyncio.Lock()
        # A switch rebuilds the config before it records the agent: a context
        # adopted meanwhile would carry the agent the switch is leaving.
        self._agent_switching = asyncio.Lock()
        self._session_opened_turn_pinning = asyncio.Lock()
        self._scheduled_turn_opening = False
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
        self._mcp_apps = SessionMcpApps()
        self._event_id = 0
        self._open_callbacks: dict[tuple[str, str], PublicCallbackEntry] = {}
        self._events_condition = asyncio.Condition()
        self._events_subscribed = False
        # From ``subscribe`` until its stream ends: a notice raised before the
        # stream's first read waits for it rather than being dropped.
        self._notices_subscribed = False
        # Runtime watermark the direct event stream delivered last, and the
        # direct reads waiting for theirs.
        self._observed_harness_watermark = 0
        self._pending_reads: dict[
            asyncio.Future[SessionReadResponse], HarnessSessionSnapshot
        ] = {}
        self._history_windows = _HistoryWindows()
        self._history_projections = _HistoryProjections()
        self._child_history_projections: dict[str, _HistoryProjections] = {}
        self._child_states: dict[str, PublicSessionState] = {}
        self._child_identities: dict[str, _ChildIdentity] = {}
        self._stopped_children: dict[str, int] = {}
        self._observed_stop_effect_ids: set[str] = set()
        self._telemetry = telemetry_client or TelemetryClient(
            # Moving or relocating the session swaps the context.
            config_getter=lambda: self._context.config_orchestrator.config,
            harness_backend=ExperimentSurface.UNIFIED,
            credentials=context.platform_credentials,
        )
        # Resolved once per open (parent is in async session state, not on the
        # session handle); the telemetry getter reads the cached value so every
        # event on a fork/subagent session carries its parent.
        self._parent_session_id: str | None = None
        # Set when another session started this one to hand it a task.
        self._handed_task_pending = False
        # Session messages and starts that led to the running turn since a
        # user wrote; the peer tools refuse past `MAX_SESSION_HOPS`.
        self._message_hops = 0
        # Sessions the running turn started; `start_session` refuses past
        # `MAX_TURN_STARTED_SESSIONS`.
        self._turn_started_sessions = 0
        self._turn_counting_starts: str | None = None
        # The model picks made while the worktree is prepared, replayed in order
        # to the context that setup brings. Queuing and replaying both hold the
        # lock, so a pick lands either in the replay or after it.
        self._model_picks_after_setup: list[ModelConfigWriteParams] = []
        self._model_pick_replay = asyncio.Lock()
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
        # The user's latest message, which names a worktree the agent did not.
        self._user_request: str | None = None
        self._unlanded_worktree: Path | None = None
        self._unlanded_worktree_checked = False
        self._unlanded_worktree_candidate = False
        # The main-agent turns a notification hook already heard of.
        self._notified_turn_ids: set[str] = set()
        # Whether session_start has run for this session; session_end runs only
        # for one that started. The task runs its hooks; the next message waits.
        self._session_started = False
        self._session_start_task: asyncio.Task[str | None] | None = None
        # Its notices, shown once the client holds the session.
        self._session_start_notices: asyncio.Task[None] | None = None
        self._end_reason = SessionEndReason.OTHER
        # The session's own turns, so the model calls its subagents report
        # into the same telemetry sink are not counted as its own.
        self._root_turn_ids: set[str] = set()
        self._model_calls = 0
        # Per-turn file snapshots answering rewind restores: the harness
        # runtime writes files itself, so the adapter records disk states at
        # the turn boundaries its own state updates observe. A sandboxed
        # session's files are not on this disk, so it records none.
        self._rewind_snapshots: SessionRewindFileSnapshots = (
            RewindFileSnapshots(cwd=lambda: self.cwd)
            if derivation.adapter_config.sandbox is None
            else UnavailableRewindFileSnapshots()
        )
        # The review dispatcher over the live rewind snapshots, rebuilt when
        # their checkpoint history swaps (a fork swaps the snapshots; a
        # rewind reset replaces the checkpointer).
        self._review_handler_cache: tuple[object, ReviewRequestHandler] | None = None
        # PR/MR artifact monitoring for this session. The service exists
        # regardless of the flag — reads (artifacts/list) keep answering from
        # persisted artifacts — while the flag gates detection at the
        # interception point below. It shares ``_metadata_lock`` with sidecar
        # writes so artifact RMW cannot race a full metadata persist.
        self._artifact_service = SessionArtifactService(
            session_id=self.session_id,
            session_dir=self._session_dir(),
            telemetry=self._session_telemetry,
            metadata_lock=self._metadata_lock,
            on_artifacts_changed=self._sync_metadata_artifacts,
        )
        if self._metadata is not None:
            self._artifact_service.load(self._metadata)
        # Phase 2 background task discovering pre-existing PRs/MRs at session
        # load; scheduled from _after_open once the open response is sent.
        self._pr_discovery_task: asyncio.Task[None] | None = None
        self._entering_worktree = False
        self._pr_discovery_after_setup = False
        self._pr_discovery_rerun = False
        self._pr_discovery_every_worktree = False
        # Whether the session's PRs are kept fresh in the process's pull
        # request cache, which it owns as an attached session
        # (``_watch_pull_requests``).
        self._watching_pull_requests = False
        # The artifacts/updated announcements of new reads, in flight.
        self._background_announcements: set[asyncio.Task[None]] = set()
        # Any shell command can create a PR (wrapped, scripted, a PR link the
        # agent opens), so a turn that ran one reruns discovery when it ends,
        # at most once per interval.
        self._shell_ran_since_pr_discovery = False
        self._turn_end_pr_discovery_at: float | None = None
        self._turn_end_pr_discovery_timer: asyncio.TimerHandle | None = None
        # Terminal shell effects awaiting async artifact processing. The
        # collector runs inside the sync state update (_updated_state or the
        # Session API path's root update); the drain runs right after it, async,
        # because the detector resolves glab api. effect_id-keyed so a resumed session's
        # history is not reprocessed.
        self._pending_shell_effects: list[_PendingShellEffect] = []
        self._processed_effect_ids: set[str] = set()
        # Attachment counts of the current turn's user message, read on each
        # ``vibe.request_sent`` its completions emit.
        self._turn_attachment_counts: dict[AttachmentKind, int] = {}
        # Mutable holder the attribution closure reads live; set by the host
        # adapter factory so the provider request metadata carries the current
        # turn's message id.
        self._message_id_holder: list[str | None] = [None]
        # The context size the last snapshot reported, read as
        # ``nb_context_tokens_before`` when a compaction (which resets the gauge to
        # zero in its own snapshot) is reconciled.
        self._context_tokens_before = 0
        self._telemetry_closed = False
        # Set when the client's stream ended without a ``session/stop``.
        self._client_disconnected = False
        self._announce_on_use = False
        # Whether vibe.ready went out for the launch this session stood in for.
        self._ready_reported = False
        self._terminal_seat: TerminalSeat | None = None
        self._seat_pending = False
        self._launch_context = launch_context
        self._user_plan = user_plan
        self._narration = NarrationService(
            lambda: NarrationContext(
                config=self._context.config_orchestrator.config,
                launch_context=self._launch_context,
                parent_session_id=self._session.parent_session_id,
                user_plan=self._user_plan,
                credentials=self._context.provider_credentials,
            )
        )
        self._image_describer = SessionImageDescriber(
            lambda: self._context.config_orchestrator.config,
            notice=lambda message: self._publish_notice(message),
            session_id=lambda: self._session.session_id,
            record_event=self._telemetry.send_telemetry_event,
            credentials=self._context.provider_credentials,
        )
        self._session_replaced = session_replaced
        # One implementation of the account ladder and the identity projection,
        # reached through host Protocols that ``AgentLoop`` also satisfies. The
        # account host reconciles the experiment manager's attribute snapshot,
        # which is the source telemetry reads for plan/segmentation.
        self._account_controller = AccountController(
            _UnifiedAccountHost(context),
            context.account_gateway,
            credentials=context.platform_credentials,
        )
        self._identity_controller = IdentityController(
            _UnifiedIdentityHost(context),
            context.identity_gateway,
            context.platform_credentials,
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
        self._feedback_controller = FeedbackController(
            _UnifiedFeedbackHost(
                # Moving or relocating the session swaps the context.
                config=lambda: self._context.config_orchestrator.config,
                telemetry=self._telemetry,
                require_session=self._require_session,
                history=self._read_history_entries,
            )
        )
        self._experiments_task: asyncio.Task[None] | None = None
        # Whether the eval this session opened with is decided, held or not: a
        # move to a worktree keeps it either way.
        self._experiments_settled = False
        self._experiments_wait: ExperimentsWait | None = None
        self._model_catalog_task: asyncio.Task[None] | None = None
        self._model_catalog_wait: ModelCatalogWait | None = None
        self._registry_sync = RegistrySkillSync(
            config=lambda: self._context.config_orchestrator.config,
            credentials=lambda: self._context.platform_credentials,
            refresh=refresh_registry_skills,
            cleanup=None if context.skill_store is None else context.skill_store.run,
            roots=lambda: self._context.harness_files.project_roots,
            running=lambda: self._running.registry_versions,
            resolve=self._resolved_registry_versions,
            apply=partial(
                self._rederive_in_background,
                "Failed to apply the synced registry skills",
                "apply_registry_skills",
            ),
        )
        self._model_catalog: RemoteFollower[ModelCatalogResponse] | None = None
        self._model_catalog_key: ModelCatalogKey | None = None
        # The catalog this session's Core runs. Per session, like the org
        # config: siblings on the context can run an older one than it holds.
        self._model_catalog_running = _catalog_of(context.config_orchestrator)
        # The pin a catalog was refused for: another pick lets it load.
        self._model_catalog_refused_for: str | None = None
        self._connector_resolve_task: asyncio.Task[None] | None = None
        self._connector_follower: RemoteFollower[ResolvedConnectorCatalog] | None = None
        self._connector_follow: FollowedCatalog | None = None
        self._connector_following = asyncio.Lock()
        # Nothing under it takes a Session lock: a turn start holds those and
        # waits for it.
        self._connectors_converging = asyncio.Lock()
        self._org_config: RemoteFollower[str] | None = None
        self._org_config_key: OrgConfigKey | None = None
        # The org config TOML this session's Core runs. Per session: siblings
        # on the context (rewind, /clear) can run an older one than it holds.
        self._org_config_running = context.org_config_report.loaded
        self._runtime_unannounced = False
        # The configured MCP servers this session last ran, for the next
        # convergence to tell what a new config takes away. Per session:
        # /clear and rewind share the config, not what each session runs.
        self._mcp_running: ResolvedMCPCatalog | None = None
        # A loaded remote value moved what MCP and connectors resolve to; the
        # next boundary converges them.
        self._integrations_due = False
        self._connectors_loaded: asyncio.Event | None = None
        self._connector_error: str | None = None
        self._mcp_discovery_task: asyncio.Task[None] | None = None
        self._mcp_discovered: asyncio.Event | None = None
        # The loads a turn waited for as long as the cap allows.
        self._cold_loads_waited_out: set[asyncio.Event] = set()
        self._mcp_unpublished = False
        self._connectors_unpublished = False
        # Serialises handing Core the tools loaded in the background.
        self._integrations_publishing = asyncio.Lock()
        self._warned_mcp_failures: dict[str, str] = {}
        self._hinted_mcp_logins: set[str] = set()
        # Held until a client subscribes, so a load failing first is not lost.
        self._pending_integration_warnings: list[str] | None = []
        self._reported_org_config: set[AdminConfigOutcome] = set()
        self._deferred = DeferredConfiguration(
            derive=self.derive_configuration,
            push=self._push_configuration,
            adopt=self._adopt_derivation,
            turn_running=lambda: self._session.active_turn_id is not None,
        )
        # Set once the client holds the response that announced this session.
        self._announced = asyncio.Event()
        self._emit_profile_warnings(derivation)
        self._defer_event_flush = False
        self._release_deferred_events: Callable[[], None] | None = None
        self._translated_state: PublicSessionState | None = None
        # Direct compaction updates translated before its reply, by watermark.
        self._pretranslated_events: dict[
            int, tuple[tuple[SessionBackendEvent, ...], PublicSessionState]
        ] = {}
        self._scheduled_loops = UnifiedScheduledLoops(
            self._session_dir() / "scheduled-loops.json",
            persistent=self._persists_to_disk,
        )
        self._child_scheduled_loops: dict[str, UnifiedScheduledLoops] = {}
        self._child_scheduled_loops_lock = asyncio.Lock()
        self._scheduled_loops_enabled = scheduled_loops_enabled
        self._scheduled_loops_task: asyncio.Task[None] | None = None
        self._skill_watch_task: asyncio.Task[None] | None = None
        self._skill_watch_roots: SkillWatchRoots | None = None
        self._skill_watch_started = False
        self._pending_startup_notices: list[str] = []
        self._resolved_cwd: tuple[str | None, Path] | None = None
        self._host_shell = _HostShell(ShellController(self._cwd_path()))

    def runtime_updated_params(self) -> RuntimeUpdatedParams:
        return RuntimeUpdatedParams(session_id=self.session_id, runtime=self._runtime)

    def mark_announced(self) -> None:
        self._announced.set()
        unshown, self._unshown_completion_warnings = (
            self._unshown_completion_warnings,
            [],
        )
        for message in unshown:
            self._publish_notice(message)
        if not self._image_input:
            self._warn_when_history_images_are_replaced()

    def _emit_profile_warnings(self, derivation: UnifiedRuntimeDerivation) -> None:
        # Every derivation carries the cached catalogue warnings, since the one
        # that fetched may never reach a session; the process cache lets the
        # first session that shows them be the only one.
        self._completion_warnings.emit_all(
            PROCESS_CATALOGUE_CACHE.unshown(derivation.profile_warnings)
        )

    def _pinned_host_hooks(self, hooks: Sequence[HostHook]) -> tuple[HostHook, ...]:
        """``hooks`` with each plugin hook set to the session's pinned copy.

        Read when the hooks fire, not when they run: a session_end runs while its
        session closes, which releases the session's plugin set.
        """
        provider = self._plugin_provider
        if provider is None or all(hook.plugin_hook is None for hook in hooks):
            return tuple(hooks)
        bound = provider.first_bound(self.session_id)
        pinned: list[HostHook] = []
        for hook in hooks:
            if hook.plugin_hook is not None:
                # Like the turn hooks: the session's first-bound copy of the plugin.
                copy = pinned_plugin_hook(bound, hook.plugin_hook)
                hook = replace(hook, cwd=copy.cwd, env=dict(copy.environment))
            pinned.append(hook)
        return tuple(pinned)

    async def _run_host_hook(
        self, hook: HostHook, payload: Mapping[str, Any]
    ) -> HostHookResult:
        # Before the run: a session_end hook can outlive the close of this
        # session's telemetry client.
        if not self._telemetry_closed:
            self._telemetry.send_hook_triggered(
                hook_type=str(payload.get("hook_event_name")),
                from_plugin=hook.plugin_hook is not None,
            )
        return await run_host_hook(
            hook.command,
            self._adapter_config,
            payload,
            timeout_s=hook.timeout_s,
            process=HookProcess(cwd=hook.cwd, env=hook.env),
        )

    def _host_hook_payload(self, event: str) -> dict[str, Any]:
        return {
            "hook_event_name": event,
            "session_id": self.session_id,
            # The same folder turn hooks receive.
            "transcript_path": str(self._session_dir().expanduser().resolve()),
            "cwd": str(self._cwd_path()),
            "parent_session_id": self._parent_session_id,
        }

    def _report_hook_failure(self, event: str) -> Callable[[str, str], None]:
        def report(name: str, reason: str) -> None:
            if not self._closed:
                self._publish_notice(f"{event} hook {name!r} failed: {reason}")

        return report

    def _notify_hooks(self, notification: Notification) -> None:
        """Fire the ``notification`` hooks, without waiting for them."""
        if not self._context.session_hooks.notification or self._closed:
            return
        self._context.host_hooks.fire(
            self._pinned_host_hooks(self._context.session_hooks.notification),
            notification.type.value,
            {
                **self._host_hook_payload("notification"),
                "notification_type": notification.type.value,
                "message": notification.message,
                "agent_name": notification.agent_name,
                **(
                    {"tool_name": notification.tool_name}
                    if notification.tool_name is not None
                    else {}
                ),
            },
            run=self._run_host_hook,
            report_failure=self._report_hook_failure("notification"),
        )

    def start_session_hooks(self, source: SessionStartSource) -> None:
        """Run the session_start hooks for a main session that just opened.

        The user can type at once; the session's next user message waits for the
        hooks, then carries their context to the model. The session_end hooks
        already running (the session this one replaces) finish first.
        """
        if self._session_started:
            return
        self._session_started = True
        hooks = self._pinned_host_hooks(self._context.session_hooks.session_start)
        if not hooks:
            return
        try:
            model: str | None = (
                self._context.config_orchestrator.config.get_active_model().alias
            )
        except ValueError:
            model = None
        payload = {
            **self._host_hook_payload("session_start"),
            "source": source.value,
            "agent_name": self._adapter_config.active_agent,
            "model": model,
        }

        host_hooks = self._context.host_hooks
        ending = host_hooks.ending()

        async def show(
            failures: Sequence[tuple[str, str]], messages: Sequence[tuple[str, str]]
        ) -> None:
            # A notice before the client holds the session would reach no one. A
            # session that ends before the client gets it stops this task (see
            # ``_forget_session_start``).
            await self.wait_announced()
            if self._closed:
                return
            for name, failure in failures:
                self._publish_notice(f"session_start hook {name!r} failed: {failure}")
            # Not a warning: the hook's own line, as other hooks show theirs.
            for name, message in messages:
                self._publish_notice(
                    f"[{name}] {message}", level="info", code=HOOK_MESSAGE_CODE
                )

        async def run() -> str | None:
            if ending:
                # Bounded by those hooks' own timeouts.
                await asyncio.wait(ending)
            result = await host_hooks.run_session_start(
                hooks, source.value, payload, run=self._run_host_hook
            )
            # The context is the next message's at once: that message may be
            # the session/start's first turn, which runs before the session is
            # announced.
            if result.failures or result.system_messages:
                self._session_start_notices = host_hooks.track(
                    show(result.failures, result.system_messages),
                    name="vibe-session-start-notices",
                )
            return result.additional_context

        task = host_hooks.track(run(), name="vibe-session-start-hooks")
        if task is not None:
            self._session_start_task = task
            self._context.session_start_contexts.set(self.session_id, task)

    def _forget_session_start(self) -> asyncio.Task[str | None] | None:
        notices, self._session_start_notices = self._session_start_notices, None
        if notices is not None and not self._announced.is_set():
            # Waiting for an announcement that will never come.
            notices.cancel()
        task, self._session_start_task = self._session_start_task, None
        if task is not None:
            self._context.session_start_contexts.discard(self.session_id, task)
            # Also when a turn already took it and is waiting on it.
            task.cancel()
        return task

    def end_session_hooks(
        self, reason: SessionEndReason
    ) -> tuple[asyncio.Task[None], ...]:
        """Run the session_end hooks in the background, once per started session,
        and return their tasks.

        They run once this session's session_start hooks have stopped, and the
        next session's session_start waits for both. Never raises: a close must
        go on whatever its hooks do.
        """
        self._end_reason = reason
        if not self._session_started:
            return ()
        self._session_started = False
        starting = self._forget_session_start()
        try:
            host_hooks = self._context.host_hooks
            if starting is not None and not starting.done():
                host_hooks.mark_ending(starting)
            else:
                starting = None
            hooks = self._pinned_host_hooks(self._context.session_hooks.session_end)
            if not hooks:
                return ()
            return host_hooks.fire(
                hooks,
                reason.value,
                {**self._host_hook_payload("session_end"), "reason": reason.value},
                run=self._run_host_hook,
                ending=True,
                after=starting,
            )
        except Exception:
            logger.warning("Failed to start the session_end hooks", exc_info=True)
            return ()

    def abandon_session_hooks(self) -> None:
        """Forget this session's session_start without a session_end: the client
        never got the session.
        """
        self._session_started = False
        self._forget_session_start()

    def _notify_callback_opened(self, callback: PublicCallbackEntry) -> None:
        if not self._context.session_hooks.notification:
            return
        child = self._child_identities.get(callback.session_id)
        agent_name = (
            child.agent_type if child is not None else self._adapter_config.active_agent
        )
        notification = callback_notification(callback, agent_name)
        if notification is not None:
            self._notify_hooks(notification)

    def _notify_turn_ended(self, turn: PublicTurn, state: PublicSessionState) -> None:
        """Fire for the end of a main-agent turn, as the CLI rings its bell.

        Nothing while the queue still holds a turn to run next, or while an approval or
        a question waits for the user: they get their own notification.
        """
        if (
            not self._context.session_hooks.notification
            or turn.session_id != self.session_id
        ):
            return
        # A snapshot can report a finished turn again, when any of its fields
        # changes: one notification per turn. A report held back below does not
        # count, so a later one can still fire.
        if turn.id in self._notified_turn_ids:
            return
        # A failed turn pauses the queue, but only in a later update: it does not
        # wait for the queue.
        if (
            turn.status is PublicTurnStatus.COMPLETED
            and state.turn_queue.items
            and not state.turn_queue.paused
        ):
            return
        if self.open_callbacks():
            return
        self._notified_turn_ids.add(turn.id)
        notification = turn_notification(turn, self._adapter_config.active_agent)
        if notification is not None:
            self._notify_hooks(notification)

    def _record_turn_failure(self, turn: PublicTurn) -> None:
        """Emit ``vibe.turn.failed`` once per failed main-agent turn."""
        if (
            turn.status is not PublicTurnStatus.FAILED
            or turn.session_id != self.session_id
            or not self._session_telemetry.claim(f"turn:{turn.id}")
        ):
            return
        error = turn.error
        self._telemetry.send_turn_failed(
            error_code=error.code if error is not None else None,
            http_status=_turn_error_http_status(error) if error is not None else None,
            message_id=self._turn_client_message_ids.get(turn.id),
        )

    def _show_completion_warning(self, warning: CompletionWarning) -> None:
        # A notice before the client holds the session would reach no one.
        if self._announced.is_set():
            self._publish_notice(warning.message)
        else:
            self._unshown_completion_warnings.append(warning.message)

    def _warn_when_history_images_are_replaced(self) -> None:
        """Tell the user a model without image input gets ``[Image]`` for the session's images.

        Queued input counts: an image queued for a model that reads images
        was not described, and reaches the new model as ``[Image]``.

        Reading the history waits on the Runtime, so it runs beside the caller:
        an open or a model switch does not wait for it.
        """
        if (task := self._history_image_warning) is not None and not task.done():
            return

        async def warn() -> None:
            try:
                if await self._session.holds_images():
                    self._show_completion_warning(IMAGES_REPLACED)
            except Exception:
                logger.warning("Failed to check history for images", exc_info=True)

        self._history_image_warning = asyncio.create_task(warn())

    def _cancel_history_image_warning(self) -> asyncio.Task[None] | None:
        task = self._history_image_warning
        self._history_image_warning = None
        if task is not None and not task.done():
            task.cancel()
            return task
        return None

    async def wait_announced(self) -> None:
        await self._announced.wait()

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

    @property
    def provider_credentials(
        self,
    ) -> Callable[[str], ProviderCredentialProvider] | None:
        return self._context.provider_credentials

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
        self._require_no_session_rewrite()
        if self._rewrite_plugins is None:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "This session has no plugin writer, so there is nothing to reload.",
            )
        if self._session.active_turn_id is not None:
            # A re-pin cannot swap the tools under a running turn: the turn
            # boundary runs the reload instead (``_settle_configuration``).
            self._plugin_reload_parked = True
            return PluginReloadResponse()
        async with self._plugin_reloading:
            self._plugin_reload_parked = False
            try:
                await self._reload_plugins_now()
            except SessionBackendError as exc:
                # A turn opened while the set was being rebuilt, and the harness
                # refused the re-pin: its boundary runs the reload instead.
                if not _is_turn_conflict(exc):
                    raise
                self._plugin_reload_parked = True
        return PluginReloadResponse()

    async def reload_switched_plugins(self) -> None:
        """Re-pin a restored session onto the plugins switched since it last ran.

        A restore binds the saved lock, and only the sessions open at a switch
        were reloaded. A set that did not change costs the rescan alone. Best
        effort: a session that cannot take it opens on its saved set.
        """
        if self._rewrite_plugins is None:
            return
        async with self._plugin_reloading:
            try:
                requested = await self._rescan_plugins()
                lock = await self._session.read_plugin_lock()
                if {plugin.name for plugin in requested} == {
                    entry.name for entry in lock.plugins
                }:
                    return
                if self._session.active_turn_id is not None:
                    self._plugin_reload_parked = True
                    return
                await self._repin_plugins(requested)
            except Exception as exc:
                if isinstance(exc, SessionBackendError) and _is_turn_conflict(exc):
                    self._plugin_reload_parked = True
                    return
                logger.warning(
                    "Failed to apply the plugin switches saved since session_id=%s "
                    "last ran",
                    self.session_id,
                    exc_info=True,
                )

    async def _reload_plugins_now(self) -> None:
        if self._rewrite_plugins is None:
            return
        await self._repin_plugins(await self._rescan_plugins())

    async def _rescan_plugins(self) -> list[ResolvedPluginDefinition]:
        try:
            return await self._plugin_provider.rescan()
        except PluginReloadUnavailableError as error:
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED, str(error)
            ) from error

    async def _repin_plugins(self, requested: list[ResolvedPluginDefinition]) -> None:
        rewrite_plugins = self._rewrite_plugins
        if rewrite_plugins is None:
            return
        previous = self._plugin_provider.bound(self.session_id) or self._plugins
        await _harness_call(rewrite_plugins(self.session_id, requested))
        # Past the write, so this is the new set. Adopting it keeps the
        # adapter's own reads off the resolve the session started with.
        bound = self._plugin_provider.bound(self.session_id)
        if bound is not None:
            # Against the set the session's hooks still run from, not the last
            # reload's: those are the hooks the notice says the session keeps.
            running = (
                self._plugin_provider.first_bound(self.session_id) or self._plugins
            )
            hooks_changed = plugin_hook_signature(bound) != plugin_hook_signature(
                running
            )
            self._plugins = bound
            for notice in plugin_reload_notices(bound):
                self._publish_notice(notice)
            if hooks_changed:
                self._publish_notice(PLUGIN_HOOKS_RELOAD_NOTICE)
            # Re-derived so the skill catalogue, payloads, issues and library
            # environment follow the new set, then and on every later derivation.
            self._settings = replace(self._settings, plugins=bound)
            await self._apply_derivation()
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
        if bound is not None:
            await self._tell_plugin_change(plugin_change_context(previous, bound))

    async def _reload_parked_plugins(self) -> None:
        """Run at the turn boundary, so a failure must never stop the next turn."""
        if not self._plugin_reload_parked:
            return
        async with self._plugin_reloading:
            if self._closed or self._session.active_turn_id is not None:
                return
            self._plugin_reload_parked = False
            try:
                await self._reload_plugins_now()
            except Exception:
                logger.warning(
                    "Failed to reload the plugins parked for session_id=%s",
                    self.session_id,
                    exc_info=True,
                )

    async def _tell_plugin_change(self, change: str | None) -> None:
        # A session nobody has used yet has nothing out of date to correct,
        # and the note would promote and persist it.
        if change is None or self._session.ephemeral:
            return
        try:
            await self.inject_context(
                ContextInjectParams(
                    session_id=self.session_id, input=[TextContentBlock(text=change)]
                )
            )
        except SessionBackendError as exc:
            if not _is_turn_conflict(exc):
                raise

    def _artifact_handler(self) -> ArtifactRequestHandler:
        """Answer the ``artifacts/`` namespace over this session's artifacts.

        The service is bound at session start/resume regardless of the feature
        flag — reads keep answering from persisted artifacts — while the flag
        gates detection at the interception point.
        """
        return ArtifactRequestHandler(
            self._artifact_service,
            self._require_session,
            emit_updated=self._emit_artifact_updated,
        )

    def _review_handler(self) -> ReviewRequestHandler:
        """Answer the review protocol over this session's checkpoint history.

        The legacy backend reviews the engine's own checkpointer; the harness
        runtime writes files itself, so the unified backend reviews the same
        per-turn snapshots rewind restores from. The handler is stateless, so
        one is cached per checkpoint history — a fork's snapshot swap or a
        rewind reset's checkpointer replacement rebuilds it over the
        surviving history. The cache lives on this per-session adapter, so a
        new process builds a new adapter — and cache — anyway.
        """
        snapshots = self._rewind_snapshots
        key = snapshots.review_cache_key()
        cached = self._review_handler_cache
        if cached is not None and cached[0] is key:
            return cached[1]
        handler = ReviewRequestHandler(
            snapshots.review_manager(), self._require_session, self._require_idle
        )
        self._review_handler_cache = (key, handler)
        return handler

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

    async def _dispatch_session_reads(
        self, method: str, raw_params: dict[str, Any]
    ) -> ProtocolModel:
        """The session-scoped read procedures, grouped to keep the router flat."""
        match method:
            case "account/read":
                params = validate_wire(AccountReadParams, raw_params)
                self._require_session(params.session_id)
                return AccountReadResponse(account=await self._read_account())
            case "identity/read":
                params = validate_wire(IdentityReadParams, raw_params)
                self._require_session(params.session_id)
                return IdentityReadResponse(
                    identity=await self._identity_controller.read()
                )
            case "providerAuth/read":
                params = validate_wire(ProviderAuthReadParams, raw_params)
                self._require_session(params.session_id)
                return ProviderAuthReadResponse(
                    auth=await read_provider_auth(
                        self._context.config_orchestrator.config
                    )
                )
        raise method_not_found(method)

    @property
    def worktree_trust_store(self) -> TrustedFoldersManager:
        return self._worktree_trust_store

    async def dispatch_extension(  # noqa: PLR0911, PLR0912 - one branch per protocol method
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "runtime/read":
                validate_wire(RuntimeReadParams, raw_params)
                response = RuntimeReadResponse(
                    runtime=self._runtime,
                    session_log=await self._session_log_summary(),
                    ready=self._is_ready(),
                )
            case "session/shellCommand":
                return await self._dispatch_shell_command(raw_params)
            case "session/backgroundProcess/output":
                response = await self._read_background_process_output(raw_params)
            case "session/backgroundProcess/stop":
                response = await self._stop_background_process(raw_params)
            case _ if method.startswith("session/"):
                response = await self._dispatch_session_extension(method, raw_params)
            case _ if method.startswith("plugin/"):
                return await self._dispatch_plugin(method, raw_params)
            case "account/read" | "identity/read" | "providerAuth/read":
                response = await self._dispatch_session_reads(method, raw_params)
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
            case _ if method.startswith("feedback/"):
                return await self._feedback_controller.dispatch(method, raw_params)
            case "workspace/prompt/prepare":
                params = validate_wire(WorkspacePromptPrepareParams, raw_params)
                self._require_session(params.session_id)
                response = await self._prepare_prompt_response(params)
            case "workspace/trust/decision":
                return await self._workspace_trust_decision(raw_params)
            case _ if method.startswith("artifacts/"):
                return await self._artifact_handler().dispatch(method, raw_params)
            case _ if method.startswith("review/"):
                await self.flush_events()
                # Cached per checkpoint history, so a fork's swap or a rewind
                # reset is picked up instead of reviewing discarded history.
                return self._review_handler().dispatch(method, raw_params)
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _stop_background_process(
        self, raw_params: dict[str, Any]
    ) -> BackgroundProcessStopResponse:
        params = validate_wire(BackgroundProcessStopParams, raw_params)
        self._require_session(params.session_id)
        stop_process = cast(
            Callable[[str], Awaitable[Any]] | None,
            getattr(self._session, "stop_background_process", None),
        )
        if not callable(stop_process):
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "This Harness does not support background process control",
            )
        result = await _harness_call(stop_process(params.process_id))
        return BackgroundProcessStopResponse.model_validate(
            result.model_dump(mode="json", by_alias=True)
        )

    async def _read_background_process_output(
        self, raw_params: dict[str, Any]
    ) -> BackgroundProcessOutputResponse:
        params = validate_wire(BackgroundProcessOutputParams, raw_params)
        self._require_session(params.session_id)
        read_output = cast(
            Callable[[Any], Awaitable[Any]] | None,
            getattr(self._session, "read_background_process_output", None),
        )
        if not callable(read_output):
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "This Harness does not support background process output",
            )
        request = harness_session_protocol.BackgroundProcessOutputRead(
            process_id=params.process_id,
            from_end=params.from_end,
            cursor=params.cursor,
            wait_ms=params.wait_ms,
            max_bytes=params.max_bytes,
        )
        result = await _harness_call(read_output(request))
        output = result.model_dump(mode="json", by_alias=True)
        if result.availability == "unavailable":
            return validate_backend_wire(
                BackgroundProcessOutputUnavailableResponse, output
            )
        return validate_backend_wire(BackgroundProcessOutputAvailableResponse, output)

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

        The model reads the result through the Harness's own ``inject_context``,
        which is where the legacy backend puts it too, so the block reaching the
        model is the same one on both backends. The terminal reads the result
        off a ``shell`` effect entry, and Core owns model history in a Unified
        session -- a Host writing a manual command into it would author a turn
        out of a user's aside. The entry is therefore projected here into the
        live event stream, exactly as ``TurnController`` does for the legacy
        backend, and its terminal form is also recorded in the Harness's
        public history store, which never reaches the model: the record
        preserves what the user saw across resume without authoring
        conversation.

        Recording follows the inject -- the model's context is the priority,
        and a crash between the two leaves the transcript silent, never a
        transcript entry whose output the model cannot see. Every output field
        in the record is capped at the model limit, so the store never holds
        more output than the model itself was shown.
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
        if params.operation_id is not None:
            await self._reject_recorded_shell_operation(operation_id)
        run_params = ShellRunParams(
            session_id=params.session_id,
            operation_id=operation_id,
            command=command,
            timeout_seconds=params.timeout_seconds or 30.0,
            cwd=self._workspace_cwd(params.cwd),
        )
        limit = manual_shell_output_limit(self._context.config_orchestrator.config)
        result, entry = await self._run_manual_shell(run_params)

        try:
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
        except BaseException:
            # The command ran and the terminal saw it, so its record must
            # survive even when the inject failed and the model saw nothing.
            await self._record_shell_effect_best_effort(entry, limit)
            raise
        await self._record_shell_effect(entry, limit)
        return DispatchResult(
            SessionShellCommandResponse(last_event_id=self._event_id),
            after_response=injected.after_response,
        )

    async def _run_manual_shell(
        self, params: ShellRunParams
    ) -> tuple[ShellRunResponse, PublicEffectEntry]:
        """Run the command behind a ``shell`` effect entry the terminal can draw."""
        effect = _ManualShellEffect(
            EventProjector(params.session_id, None), params.operation_id
        )
        started_at = time.monotonic()
        limit = manual_shell_output_limit(self._context.config_orchestrator.config)

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
            # Shutdown may already be tearing the session down, so the record
            # is best-effort: it must never mask the cancellation itself.
            await self._record_shell_effect_best_effort(effect.record, limit)
            raise
        except Exception as exc:
            self._publish_local_event(
                effect.complete(
                    shell_effect_error(
                        exc, output_text=effect.output_text, duration_ms=elapsed_ms()
                    )
                )
            )
            # No inject happens for a failed command, so the record is all the
            # trace it leaves; recording it is still best-effort because the
            # original failure is what the caller must see.
            await self._record_shell_effect_best_effort(effect.record, limit)
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
        return result, effect.record

    async def _record_shell_effect(self, entry: PublicEffectEntry, limit: int) -> None:
        """Persist one terminal manual-shell effect in the Harness store.

        The translated state must learn the stored entry before the append
        publishes it: the live stream already delivered this entry to the
        client, so a snapshot translation that still considers it new would
        emit a duplicate ``history/entryAdded`` and break the client's
        projection. Merging the exact stored (capped) entry keeps the snapshot
        reconcile silent -- and if the record itself fails, the merged entry
        is a harmless phantom the Harness never echoes back.
        """
        state = capped_shell_effect_state(entry.state, limit)
        # The snapshot pipeline re-projects a completed effect's output (it
        # adds the output model's defaults, like ``truncated``); merge the
        # entry in that already-normalized form so the reconcile that follows
        # the record finds it equal and stays silent.
        recorded = capped_effect_entry(
            cast(
                PublicEffectEntry,
                normalize_effect_output(entry.model_copy(update={"state": state})),
            )
        )
        self._merge_translated_entry(recorded)
        await _harness_call(
            self._session.record_host_effect(
                harness_session_protocol.HostEffectRecord(
                    id=recorded.id,
                    title=recorded.title,
                    turn_id=recorded.turn_id,
                    created_at=recorded.created_at,
                    updated_at=recorded.updated_at,
                    detail=cast(
                        dict[str, JsonValue], recorded.detail.model_dump(mode="json")
                    ),
                    state=cast(dict[str, JsonValue], state.model_dump(mode="json")),
                )
            )
        )

    def _merge_translated_entry(self, entry: PublicHistoryEntry) -> None:
        state = self._translated_state
        if state is None:
            return
        history = state.history or []
        if any(existing.id == entry.id for existing in history):
            history = [
                entry if existing.id == entry.id else existing for existing in history
            ]
        else:
            history = [*history, entry]
        self._translated_state = state.model_copy(update={"history": history})

    async def _record_shell_effect_best_effort(
        self, entry: PublicEffectEntry, limit: int
    ) -> None:
        """Record a terminal effect without ever masking the failure at hand."""
        try:
            await self._record_shell_effect(entry, limit)
        except Exception as exc:
            logger.exception(
                "Failed to record manual shell effect %s in session %s",
                entry.id,
                self.session_id,
            )
            _report(exc, "record_shell_effect")

    async def _reject_recorded_shell_operation(self, operation_id: str) -> None:
        """Reject an operation id the session already recorded.

        The authoritative guard lives in the Harness store; this pre-check
        fails the request before the command runs, so a reused id cannot strand
        a live effect whose record the store would refuse. It reads the latest
        history window, so an id older than the window is still caught by the
        store's own duplicate guard when the record lands.
        """
        read = await _harness_call(
            self._session.read(
                HarnessSessionReadParams(session_id=self.session_id, history_limit=200)
            )
        )
        for entry in read.snapshot.state.history.entries:
            if isinstance(entry, dict) and entry.get("id") == operation_id:
                raise SessionBackendError(
                    ProtocolErrorCode.CONFLICT,
                    f"Shell operation already exists: {operation_id}",
                )

    def _publish_local_event(
        self, event: AppServerEvent, *, buffer: bool = False
    ) -> None:
        """Queue an event the Host minted for this session's own event stream.

        Dropped when nothing is subscribed, unless ``buffer`` holds it for the
        subscription that is about to start. The queue is drained by the stream,
        so buffering without one would stall the next ``flush_events`` on an
        event no Client is waiting for.
        """
        if not self._events_subscribed and not buffer:
            return
        self._event_id += 1
        self._host_shell.pending += 1
        self._host_shell.queue.put_nowait(_event_envelope(event, self._event_id))

    def _publish_notice(
        self, message: str, *, level: str = "warning", code: str | None = None
    ) -> None:
        """Send the Client a remark of the Host's own as a ``warning``.

        It rides the Host's side of the event stream, not the Runtime's: the
        Session API has no procedure for it, and a remark changes nothing about
        the Session, so the Session never stores it. ``code`` names it for a
        Client that handles it on its own. Dropped when no subscription is
        open, as the Runtime drops an event nobody follows.
        """
        if not (self._events_subscribed or self._notices_subscribed):
            return
        self._host_shell.pending += 1
        self._host_shell.queue.put_nowait(
            self._notice_event({"message": message, "level": level, "code": code})
        )

    def _workspace_cwd(self, requested_cwd: str | None) -> str:
        try:
            return resolve_workspace_cwd(self._cwd_path(), requested_cwd)
        except RequestFailure as exc:
            raise SessionBackendError(exc.code, str(exc), exc.data) from exc

    async def _dispatch_plugin(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method == "plugin/info":
            params = validate_wire(PluginInfoParams, raw_params)
            self._require_session(params.session_id)
            return DispatchResult(
                PluginInfoResponse(info=await self._read_plugin_info())
            )
        if method == "plugin/reload":
            params = validate_wire(PluginReloadParams, raw_params)
            self._require_session(params.session_id)
            return DispatchResult(await self._reload_plugins(), runtime_updated=True)
        raise method_not_found(method)

    async def _dispatch_narration(
        self, raw_params: dict[str, Any]
    ) -> NarrationSummarizeResponse:
        params = validate_wire(NarrationSummarizeParams, raw_params)
        self._require_session(params.session_id)
        summary = await self._narration.summarize(params)
        return NarrationSummarizeResponse(summary=summary)

    async def _wait_for_subagents(
        self, timeout_ms: int
    ) -> SessionSubagentsWaitResponse:
        """Wait up to ``timeout_ms`` for this session's subagents to settle.

        Starts no turn of this session's: a subagent's late result stays
        queued for it.
        """
        host = self._host
        if host is None:
            return SessionSubagentsWaitResponse(settled=True)
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                await host.wait_for_children_settled(self.session_id)
        except TimeoutError:
            settled = False
        else:
            settled = True
        running = [
            SubagentRef(
                session_id=record.child_session_id,
                name=record.agent_name,
                agent_type=record.agent_type,
            )
            for record in host.child_session_records(self.session_id)
            if record.state.type in _RUNNING_CHILD_STATES
        ]
        return SessionSubagentsWaitResponse(settled=settled, running=running)

    async def _interrupt_subagents(
        self, reason: str
    ) -> SessionSubagentsInterruptResponse:
        host = self._host
        if host is None:
            return SessionSubagentsInterruptResponse()
        results = await host.interrupt_children(self.session_id, reason)
        return SessionSubagentsInterruptResponse(
            results=[
                SubagentInterruptResult(
                    session_id=result.child_session_id,
                    name=result.agent_name,
                    outcome=_INTERRUPT_OUTCOMES[result.outcome],
                    error=result.error,
                )
                for result in results
            ]
        )

    async def _read_subagents(self, timeout_ms: int) -> SessionSubagentsReadResponse:
        host = self._host
        if host is None:
            return SessionSubagentsReadResponse()
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        return SessionSubagentsReadResponse(
            subagents=await read_subagent_runs(
                host,
                self.session_id,
                self._context.subagent_steps,
                storage_root=self._storage_root,
                pricing=self._subagent_pricing(),
                deadline=deadline,
            )
        )

    def _subagent_pricing(self) -> SubagentPricing:
        # The root's prices are its runtime's, which price its own cost.
        try:
            root_alias: str | None = self._session_model().alias
        except ValueError:
            root_alias = None
        return SubagentPricing(
            models=self._context.config_orchestrator.config.models,
            root_alias=root_alias,
            root_prices=self._runtime.stats,
        )

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
            case "session/context/read":
                params = validate_wire(SessionContextReadParams, raw_params)
                self._require_readable_session(params.session_id)
                response = SessionContextReadResponse(
                    breakdown=await self._context_breakdown(params.session_id)
                )
            case "session/ready/wait":
                # Block on the background experiment eval so new_session/ready have
                # fired and the plan snapshot is resolved before the client records
                # startup-class events (vibe.startup, etc.) — parity with the legacy
                # AgentLoop, which awaits experiments in wait_until_ready. If that
                # eval was cancelled (rapid start/stop) the fresh-start callback
                # never ran, so report not-ready to keep the client from emitting
                # unpaired startup telemetry, mirroring the legacy skip.
                # Not the connector and MCP loads, unless asked: clients hold
                # the first message until ready, and a message never waits on
                # them. Their outcome reaches the client as runtime/updated and
                # a warning.
                if _ready_wait_integrations(raw_params):
                    await self._wait_for_integration_loads()
                    await _harness_call(self._session.wait_for_recovered_turn())
                ready = not self._closed
                task = self._experiments_task
                if task is not None:
                    await asyncio.wait({task})
                    if task.cancelled():
                        ready = False
                # And a held catalog fetch, so the model and the warnings the
                # client reads next already reflect the catalog.
                await self._wait_for_model_catalog()
                # The measured session start -> ready duration, recorded once
                # and shared with the ``vibe.ready`` event so both agree.
                response = SessionReadyWaitResponse(
                    ready=ready, init_duration_ms=self._measured_init_duration_ms()
                )
            case "session/ready/read":
                response = SessionReadyReadResponse(ready=self._is_ready())
            case "session/stop":
                params = validate_wire(SessionStopParams, raw_params)
                self._require_session(params.session_id)
                response = SessionStopResponse()
            case (
                "session/subagents/wait"
                | "session/subagents/interrupt"
                | "session/subagents/read"
            ):
                response = await self._dispatch_subagents(method, raw_params)
            case "session/history/list":
                history_params = validate_wire(SessionHistoryListParams, raw_params)
                self._require_readable_session(history_params.session_id)
                response = await self._history_list(history_params)
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
                entry = await self._rewound_entry(params.entry_id)
                paths = self._rewind_snapshots.restorable_paths(entry.id)
                response = SessionRewindReadResponse(
                    has_file_changes=bool(paths), paths=paths
                )
            case _:
                raise method_not_found(method)
        return response

    async def _dispatch_subagents(
        self, method: str, raw_params: dict[str, Any]
    ) -> ProtocolModel:
        """Answer the experimental ``session/subagents/*`` methods."""
        match method:
            case "session/subagents/wait":
                wait_params = validate_wire(SessionSubagentsWaitParams, raw_params)
                self._require_session(wait_params.session_id)
                return await self._wait_for_subagents(wait_params.timeout_ms)
            case "session/subagents/interrupt":
                interrupt_params = validate_wire(
                    SessionSubagentsInterruptParams, raw_params
                )
                self._require_session(interrupt_params.session_id)
                return await self._interrupt_subagents(interrupt_params.reason)
            case "session/subagents/read":
                read_params = validate_wire(SessionSubagentsReadParams, raw_params)
                self._require_session(read_params.session_id)
                return await self._read_subagents(read_params.timeout_ms)
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
            case "config/effective/read":
                effective_params = validate_wire(ConfigEffectiveReadParams, raw_params)
                self._require_session(effective_params.session_id)
                return effective_config_response(
                    self._context.config_orchestrator.config,
                    bypass_tool_permissions=self._runtime.bypass_tool_permissions,
                )
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
                await self._write_proxy_settings(proxy_write)
                return EmptyResponse()
            case _:
                raise method_not_found(method)

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        self._require_readable_session(params.session_id)
        if params.session_id != self.session_id:
            return await self._read_child_response(_harness_read_params(params))
        if self._api_path is not None:
            return await self._api_path.read(params)
        snapshot = await self._read_current_snapshot(params)
        return await self._read_snapshot_response(
            _without_probe(snapshot, params.history_limit)
        )

    async def _read_snapshot_response(
        self, snapshot: HarnessSessionSnapshot
    ) -> SessionReadResponse:
        if not self._events_subscribed:
            return self._read_response(snapshot)
        # A Harness event can produce several public events. Capture the public
        # cursor when this snapshot's Harness watermark is delivered, before
        # later events can advance it. Reads must not advance the live cursor.
        if snapshot.watermark <= self._observed_harness_watermark:
            return self._project_read(snapshot)
        observed: asyncio.Future[SessionReadResponse] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_reads[observed] = snapshot
        try:
            return await observed
        finally:
            del self._pending_reads[observed]

    async def _with_older_history(
        self, snapshot: HarnessSessionSnapshot, state: PublicSessionState
    ) -> PublicSessionState:
        # The live window keeps its limit, so a one-off wider read probes past it.
        history_limit = snapshot.history_limit
        entries = snapshot.state.history.entries
        if (
            state.history_before_cursor is not None
            or not state.history
            or len(entries) < history_limit
        ):
            return state
        try:
            probe = await self._session.read(
                HarnessSessionReadParams(
                    session_id=self.session_id, history_limit=history_limit * 2
                )
            )
        except Exception as exc:
            logger.warning("Could not probe for older history", exc_info=exc)
            return state
        probed = [entry.get("id") for entry in probe.snapshot.state.history.entries]
        # Entries appended meanwhile shift the probe forward; only an entry
        # before the loaded page's first one proves older history.
        first = entries[0].get("id")
        if first not in probed or probed.index(first) == 0:
            return state
        return state.model_copy(update={"history_before_cursor": state.history[0].id})

    async def _read_current_snapshot(
        self, params: SessionReadParams
    ) -> HarnessSessionSnapshot:
        async with self._events_condition:
            result = await self._session.read(_harness_probe_read_params(params))
            while self._events_subscribed:
                active_turn_id = self._session.active_turn_id
                latest_turn = result.snapshot.state.latest_turn
                if active_turn_id is None or (
                    latest_turn is not None and latest_turn.id == active_turn_id
                ):
                    break
                # turn/start reserves execution before the Runtime publishes
                # its first state. Do not report the preceding idle turn after
                # the caller has already received acceptance of the new one.
                await self._events_condition.wait()
                result = await self._session.read(_harness_probe_read_params(params))
            return result.snapshot

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
        except Exception as exc:
            logger.exception(
                "Failed to persist Unified session bumped_at for session_id=%s",
                self.session_id,
            )
            _report(exc, "persist_session_metadata")
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
            except Exception as exc:
                logger.exception(
                    "Failed to publish Unified session metadata update "
                    "for session_id=%s",
                    metadata.session_id,
                )
                _report(exc, "publish_session_metadata")
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
            update={"event_id": self._event_id, "session": session}
        )

    async def subscribe(self, params: SessionReadParams) -> SessionEventSubscription:
        if self._api_path is not None:
            return await self._api_path.subscribe(params)
        subscription = await self._session.subscribe(_harness_read_params(params))
        self._publish_startup_notices()
        self._seed_tool_telemetry(subscription.snapshot.state)
        self._seed_artifact_effects(subscription.snapshot.state)
        snapshot = self._read_response(subscription.snapshot)
        snapshot = snapshot.model_copy(
            update={
                "state": await self._with_older_history(
                    subscription.snapshot, snapshot.state
                )
            }
        )
        self._seed_stats(snapshot.state)
        # A resumed session's next compaction reports the size it replaced; keep
        # the __init__ default of 0 when the snapshot carries no measurement.
        self._track_context_tokens(snapshot.state)
        self._translated_state = snapshot.state
        async with self._events_condition:
            self._observed_harness_watermark = subscription.snapshot.watermark
            self._events_condition.notify_all()
        return SessionEventSubscription(
            snapshot=snapshot, events=self._translated_events(subscription)
        )

    async def flush_events(self) -> None:
        # Host-minted events go out first and unconditionally: a Client that
        # asked to wait for incoming notifications waits only for the ones that
        # preceded the response, so a `!` command answering ahead of its own
        # effect entry would leave the terminal with nothing to draw.
        await self._flush_host_events()
        # Compaction releases its events after its response. Concurrent
        # requests must not wait for that same deferred batch before replying.
        if self._defer_event_flush:
            return
        if self._api_path is not None:
            await self._api_path.flush_events()
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
        # Applies while a requested worktree is still being prepared: a client
        # picks the profile before its first message, and adopt_context carries
        # it into the worktree's context.
        # Entering or leaving smart-approve flips gated tools between "classify" and
        # "ask"/"allow" in the adapter config, which _apply_derivation pushes to the
        # live session -- no Core hook rebinding, so the switch is safe mid-session.
        async with self._agent_switching:
            agents = self._context.agents
            previous = agents.active_profile.name
            try:
                await agents.aswitch_profile(params.agent_name)
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
        await self._persist_pin(SessionPin.AGENT_NAME, params.agent_name)
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
            await self._context.agents.aswitch_profile(name)
            await self._apply_agent_derivation()
        except Exception:
            logger.exception(
                "Failed to restore the agent profile after a rejected switch agent=%s",
                name,
            )

    async def install_agent(
        self, params: AgentInstallParams
    ) -> SessionBackendResult[AgentsListResponse]:
        return await self._set_agent_installed(params, installed=True)

    async def uninstall_agent(
        self, params: AgentInstallParams
    ) -> SessionBackendResult[AgentsListResponse]:
        return await self._set_agent_installed(params, installed=False)

    async def _set_agent_installed(
        self, params: AgentInstallParams, *, installed: bool
    ) -> SessionBackendResult[AgentsListResponse]:
        self._require_session(params.session_id)
        self._require_idle()
        await self._await_deferred_setup()
        agents = self._context.agents
        try:
            change = plan_installed_agents_change(
                self._context.config_orchestrator,
                agents,
                params.agent_name,
                installed=installed,
            )
        except AgentInstallError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        previous_active = agents.active_profile.name
        was_installed: bool | None = None

        def toggle(names: list[str]) -> list[str]:
            nonlocal was_installed
            was_installed = params.agent_name in names
            return with_agent_installed(names, params.agent_name, installed=installed)

        # The profile switch, the write and the replacement pin land or unwind as
        # one: a write left on disk under a pin that still names the uninstalled
        # agent is exactly the resume this path exists to prevent.
        rollback_write = False
        try:
            if change.switch_to is not None:
                await agents.aswitch_profile(change.switch_to)
                await self._apply_agent_derivation()
            # write_config can raise after the patch reached the layer file, so the
            # write counts as landed from here until it comes back rejected.
            rollback_write = True
            write = await self._write_installed_agents(toggle)
            if write.response.rejected or write.response.failures:
                rollback_write = False
                raise SessionBackendError(
                    ProtocolErrorCode.INVALID_PARAMS,
                    "; ".join(write.response.failures) or "Configuration edit rejected",
                )
            verify_installed_agents_change(
                self._context.config_orchestrator,
                agents,
                params.agent_name,
                installed=installed,
            )
            if change.switch_to is not None:
                await self._persist_pin(SessionPin.AGENT_NAME, change.switch_to)
        except Exception as exc:
            # Undo the write first: restoring the profile needs the agent that the
            # write removed to be selectable again.
            if rollback_write and was_installed is not None:
                await self._rollback_installed_agents(
                    params.agent_name, installed=was_installed
                )
            if change.switch_to is not None:
                await self._restore_profile(previous_active)
            if isinstance(exc, SessionBackendError):
                raise
            if isinstance(exc, AgentInstallError | ValueError):
                raise SessionBackendError(
                    ProtocolErrorCode.INVALID_PARAMS, str(exc)
                ) from exc
            verb = "install" if installed else "uninstall"
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR,
                f"Failed to {verb} agent '{params.agent_name}': {exc}",
            ) from exc
        runtime = write.response.runtime
        return SessionBackendResult(
            response=AgentsListResponse(
                active=runtime.active_agent, agents=runtime.agents
            ),
            runtime_updated=True,
        )

    async def _write_installed_agents(
        self, mutate: Callable[[list[str]], list[str]]
    ) -> SessionBackendResult[ConfigWriteResponse]:
        # Applied to the file's own list: another session may have changed it.
        self._require_idle()
        orchestrator = self._context.config_orchestrator
        try:
            mutation = await orchestrator.mutate_field(
                "/installed_agents",
                mutate,
                reason="app-server agents install",
                default=[],
                target_layer=orchestrator.writable_layer_name,
                preflight=self._preflight_config,
            )
        except (ConfigPatchValidationError, ValueError) as exc:
            logger.warning("Rejected installed_agents write: %s", exc)
            return SessionBackendResult(
                response=ConfigWriteResponse(
                    runtime=self._runtime, rejected=True, reason=str(exc)
                )
            )
        await self._apply_written_config()
        if mutation.failures:
            return SessionBackendResult(
                response=ConfigWriteResponse(
                    runtime=self._runtime,
                    failures=[str(failure) for failure in mutation.failures],
                )
            )
        return SessionBackendResult(
            response=ConfigWriteResponse(
                runtime=self._runtime, status=self._mutation_status()
            )
        )

    async def _rollback_installed_agents(
        self, agent_name: str, *, installed: bool
    ) -> None:
        try:
            write = await self._write_installed_agents(
                lambda names: with_agent_installed(
                    names, agent_name, installed=installed
                )
            )
        except Exception:
            logger.exception(
                "Failed to roll back installed_agents after a failed agents change"
            )
            return
        if write.response.rejected or write.response.failures:
            logger.error(
                "Rolling back installed_agents was rejected: %s",
                "; ".join(write.response.failures) or "Configuration edit rejected",
            )

    async def update_settings(
        self, params: SessionSettingsUpdateParams
    ) -> SessionBackendResult[EmptyResponse]:
        self._require_session(params.session_id)
        self._require_idle()
        self._settings = replace(
            self._settings,
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
        if not _is_turn_independent_write(params.ops):
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
        a turn reads keeps ``config/write``'s idle-only contract.
        """
        self._require_session(params.session_id)
        if params.model_alias:
            # A catalog alias picks its model, which the session then records.
            params = params.model_copy(
                update={
                    "model_alias": self._context.config_orchestrator.config.resolve_model_name(
                        params.model_alias
                    )
                }
            )
        # Held across the write, so a setup that finishes meanwhile replays this
        # pick rather than leaving it queued behind a replay that already ran.
        async with self._model_pick_replay:
            if self._setup_in_progress():
                # A checkout can take minutes, so the pick does not wait for it:
                # it is checked against the configuration the session has now,
                # and applied again to the one setup brings, which replaces
                # overrides. A pick that also becomes the user's default writes
                # it then, to the worktree's layers, as it would have by
                # waiting, and like that write it lets a layer above the session
                # keep its own model or level.
                written = await self._write_session_model_config(
                    params, reject_pinned=params.session_only
                )
                if not written.response.failures:
                    self._model_picks_after_setup.append(params)
                return written
        await self._await_deferred_setup()
        if params.session_only:
            return await self._write_session_model_config(params)
        return await self._write_default_model_config(params)

    async def _write_default_model_config(
        self, params: ModelConfigWriteParams
    ) -> SessionBackendResult[ConfigWriteResponse]:
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

    async def _write_session_model_config(
        self, params: ModelConfigWriteParams, *, reject_pinned: bool = True
    ) -> SessionBackendResult[ConfigWriteResponse]:
        """Only the session override layer takes the pick; turn start pins it."""
        self._require_no_session_rewrite()
        orchestrator = self._context.config_orchestrator
        model_alias = params.model_alias
        self._require_known_model(model_alias)
        failures: list[BaseException] = []
        if model_alias:
            failures = await set_session_active_model_override(
                orchestrator,
                model_alias,
                reason="session model pick",
                preflight=self._preflight_config,
            )
        if not failures and params.reasoning_effort is not None:
            failures = await set_session_reasoning_effort_override(
                orchestrator, params.reasoning_effort, reason="session thinking pick"
            )
        await self._apply_written_config()
        if failures:
            return SessionBackendResult(
                response=ConfigWriteResponse(
                    runtime=self._runtime,
                    failures=[str(failure) for failure in failures],
                )
            )
        accepted = SessionBackendResult(
            response=ConfigWriteResponse(
                runtime=self._runtime, status=self._mutation_status()
            )
        )
        if not reject_pinned:
            return accepted
        # A layer above the session (an agent profile, an admin) can pin either
        # pick, and the write then lands without taking effect.
        active = orchestrator.config.get_active_model()
        if model_alias and active.alias != model_alias:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                f"This configuration runs {active.alias}, so {model_alias} "
                "cannot be picked for one session",
            )
        if (
            params.reasoning_effort is not None
            and active.thinking != params.reasoning_effort
        ):
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                f"This configuration fixes the thinking level of {active.alias} "
                f"at {active.thinking}, so {params.reasoning_effort} cannot be "
                "picked for one session",
            )
        return accepted

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
        operations = config_write_ops_to_patches(
            orchestrator.config,
            ops,
            sparse_override_model_aliases=(
                await orchestrator.sparse_override_model_aliases()
            ),
        )

        async def preflight(candidate: VibeConfigSchema) -> None:
            # What lands on disk is read without the session's own layer, which
            # would mask it. The session's own layer is checked by
            # ``_preflight_config`` once the session runs the model it touches.
            if any(op.target_layer != OverridesLayer.NAME for op in ops):
                without = (OverridesLayer.NAME,)
                _check_saved_models(
                    await orchestrator.preview_patch([], without=without),
                    await orchestrator.preview_patch(operations, without=without),
                )
            await self._preflight_config(candidate)

        try:
            failures = await orchestrator.apply_patch(
                operations, reason=params.reason, preflight=preflight
            )
        except ModelFactsError as exc:
            # A bare rejection reads as a malformed edit; this one is well formed
            # but refused for the model, and the reason says what to pick instead.
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        except (ConfigPatchValidationError, ValueError) as exc:
            # The caller shows the reason: say a lean agent's shell limits
            # clash with config.toml, the user must know what to change.
            logger.warning("Rejected config write %r: %s", params.reason, exc)
            return SessionBackendResult(
                response=ConfigWriteResponse(
                    runtime=self._runtime, rejected=True, reason=str(exc)
                )
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
        self._telemetry.send_config_updated(
            fields=sorted({_config_write_field(op.path) for op in params.ops}),
            target_layers=sorted({op.target_layer or "default" for op in params.ops}),
        )
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
        context = self._context
        # The files first: the org config is checked against them, and a local
        # fix is what lets a policy refused before load now.
        async with context.admin_config_lock:
            try:
                await context.config_orchestrator.reload(
                    preflight=self._preflight_config
                )
            except InvalidConfigError as exc:
                # A config the running agent refuses, say shell timeouts it
                # would replace, is the user's to fix: the session keeps the
                # configuration it had, and the client shows why.
                raise SessionBackendError.configuration(exc) from exc
        # The files may have moved anything, MCP and connectors included; a
        # turn started from here converges them at its own boundary.
        self._deferred.park()
        self._integrations_due = True
        await self._follow_current_org_key()
        await asyncio.gather(
            self._refetch_connector_catalog(), self._refetch_model_catalog()
        )
        if (follower := self._org_config) is not None:
            # Fetched for the whole process: every other session loads a
            # changed answer at its own next boundary, this one here.
            await follower.refresh(self._refetch_org_config)
            # A turn may have started during the fetch.
            self._require_idle()
            try:
                await self._load_remote_values(strict=True)
            except SessionBackendError:
                # The files still land; the caller hears why the policy did not.
                await self._converge(strict=True)
                raise
        else:
            await self._load_model_catalog(reserved_turn=False, strict=True)
        await self._converge(strict=True)
        return SessionBackendResult(
            response=ConfigMutationResponse(runtime=self._runtime)
        )

    def _follow_org_config(self) -> None:
        """Load the org config the process refreshes at this session's turn
        boundaries.
        """
        context = self._context
        if self._closed or self._org_config is not None:
            return
        key = context.org_config_key.key
        self._org_config_key = key
        loaded = self._org_config_running
        if key is not None:
            source: FollowedValue[str] = context.org_config_service.followed(key)
        elif loaded:
            # No credential names an org any more: the policy held stops
            # applying, in every session on this context.
            source = _NoOrgConfig()
        else:
            return
        self._org_config = RemoteFollower(source, loaded=loaded)

    async def _follow_current_org_key(self) -> None:
        """Follow the org config of the credential and endpoint the config
        names now: a login or tenant reconciliation can move them mid-session.
        Recorded on the context, so a /clear or rewind follows the same one.
        """
        if self._closed:
            return
        context = self._context
        candidate = context.config_orchestrator.copy()
        if (layer := admin_layer(candidate)) is not None:
            # The key is read without the org config, as at session build.
            layer.restore({})
            await candidate.reload_layer(layer.name)
        credentials = context.platform_credentials
        key = OrgConfigService.key_for(candidate, credentials)
        # Signed in with a user token, tenant not known yet: the org config is
        # unknown, not gone. Keep following the one held rather than dropping it.
        if key is not None or not oidc_tenant_unresolved(candidate.config, credentials):
            context.org_config_key.key = key
        self._follow_context_org_key()

    def _adopt_resolved_oidc_org_key(self) -> None:
        """Follow the org config of a user token's tenant resolved since the build.

        A session that started before its tenant was known has no key; the
        tenant, once discovered, is stored with the session, so this boundary
        can follow its org config without waiting for ``/reload``. No org
        config applies under a missing key, so the live config gives the key.
        """
        slot = self._context.org_config_key
        if slot.key is not None:
            return
        slot.key = OrgConfigService.key_for(
            self._context.config_orchestrator, self._context.platform_credentials
        )

    def _follow_context_org_key(self) -> None:
        """Re-follow if a /reload in another session on this context moved
        the key.
        """
        if self._org_config_key == self._context.org_config_key.key:
            return
        self._org_config = None
        self._follow_org_config()

    async def _load_remote_values(
        self, *, reserved_turn: bool = False, strict: bool = False
    ) -> None:
        """Run the session under the remote values held now: the org config
        through ``_commit_org_config``, then the model catalog through
        ``_commit_model_catalog``; a changed connector catalog is left to
        ``_converge_integrations``, with the selection the org config narrows.

        Between turns only, so the turn that ran keeps the config it started
        with; a reserved turn has not started, so ``reserved_turn`` runs before
        it does. ``strict`` raises what the session cannot run under, and a
        turn that took over.
        """
        await self._load_org_config(reserved_turn=reserved_turn, strict=strict)
        await self._load_model_catalog(reserved_turn=reserved_turn, strict=strict)
        # Once the first load settled: a login or a late OIDC tenant may have
        # named an account since.
        if self._connector_resolve_task is None or self._connector_resolve_task.done():
            await self._follow_connector_catalog()
        self._note_connector_catalog()

    async def _load_org_config(self, *, reserved_turn: bool, strict: bool) -> None:
        self._adopt_resolved_oidc_org_key()
        self._follow_context_org_key()
        follower = self._org_config
        if follower is None or self._closed:
            return
        follower.check()
        if not follower.due:
            return
        report = self._context.org_config_report
        # Its install can switch the mode: one switch at a time per session.
        async with self._agent_switching, self._context.admin_config_lock:
            # Another boundary may have run it while this one waited.
            toml_text = follower.take()
            if toml_text is None:
                return
            if toml_text in report.refused and not strict and not follower.forced:
                # Refused by another session on this context, which told the
                # user. Left due: a /reload there that runs it lands here too.
                return
            if not reserved_turn and self._session.active_turn_id is not None:
                if strict:
                    _raise_turn_took_over()
                return
            await self._commit_org_config(
                follower, toml_text, reserved_turn=reserved_turn, strict=strict
            )

    async def _follow_connector_catalog(self) -> None:
        """Follow the catalog of the account the config names, once the
        session's first load settled what it runs; again when a login,
        ``/reload`` or worktree config moved the account.
        """
        service = self._context.connector_catalog_service
        if self._closed or service is None:
            return
        # Serialised: the end of a load, /reload and a worktree move can follow
        # at once, and each would otherwise hold the account without letting go.
        async with self._connector_following:
            orchestrator = self._context.config_orchestrator
            if self._connector_follower is not None and await service.follows(
                orchestrator, self._connector_follow
            ):
                return
            self._unfollow_connector_catalog()
            follow = await service.follow(orchestrator)
            if follow is None:
                return
            if self._closed:
                follow.release()
                return
            self._connector_follow = follow
            self._connector_follower = RemoteFollower(
                follow, loaded=self.accepted_connector_catalog()
            )

    def _unfollow_connector_catalog(self) -> None:
        if self._connector_follow is not None:
            self._connector_follow.release()
        self._connector_follow = None
        self._connector_follower = None

    def _note_connector_catalog(self) -> None:
        """Leave a changed catalog for ``_converge_integrations``: one the user
        connected, disconnected or that was revoked.
        """
        follower = self._connector_follower
        if follower is None or self._closed:
            return
        follower.check()
        if follower.due:
            self._integrations_due = True

    def _follow_model_catalog(self) -> None:
        """Follow the catalog of the account and endpoint the config names now:
        a login, an org config or ``/reload`` can move them mid-session.
        """
        context = self._context
        config = context.config_orchestrator.config
        key = ModelCatalogKey.current(config, context.platform_credentials)
        if key is None:
            self._model_catalog = None
            self._model_catalog_key = None
            return
        if self._model_catalog is not None and key == self._model_catalog_key:
            return
        self._model_catalog_key = key
        self._model_catalog = RemoteFollower(
            context.model_catalog_service.followed(key),
            loaded=self._model_catalog_running,
        )

    async def _refetch_model_catalog(self) -> None:
        self._follow_model_catalog()
        follower = self._model_catalog
        key = self._model_catalog_key
        if follower is None or key is None:
            return
        await follower.refresh(
            partial(self._context.model_catalog_service.refresh, key)
        )

    def _due_model_catalog(self) -> RemoteFollower[ModelCatalogResponse] | None:
        if self._closed:
            return None
        self._follow_model_catalog()
        follower = self._model_catalog
        if follower is None:
            return None
        follower.check()
        refused_for = self._model_catalog_refused_for
        if (
            refused_for is not None
            and refused_for != self._context.config_orchestrator.config.active_model
        ):
            self._model_catalog_refused_for = None
            follower.force()
        return follower if follower.due else None

    async def _load_model_catalog(self, *, reserved_turn: bool, strict: bool) -> bool:
        """Load the catalog held, if it changed, between turns only: the
        turn that ran keeps the config it started with. Answers whether the
        session runs a new one.
        """
        if self._due_model_catalog() is None:
            return False
        # Its install rebuilds the config: one switch at a time per session.
        async with self._agent_switching, self._context.admin_config_lock:
            # Taken again: another boundary may have run it, or a worktree move
            # changed what the session follows, while this one waited.
            follower = self._due_model_catalog()
            catalog = follower.take() if follower is not None else None
            if follower is None or catalog is None:
                return False
            if catalog == self._model_catalog_running and not follower.forced:
                follower.mark_loaded(catalog)
                return False
            if not reserved_turn and self._session.active_turn_id is not None:
                if strict:
                    _raise_turn_took_over()
                return False
            return await self._commit_model_catalog(
                follower, catalog, reserved_turn=reserved_turn, strict=strict
            )

    async def _commit_model_catalog(
        self,
        follower: RemoteFollower[ModelCatalogResponse],
        catalog: ModelCatalogResponse,
        *,
        reserved_turn: bool,
        strict: bool,
    ) -> bool:
        """Run the session under ``catalog``; under the lock.

        Loaded into a copy of the session's config, and what that derives is
        pushed to Core; only then is the catalog installed on the shared
        config. A session pinned to a model keeps it: a catalog that would move
        the pin is refused for this session. Displaced by a turn, nothing was
        installed, and it stays due.
        """
        context = self._context
        pinned = context.config_orchestrator.config.active_model
        retired: list[str] = []

        async def build() -> UnifiedRuntimeDerivation:
            orchestrator = context.config_orchestrator.copy()
            layer = _catalog_layer_of(orchestrator)
            if layer is None:
                raise _CandidateRefusedError("no model catalog layer", shared=False)
            layer.set_catalog(catalog)
            try:
                await orchestrator.reload_layer(layer.name)
            except Exception as exc:
                raise _CandidateRefusedError(str(exc), shared=True) from exc
            # A pin the catalog renamed resolves through its aliases; one it
            # retired falls back to the default.
            if pinned and not orchestrator.config.active_model:
                retired.append(pinned)
                raise _CandidateRefusedError(
                    f"it retires the pinned model '{pinned}'", shared=False
                )
            agents = context.agents.under(orchestrator)
            return await self._derive_candidate(
                UnifiedConfigCandidate(orchestrator, agents)
            )

        async def install() -> bool:
            return await self._install_model_catalog(catalog)

        push = (
            partial(self._push_configuration, allow_reserved_turn=True)
            if reserved_turn
            else None
        )
        try:
            running = await self._deferred.commit(
                build, install=install, push=push, check_turn=not reserved_turn
            )
        except (HarnessConfigurationRejectedError, _CandidateRefusedError) as exc:
            # Not tried again until the catalog changes: the session keeps the
            # models it runs.
            follower.mark_loaded(catalog)
            logger.info(
                "Model catalog not applied to session_id=%s: %s", self.session_id, exc
            )
            if retired and retired[0] == self._model_catalog_refused_for:
                return False
            if retired:
                self._model_catalog_refused_for = retired[0]
                self._session.publish_notice(
                    f"Model '{retired[0]}' is no longer in the model list; this"
                    " session keeps it. Pick another model to use the updated list."
                )
            elif strict:
                self._session.publish_notice(
                    "The refreshed model list could not be applied."
                )
            return False
        if not running:
            if strict:
                _raise_turn_took_over()
            return False
        if self._deferred.parked:
            # Core runs it, but the shared config does not hold it: it stays
            # due, and the next boundary installs and pushes it again.
            return False
        follower.mark_loaded(catalog)
        self._model_catalog_running = catalog
        self._model_catalog_refused_for = None
        if not strict:
            self._runtime_unannounced = True
        return True

    async def _install_model_catalog(self, catalog: ModelCatalogResponse) -> bool:
        """Install on the shared config the catalog Core runs now; a failure
        leaves the change parked, so the next boundary pushes the shared config.
        """
        orchestrator = self._context.config_orchestrator
        layer = _catalog_layer_of(orchestrator)
        if layer is None:
            return False
        try:
            await set_catalog_and_reload(orchestrator, layer, catalog)
        except Exception:
            logger.warning(
                "Failed to install the model catalog Core runs for session_id=%s",
                self.session_id,
                exc_info=True,
            )
            return False
        return True

    async def _commit_org_config(
        self,
        follower: RemoteFollower[str],
        toml_text: str,
        *,
        reserved_turn: bool,
        strict: bool,
    ) -> None:
        """Run the session under ``toml_text``; under the lock.

        The one path an org config reaches a session by. The policy is loaded
        into a copy of the session's config, with the mode it leaves, and what
        that derives is pushed to Core. Only once Core runs it is the copy's
        admin layer and mode installed on the shared config, the value
        recorded as decided, and the user told; MCP servers and connectors then
        follow. Refused, by the preflight or by Core, nothing was installed:
        the value is recorded as decided, so no session on this context tries
        it again until it changes or ``/reload``. Displaced by a turn, nothing
        was installed either, and it stays due.
        """
        context = self._context
        built: list[_OrgConfigCandidate] = []

        async def build() -> UnifiedRuntimeDerivation:
            # Copied under the apply lock, so no write pushed meanwhile is lost.
            orchestrator = context.config_orchestrator.copy()
            loaded = await load_admin_layer(orchestrator, toml_text)
            if not loaded.applied:
                # The policy itself: no session on this context can run it.
                raise _CandidateRefusedError(
                    loaded.error or loaded.outcome.value, shared=True, result=loaded
                )
            agents = context.agents.under(orchestrator)
            left_mode = await _leave_excluded_mode(agents, orchestrator)
            layer = admin_layer(orchestrator)
            built.append(
                _OrgConfigCandidate(
                    result=loaded,
                    admin=layer.snapshot() if layer is not None else {},
                    agents=agents,
                    left_mode=left_mode,
                )
            )
            return await self._derive_candidate(
                UnifiedConfigCandidate(orchestrator, agents)
            )

        async def install() -> bool:
            candidate = built[-1]
            return await self._install_candidate(
                candidate.admin, candidate.agents, mode_left=candidate.left_mode
            )

        push = (
            partial(self._push_configuration, allow_reserved_turn=True)
            if reserved_turn
            else None
        )
        try:
            running = await self._deferred.commit(
                build, install=install, push=push, check_turn=not reserved_turn
            )
        except (HarnessConfigurationRejectedError, _CandidateRefusedError) as exc:
            refused = isinstance(exc, _CandidateRefusedError)
            self._refuse_org_config(
                follower,
                toml_text,
                (exc.result if refused else None)
                or AdminConfigApplyResult(
                    AdminConfigOutcome.APPLY_FAILED, error=str(exc)
                ),
                strict=strict,
                shared=refused and exc.shared,
            )
            return
        if not running:
            if strict:
                _raise_turn_took_over()
            return
        if self._deferred.parked:
            # Core runs it, but the shared config does not hold it: it stays
            # due, and the next boundary installs and pushes it again.
            if strict:
                raise SessionBackendError(
                    ProtocolErrorCode.INTERNAL_ERROR,
                    "Your administrator-managed configuration could not be"
                    " installed; it is tried again when the next turn ends.",
                )
            return
        result = (
            built[-1].result
            if toml_text
            else AdminConfigApplyResult(AdminConfigOutcome.DISABLED)
        )
        left_mode = built[-1].left_mode
        follower.mark_loaded(toml_text)
        self._org_config_running = toml_text
        context.org_config_report.loaded = toml_text
        context.org_config_report.refused.discard(toml_text)
        report_admin_config_outcome(result, telemetry=self._telemetry)
        if left_mode is not None:
            self._publish_notice(
                f"The '{left_mode}' mode is disabled by your organization."
            )
        self._integrations_due = True
        if not strict:
            # Announced once MCP servers and connectors followed it.
            self._runtime_unannounced = True

    async def _derive_candidate(
        self, candidate: UnifiedConfigCandidate
    ) -> UnifiedRuntimeDerivation:
        """What ``candidate`` derives, once this session's context fits it."""
        await self._copy_skills_into_sandbox(candidate.orchestrator)
        preflight = self._context.preflight
        try:
            derivation = (
                await preflight(
                    candidate.orchestrator.config, self._settings, candidate
                )
                if preflight is not None
                else None
            )
        except Exception as exc:
            raise _CandidateRefusedError(str(exc), shared=True) from exc
        if derivation is None:
            raise _CandidateRefusedError(
                "no preflight to derive the policy with", shared=False
            )
        try:
            await self._session.check_config_fits(derivation.session_config)
        except HarnessConfigurationRejectedError as exc:
            raise _CandidateRefusedError(str(exc), shared=False) from exc
        return derivation

    async def _install_candidate(
        self, admin: dict[str, Any], agents: AgentManager, *, mode_left: str | None
    ) -> bool:
        """Install on the shared config what Core runs now; nothing here may
        refuse it. Answers whether it did: a failure leaves the change parked,
        so the next boundary pushes the shared config.
        """
        orchestrator = self._context.config_orchestrator
        layer = admin_layer(orchestrator)
        previous = layer.snapshot() if layer is not None else None
        try:
            if layer is not None:
                layer.restore(admin)
                await orchestrator.reload_layer(layer.name)
            if mode_left is not None:
                await self._context.agents.aswitch_profile(agents.active_profile.name)
        except BaseException as exc:
            if layer is not None and previous is not None:
                layer.restore(previous)
            # Cancelled: the layer is back, and the caller awaits the
            # cancellation, as in ``load_admin_layer``.
            if not isinstance(exc, Exception):
                raise
            logger.warning(
                "Failed to install the org config Core runs for session_id=%s",
                self.session_id,
                exc_info=True,
            )
            if layer is not None:
                with contextlib.suppress(Exception):
                    await orchestrator.reload_layer(layer.name)
            return False
        return True

    def _refuse_org_config(
        self,
        follower: RemoteFollower[str],
        toml_text: str,
        result: AdminConfigApplyResult,
        *,
        strict: bool,
        shared: bool,
    ) -> None:
        """Record an org config the session cannot run under, and report it;
        raise if strict. ``shared`` when no session on this context can run it
        (the policy itself is at fault), not only this one (its context does
        not fit).
        """
        follower.mark_loaded(toml_text)
        if shared:
            self._context.org_config_report.refused.add(toml_text)
        report_admin_config_outcome(result, telemetry=self._telemetry)
        if strict:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                f"Your administrator-managed configuration could not be applied:"
                f" {result.error or result.outcome.value}",
            )
        self._show_admin_config_failure(result)

    async def _converge(self, *, strict: bool = False) -> None:
        """Bring Core, MCP servers and connectors to the session's config.

        The one place a changed config reaches what the session runs: a parked
        derivation is pushed, then the integrations it moved follow it. Core
        refusing the derivation drops it, as for any parked write, and leaves
        the integrations where they are. ``strict`` raises what fails instead
        of keeping it for the next boundary.
        """
        try:
            settled = await self._deferred.settle()
        except HarnessConfigurationRejectedError as exc:
            if strict:
                raise SessionBackendError(
                    ProtocolErrorCode.INVALID_PARAMS, str(exc)
                ) from exc
            await self._drop_refused_configuration(exc)
            return
        if settled:
            self._runtime_unannounced = True
        if self._deferred.parked:
            if strict:
                _raise_turn_took_over()
            # A turn started: its boundary converges the rest.
            await self._announce_unannounced_runtime()
            return
        await self._converge_integrations(strict=strict)
        if strict:
            # A strict caller answers with the runtime itself.
            self._runtime_unannounced = False
            return
        await self._announce_unannounced_runtime()

    async def _converge_integrations(self, *, strict: bool = False) -> None:
        """Bring MCP servers and connectors to the config, if a change moved
        them. A failure leaves them due for the next boundary; ``strict``
        raises it.
        """
        if not self._integrations_due:
            return
        try:
            service = self._context.mcp_catalog_service
            if service is not None and self._mcp_running is not None:
                # Projects the MCP servers onto ``_runtime``.
                await service.converge(self, self._mcp_running)
            await self._converge_connectors()
        except Exception:
            if strict:
                raise
            logger.warning(
                "Failed to converge integrations for session_id=%s",
                self.session_id,
                exc_info=True,
            )
            return
        self._integrations_due = False
        self._runtime_unannounced = True

    async def _refetch_connector_catalog(self) -> None:
        """Fetch the catalog now for the whole process (``/reload``), following
        the account the reloaded config names. A failure keeps the catalog the
        session runs; ``/reload`` is not refused for it.
        """
        await self._follow_connector_catalog()
        follower = self._connector_follower
        service = self._context.connector_catalog_service
        if follower is None or service is None:
            return
        try:
            await follower.refresh(
                partial(
                    service.resolve_catalog,
                    self._context.config_orchestrator,
                    force_refresh=True,
                )
            )
        except ConnectorCatalogError as exc:
            logger.warning(
                "Failed to refresh the connector catalog on reload: %s", str(exc)
            )
            self._warn_integrations_unavailable([
                f"Couldn't refresh your connectors: {str(exc).rstrip('.')}. "
                "Using the ones loaded earlier."
            ])
            return
        self._integrations_due = True

    async def _refetch_org_config(self) -> None:
        context = self._context
        if (key := context.org_config_key.key) is None:
            return
        fetched = await context.org_config_service.refresh(key)
        if not isinstance(fetched, str):
            self._show_admin_config_failure(fetched)
            report_admin_config_outcome(fetched, telemetry=self._telemetry)

    async def _converge_connectors(self) -> None:
        """Run the catalog the process holds, if it changed, with the selection
        the config resolves now (the org config may have narrowed it).

        Serialised, and the catalog taken once the lock is held: two boundaries
        can settle at once (a turn ending while the next starts), and the
        second then finds the catalog already running instead of pushing it
        to Core again.
        """
        service = self._context.connector_catalog_service
        if service is None:
            return
        async with self._connectors_converging:
            follower = self._connector_follower
            changed = follower.take() if follower is not None else None
            catalog = changed or self._connector_catalog
            if not catalog.revision:
                return
            selection = service.resolve_selection(
                self._context.config_orchestrator, catalog
            )
            if (
                catalog == self._connector_catalog
                and selection.selection_revision
                == self._connector_selection.selection_revision
            ):
                if changed is not None and follower is not None:
                    follower.mark_loaded(changed)
                return
            try:
                state = await self._apply_connectors(
                    catalog, selection, push_to_core=True
                )
            except SessionBackendError as exc:
                # A turn taking over leaves it due; a refusal of the catalog
                # itself is not retried until a newer one, or /reload.
                if exc.code is not ProtocolErrorCode.CONFLICT and changed:
                    if follower is not None:
                        follower.mark_loaded(changed)
                    logger.warning(
                        "Session %s refused the connector catalog: %s",
                        self.session_id,
                        exc,
                    )
                    return
                raise
            self._connectors_unpublished = False
            self._update_connector_projection(state)

    def _close_org_config(self) -> None:
        self._org_config = None
        self._model_catalog = None
        self._model_catalog_key = None

    def report_org_config(self, report: OrgConfigReport | None) -> None:
        """Report what the session's build did with the org config, once the
        session is open.

        The notices need a session to publish on, and the telemetry
        event a session to attribute it to; neither exists while the start
        builds the session from the fetched config.
        """
        if report is None:
            return
        if report.excluded_agent is not None:
            self._publish_notice(
                f"The '{report.excluded_agent}' mode is disabled by your organization."
            )
        self._report_org_config_outcome(report.outcome)

    def _report_org_config_outcome(
        self, outcome: AdminConfigApplyResult | None
    ) -> None:
        # A --worktree start rebuilds in the worktree from the cached copy the
        # start just fell back from: a failure already reported, even when the
        # error text differs (another folder, or the fetched copy's error).
        if outcome is None or outcome.outcome in self._reported_org_config:
            return
        self._reported_org_config.add(outcome.outcome)
        self._show_admin_config_failure(outcome)
        report_admin_config_outcome(
            outcome, telemetry=self._telemetry, quiet_fetch_failures=True
        )

    def _show_admin_config_failure(self, result: AdminConfigApplyResult) -> None:
        if result.outcome not in {
            AdminConfigOutcome.PARSE_FAILED,
            AdminConfigOutcome.APPLY_FAILED,
        }:
            return
        error = result.error or "Unknown error"
        self._publish_notice(
            f"Your administrator-managed configuration could not be applied: {error}",
            level="error",
        )

    async def _config_read_response(self) -> ConfigReadResponse:
        return await _config_read_response(
            self._context.config_orchestrator,
            self._context.harness_files,
            sandbox=self._context.sandbox_skills,
        )

    def _resolved_registry_versions(self) -> RegistryVersions:
        return resolved_registry_versions(
            lambda: self._context.config_orchestrator.config,
            harness_files=self._context.harness_files,
            sandbox=self._context.sandbox_skills,
        )

    async def _config_fields_response(self) -> ConfigFieldsReadResponse:
        orchestrator = self._context.config_orchestrator
        layer_values = await collect_layer_values(
            orchestrator.layers, orchestrator.config
        )
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
        relocation moves the session as it stands. Both bar even a pick a running
        turn would only park: there is no later boundary to land it on that the
        rewrite has not already read past.
        """
        if self._defer_event_flush:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "Context compaction is already running"
            )
        if self._relocation_active:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "Session relocation is already running"
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

    @contextlib.asynccontextmanager
    async def relocation_reservation(self) -> AsyncIterator[None]:
        """Reserve the idle Session and serialize configuration through a move."""
        async with self._agent_switching, self._deferred.exclusive():
            self._require_idle()
            self._relocation_active = True
            try:
                await _harness_call(self._session._guard_relocation())
                yield
            finally:
                self._relocation_active = False

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

    async def _apply_derivation(self, *, allow_reserved_turn: bool = False) -> bool:
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
            return await self._deferred.apply_through(
                partial(self._push_configuration, allow_reserved_turn=True)
            )
        return await self._deferred.apply()

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
                self._emit_worktree_created_telemetry(self._deferred_setup_result)
            except asyncio.CancelledError:
                self._setup_abandoned = True
                raise
            except Exception as exc:
                self._setup_abandoned = True
                logger.warning("Deferred session setup failed", exc_info=exc)
                # A client waiting for the worktree before showing its UI (the
                # CLI's pre-TUI gate) has no turn to carry the failure, so it
                # rides a history entry like every other worktree effect.
                # The session it describes is abandoned either way.
                with contextlib.suppress(Exception):
                    await self._emit_worktree_failure(exc)
            finally:
                ready.set()
            discover = self._pr_discovery_after_setup and not self._setup_abandoned
            self._pr_discovery_after_setup = False
            if discover:
                self.schedule_pr_discovery()

        self._deferred_setup_task = asyncio.create_task(run())

    async def _await_deferred_setup(self) -> WorktreeResolution | None:
        resolution = await self._await_deferred_workspace()
        await self._apply_model_pick_after_setup()
        return resolution

    async def _await_deferred_workspace(self) -> WorktreeResolution | None:
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

    async def _apply_model_pick_after_setup(self) -> None:
        async with self._model_pick_replay:
            picks = self._model_picks_after_setup
            self._model_picks_after_setup = []
            for pick in picks:
                written = await (
                    self._write_session_model_config(pick)
                    if pick.session_only
                    else self._write_default_model_config(pick)
                )
                if written.response.rejected or written.response.failures:
                    raise SessionBackendError(
                        ProtocolErrorCode.INVALID_PARAMS,
                        "; ".join(written.response.failures)
                        or "The configuration rejected the model choice",
                    )

    def _setup_in_progress(self) -> bool:
        return self._turns_deferred is not None and not self._turns_deferred.is_set()

    def _require_known_model(self, model_alias: str | None) -> None:
        models = self._context.config_orchestrator.config.models
        if model_alias and model_alias not in models:
            available = ", ".join(sorted(models))
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                f"Unknown model: {model_alias}. Available: {available}",
            )

    @contextlib.asynccontextmanager
    async def _fork_boundary(self) -> AsyncIterator[None]:
        """Keep source Turns blocked until its checkout and history are copied."""
        async with self._session._fork_boundary():
            yield

    async def _cancel_deferred_setup_task(self) -> None:
        setup = self._deferred_setup_task
        self._deferred_setup_task = None
        if setup is None:
            return
        setup.cancel()
        with contextlib.suppress(BaseException):
            await setup

    async def adopt_context(self, context: UnifiedSessionContext) -> None:
        async with self._agent_switching:
            await self._adopt_context(context)

    async def _adopt_context(self, context: UnifiedSessionContext) -> None:
        previous = self._context
        # The Harness keeps the routes it already accepted, and nothing re-reads
        # them here, so the incoming resolver has to inherit their published
        # names or every routed provided tool would fall back to its route and
        # lose the permission configured against it.
        context.provided_names.adopt(previous.provided_names)
        context.completion_budget.adopt(previous.completion_budget)
        # Same reason: the live executor closes over the lists the previous holder
        # handed out, so a rewind has to reach those and not the incoming blank ones.
        context.vibe_tools.adopt(previous.vibe_tools)
        # The incoming context is built from the launch options, so a profile
        # switched while the worktree was prepared lives only on the outgoing one.
        active = previous.agents.active_profile.name
        mode_notice = None
        if context.agents.active_profile.name != active:
            # The incoming context can lack the mode: a cached org config that
            # excludes it, or a custom agent this checkout does not have. The
            # session goes on in the mode the incoming context started.
            try:
                await context.agents.aswitch_profile(active)
            except InvalidConfigError as exc:
                # The mode is there, but the incoming config clashes with it, say
                # with lean's shell timeouts: the user's to fix, so the move is
                # refused as a session start in it is, not run in another mode.
                raise SessionBackendError.configuration(exc) from exc
            except ValueError:
                reason = (
                    "is disabled by your organization"
                    if context.agents.org_excludes(active)
                    else "isn't available in this folder"
                )
                mode_notice = f"The '{active}' mode {reason}."
        # Carried with no await before the swap: an eval landing earlier is
        # carried, one landing later reaches the new context.
        _carry_experiments(previous, context)
        # An eval still in flight lands on the new context, so a /whoami
        # reconcile there waits for it, and reaches the manager it lands on.
        if self._experiments_task is not None and not self._experiments_task.done():
            context.experiments_init_gate.track(self._experiments_task)
        previous_account = self._account_controller
        self._account_controller = AccountController(
            _UnifiedAccountHost(context),
            context.account_gateway,
            credentials=context.platform_credentials,
        )
        # Followed again below, from what the new context was built under.
        self._close_org_config()
        self._context = context
        try:
            await _run_carried_variants(context, context)
            applied = await self._apply_derivation(allow_reserved_turn=True)
            if not applied:
                raise SessionBackendError(
                    ProtocolErrorCode.CONFLICT,
                    "The worktree configuration could not be applied before setup completed",
                )
        except BaseException:
            self._context = previous
            self._account_controller = previous_account
            self._follow_org_config()
            raise
        # The reserved turn's Core is built from this context, under the org
        # config the process held when it was built: follow from there, and
        # bring MCP servers and connectors to it before the turn is promoted.
        self._follow_org_config()
        await self._follow_connector_catalog()
        self._integrations_due = True
        if mode_notice is not None:
            self._publish_notice(mode_notice)
        # Only the outcome: the session start already showed the --agent one.
        report = context.org_config_report.take()
        self._report_org_config_outcome(None if report is None else report.outcome)

    async def adopt_relocated_context(
        self,
        context: UnifiedSessionContext,
        derivation: UnifiedRuntimeDerivation,
        *,
        git_trust: TrustedFoldersManager,
    ) -> None:
        """Adopt context already committed through the Runtime relocation boundary."""
        previous_context = self._context
        # Repository I/O completed during preparation. After Runtime commits,
        # publish context, derivation and source trust before the first await.
        context.provided_names.adopt(previous_context.provided_names)
        _carry_experiments(previous_context, context)
        if self._experiments_task is not None and not self._experiments_task.done():
            context.experiments_init_gate.track(self._experiments_task)
        context.vibe_tools.adopt(previous_context.vibe_tools)
        account_controller = AccountController(
            _UnifiedAccountHost(context),
            context.account_gateway,
            credentials=context.platform_credentials,
        )
        identity_controller = IdentityController(
            _UnifiedIdentityHost(context),
            context.identity_gateway,
            context.platform_credentials,
        )
        skills_controller = SkillsController(
            _UnifiedSkillsHost(
                context,
                runtime=lambda: self._runtime,
                require_idle=self._require_idle,
                require_session=self._require_session,
                refresh=self._refresh_after_skill_change,
            )
        )
        shell_controller = ShellController(derivation.adapter_config.workspace.cwd)
        previous_shell_controller = self._host_shell.controller
        # Followed again below, from what the new context was built under.
        self._close_org_config()
        self._context = context
        self._worktree_trust_store = git_trust
        report = context.org_config_report.take()
        self._report_org_config_outcome(None if report is None else report.outcome)
        self._plugins = context.plugins
        self._plugin_provider = context.plugin_provider
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
        self._account_controller = account_controller
        self._identity_controller = identity_controller
        self._skills_controller = skills_controller
        # Only the shell's folder changes: the live event stream drains this
        # queue, so a new one would leave every later flush waiting on it.
        self._host_shell.controller = shell_controller
        self._adopt_derivation(derivation)
        self._deferred.mark_applied()
        for pending in (
            self._cancel_experiments_task(),
            self._cancel_connector_resolve_task(),
            self._cancel_mcp_discovery_task(),
        ):
            if pending is not None:
                with contextlib.suppress(BaseException):
                    await pending
        # Follows again from what the new context was built under.
        self._follow_org_config()
        await self._follow_connector_catalog()
        with contextlib.suppress(Exception):
            await previous_shell_controller.close()
        with contextlib.suppress(Exception):
            await previous_context.experiment_manager.aclose()

    def replace_relocation_trust(self, checkout: Path) -> None:
        """Retain the new relocation grant and release the prior moved-to checkout."""
        previous = self._relocation_trust_root
        self._relocation_trust_root = checkout
        if previous is not None and previous != checkout:
            self._context.harness_files.trust_store.revoke_session_trust(previous)

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

    async def derive_configuration(self) -> UnifiedRuntimeDerivation:
        """A sandboxed session's skills are copied first, so the derivation finds the
        versions it resolves.
        """
        await self._copy_skills_into_sandbox()
        return await asyncio.to_thread(self._context.derive, self._settings)

    async def _copy_skills_into_sandbox(
        self, orchestrator: ConfigOrchestrator[VibeConfigSchema] | None = None
    ) -> None:
        """Under ``orchestrator``'s config when given, as an org-config candidate is
        derived before it is adopted.
        """
        sandbox = self._context.sandbox_skills
        source = orchestrator or self._context.config_orchestrator
        if sandbox is None or not source.config.experimental_enable_registry_skills:
            return
        skills = await asyncio.to_thread(
            host_skills,
            lambda: source.config,
            harness_files=self._context.harness_files,
        )
        await sandbox.install(skills.values())

    async def _push_configuration(
        self, derivation: UnifiedRuntimeDerivation, *, allow_reserved_turn: bool = False
    ) -> None:
        """Push one complete configuration into the Harness Session.

        Instructions travel with every push, though only an unpromoted Core can
        take them: Core receives its prompt at creation and holds it for life.
        Before promotion, the Session must retain the prompt most recently
        requested by the user.

        A reserved turn has not started in Core yet, so it is still between
        turns for configuration purposes. Allowing this push prevents a
        deferred worktree turn from running on the configuration it replaced.
        """
        await self._commands.apply_config(
            derivation.session_config, allow_reserved_turn=allow_reserved_turn
        )

    def _adopt_derivation(self, derivation: UnifiedRuntimeDerivation) -> None:
        """Publish the derivation the session is now running."""
        self._running = derivation
        self._org_config_running = self._context.org_config_report.loaded
        self._model_catalog_running = _catalog_of(self._context.config_orchestrator)
        self._runtime = self._projected_runtime(derivation)
        self._adapter_config = derivation.adapter_config
        self._model = derivation.adapter_config.model
        self._session_telemetry.set_model(self._model)
        self._skills = derivation.skill_payloads
        self._completion_catalogue = derivation.completion_catalogue
        image_input = _image_input(derivation)
        switched_away_from_images = self._image_input and not image_input
        self._image_input = image_input
        if switched_away_from_images and self._announced.is_set():
            self._warn_when_history_images_are_replaced()
        self._bind_completion_sources(derivation)
        self._emit_profile_warnings(derivation)
        self._follow_skill_roots()
        self._registry_sync.follow()

    def _bind_completion_sources(self, derivation: UnifiedRuntimeDerivation) -> None:
        """Point this derivation's request callbacks back at this session.

        Every derivation brings fresh holders, so a re-derivation would otherwise
        lose both request metadata and the session family's cache affinity.
        """
        derivation.completion_affinity.bind(self._completion_affinity_id)
        if self._completion_attribution is not None:
            derivation.completion_attribution.bind(self._completion_attribution)

    def _completion_affinity_id(self) -> str:
        return self._root_session_id or self.session_id

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

        The agent's prompt is the one part that cannot always follow. Core is
        handed its instructions when it is created and holds them for life, so a
        switch before the first turn carries the new agent's prompt and a switch
        after it changes everything except the prompt. ``/clear`` starts a Core
        that takes the current one.
        """
        # Exclusive for the same reason a config write is serialised: this
        # derives and pushes for itself, so an apply that derived before the
        # switch could otherwise land after it and restore the old agent.
        async with self._deferred.exclusive():
            derivation = await self.derive_configuration()
            if self._session.active_turn_id is None:
                await self._commands.apply_config(derivation.session_config)
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
            # Last: a failed journal write must not leave the switch half-applied.
            await self._session.record_live_agent_change(approval_only)

    async def _refresh_after_skill_change(self) -> RuntimeSnapshot:
        await self._context.config_orchestrator.reload(preflight=self._preflight_config)
        await self._apply_derivation()
        return self._runtime

    async def _preflight_config(self, candidate: VibeConfigSchema) -> None:
        """Refuse a candidate configuration before anything persists it.

        Besides the candidate being valid, the live context has to fit the
        model it runs. Core would refuse the push otherwise, after the write
        had already landed in config.toml, and every later session start would
        refuse it again.
        """
        preflight = self._context.preflight
        if preflight is None:
            return
        derivation = await preflight(candidate, self._settings)
        if derivation is None:
            return
        try:
            await self._session.check_config_fits(derivation.session_config)
        except HarnessConfigurationRejectedError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc

    async def _apply_derivation_when_idle(self) -> bool:
        """Push a derivation that originated outside a config mutation.

        Tenant reconciliation can land during a turn, and ``_require_idle``
        exists because the Rust Core reads its settings at turn start: pushing
        between two iterations would swap the provider underneath a running
        turn. Persist always, push only when Core is between turns;
        ``start_turn`` flushes what is pending. Returns whether the live runtime
        moved, which a deferred push has not yet done.
        """
        return await self._deferred.apply_when_idle()

    async def _apply_written_config(self) -> None:
        """Apply what the user just wrote, and report it even when it is parked.

        Clients read the runtime on the response as the catalogue they just
        wrote, so a parked write must still answer with it. A derivation nobody
        asked for stays invisible until it takes effect.
        """
        try:
            await self._apply_derivation_when_idle()
        except HarnessConfigurationRejectedError as exc:
            # The preflight asks Core the same question first; this is Core
            # changing its answer in between. Refused all the same.
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        # Under the lock: derived outside it, a projection can be computed
        # before a settle adopts a newer configuration and assigned after,
        # leaving the session reporting a runtime it is no longer running.
        async with self._deferred.exclusive():
            if not self._deferred.parked:
                return
            # Derived again, deliberately: the config can move again before the
            # boundary, and the later derivation is the one that takes effect.
            # Only the projection moves here; the turn keeps what it bound.
            derivation = await self.derive_configuration()
            self._runtime = self._projected_runtime(derivation)

    async def initialize_experiments(self, launch_context: LaunchContext) -> None:
        """Run the session under the eval its process holds for its account
        and client, fetching it with none held. The session keeps that eval
        for its life: a later refresh only reaches sessions opened after it.
        """
        if self._experiments_settled:
            # Moved to a worktree: the session keeps the eval it opened with.
            # Without one it runs on the cached variants its context was built
            # from, as it did before the move.
            if self._context.experiment_manager.export_state() is not None:
                await self._apply_experiment_variants()
            return
        await self._initialize_experiments(launch_context)
        # Not on cancellation: a session moved mid-eval evaluates again.
        self._experiments_settled = True

    async def _initialize_experiments(self, launch_context: LaunchContext) -> None:
        self._launch_context = launch_context
        context = self._context
        service = context.experiments_service
        config = context.config_orchestrator.config
        credentials = context.platform_credentials
        prompt_id = self._experiment_system_prompt_id()
        try:
            key = (
                await ExperimentsKey.of(
                    config,
                    launch_context,
                    experiment_system_prompt_id=prompt_id,
                    credentials=credentials,
                )
                if experiments_eval_runs(config, credentials)
                else None
            )
            if key is None:
                # No eval to share (opted out, or a CLI OIDC tenant still
                # unknown): only the attributes and plan telemetry segments by,
                # resolved through this context's caches.
                attributes, self._user_plan = await resolve_plan_attributes(
                    config=config,
                    launch_context=launch_context,
                    resolve_identity=context.identity_cache.resolve,
                    resolve_whoami=context.whoami_cache.resolve,
                    harness=ExperimentSurface.UNIFIED,
                    experiment_system_prompt_id=prompt_id,
                    credentials=credentials,
                )
                if attributes is not None:
                    # The session's context now: a worktree move during the
                    # lookup replaced the one this started on.
                    self._context.experiment_manager.set_attributes(attributes)
                return
            try:
                held = await service.evaluate(key)
            except ExperimentsEvalFailedError as exc:
                self._user_plan = exc.user_plan
                if exc.attributes is not None:
                    self._context.experiment_manager.set_attributes(exc.attributes)
                return
            self._user_plan = held.user_plan
            self._context.experiment_manager.hydrate(
                held.response, attributes=held.attributes, source="process"
            )
            await self._apply_experiment_variants()
        except Exception:
            service.failures.mark()
            logger.exception("Failed to initialize experiments")

    def _experiment_system_prompt_id(self) -> str | None:
        # Sessions open with the cached eval's variants already in this layer.
        for layer in self._context.config_orchestrator.layers:
            if isinstance(layer, GrowthbookLayer):
                variant = layer.variants.get(ExperimentName.SYSTEM_PROMPT.value)
                return variant if isinstance(variant, str) else None
        return None

    def _start_model_catalog_fetch(self) -> None:
        """Follow the catalog the process holds for this session's account,
        fetching it with none held; readiness and the first turn wait for that
        fetch when the session's model may come from the catalog.

        A no-op unless the experimental flag is on.
        """
        context = self._context
        orchestrator = context.config_orchestrator
        if not orchestrator.config.experimental_enable_model_catalog:
            return
        if (task := self._model_catalog_task) is not None and not task.done():
            return
        service = context.model_catalog_service
        task = asyncio.create_task(self._open_model_catalog())
        self._model_catalog_task = task
        self._model_catalog_wait = (
            ModelCatalogWait(task, service.failures)
            if model_catalog_wait_needed(
                orchestrator, service.failures, context.platform_credentials
            )
            else None
        )

    async def _open_model_catalog(self) -> None:
        """Load the catalog held for this session's account, once the process
        holds one: at once from memory or disk, else after the first fetch.
        Fail-open: without one, the session runs on local config.
        """
        context = self._context
        key = await ModelCatalogKey.of(
            context.config_orchestrator.config, context.platform_credentials
        )
        if key is None or self._closed:
            return
        await context.model_catalog_service.ensure(key)
        try:
            changed = await self._load_model_catalog(reserved_turn=False, strict=False)
        except Exception as exc:
            logger.exception("Failed to apply the model catalog to config")
            _report(exc, "apply_model_catalog")
            return
        if not changed:
            return
        await self.wait_announced()
        if not self._closed:
            await self._announce_unannounced_runtime()

    async def _wait_for_model_catalog(self) -> None:
        # Waiters arrive once the session is announced, so the fetch's own
        # wait for the announce never holds them.
        if (wait := self._model_catalog_wait) is not None:
            await wait.wait()

    async def _wait_for_first_turn_fetches(self) -> None:
        await self._wait_for_cold_integrations()
        # Caps that count from session open cost the longer one. A catalog the
        # eval turns on starts its cap when the eval lands, so the two add up.
        # The eval wait is for turns only: ``session/ready/wait`` keeps waiting
        # for the whole eval, so the startup telemetry it gates carries its
        # fields.
        await self._wait_for_model_catalog()
        # A model picked since the session opened leaves the eval nothing to
        # route: the experiment only sets the default of an unpinned session.
        # Checked after the catalog, which can resolve a pin.
        if self._context.config_orchestrator.config.active_model:
            return
        if (wait := self._experiments_wait) is not None:
            await wait.wait()
            # The eval can turn the catalog on and start its fetch.
            await self._wait_for_model_catalog()

    async def _rederive_in_background(
        self,
        failure: str,
        boundary: str,
        prepare: Callable[[], Awaitable[bool]] | None = None,
    ) -> bool:
        """``prepare`` runs first under the admin lock, and False skips the derivation.
        Fail-open: a failure is logged as ``failure`` and reported under ``boundary``.
        A running turn keeps its config, and a derivation it parks still counts as
        nothing failed.
        """
        try:
            async with self._context.admin_config_lock:
                if prepare is not None and not await prepare():
                    return True
                changed = await self._apply_derivation_when_idle()
        except Exception as exc:
            logger.exception(failure)
            _report(exc, boundary)
            return False
        if not changed:
            return True
        await self.wait_announced()
        if not self._closed:
            await self._announce_runtime()
        return True

    def _cancel_model_catalog_task(self) -> asyncio.Task[None] | None:
        task, self._model_catalog_task = self._model_catalog_task, None
        return cancel_pending(task)

    def _cancel_registry_sync(
        self,
    ) -> asyncio.Future[list[BaseException | None]] | None:
        return self._registry_sync.stop()

    # Only what ``adapter_config`` carries takes effect in this session: the
    # model variants apply now, while ``system_prompt_id`` rides ``core_config``,
    # which is not pushed live, so it governs from the next session open. The
    # connector builtins are resolved once at open, so they follow the same rule.
    async def _apply_experiment_variants(self) -> None:
        context = self._context
        try:
            layer = context.config_orchestrator.get_layer(GrowthbookLayer.NAME)
        except KeyError:
            return
        if not isinstance(layer, GrowthbookLayer):
            return
        orchestrator = context.config_orchestrator
        catalog_was_on = orchestrator.config.experimental_enable_model_catalog
        variants = context.experiment_manager.config_variants()
        # A catalog flag the session opened with stays on until the next
        # session, like the catalog it loaded: otherwise the old model flags it
        # replaced would come back beside that catalog.
        catalog_flag = ExperimentName.MODEL_CATALOG.value
        if catalog_flag in layer.variants and catalog_flag not in variants:
            variants[catalog_flag] = layer.variants[catalog_flag]
        if layer.variants == variants:
            # Built from the eval cache that eval wrote: nothing changes, and a
            # reload would only race other writers of the same config.
            return
        async with context.admin_config_lock:
            layer.set_variants(variants)
            await orchestrator.reload()
            await self._apply_derivation_when_idle()
        # The session was built with the catalog off: a catalog the eval turns
        # on is followed from now, from the one the process holds if any, as at
        # open. It loads at once between turns, else at the next boundary.
        if not catalog_was_on:
            self._start_model_catalog_fetch()

    async def _settle_configuration(self) -> None:
        """Land a parked write, and tools loaded since, at a turn boundary.

        The Session calls this when a turn ends, and before it opens the next
        while holding its turn-start lock, so a message sent the moment a turn
        ends starts only after both landed. A refused write does not hold back
        the tools.

        A queued turn or a scheduled loop's turn is opened by the Session and
        never reaches ``start_turn``, so whenever one opens next the choices it
        runs on are pinned here, the way ``start_turn`` pins its own.
        """
        await self._load_remote_values()
        await self._pin_for_session_opened_turn()
        await self._converge()
        await self._publish_integrations()
        await self._reload_parked_plugins()

    async def _pin_for_session_opened_turn(self) -> None:
        # Both settles of a turn boundary can get here at once: the one the
        # turn's end fires and the one the queue promotion awaits. One pins,
        # the other finds the choices already recorded.
        async with self._session_opened_turn_pinning:
            if (
                self._queued_turn_opens_next() or self._scheduled_turn_opens_next()
            ) and await self._pin_session_model_choice():
                self._runtime_unannounced = True

    def _scheduled_turn_opens_next(self) -> bool:
        """Whether a scheduled loop's turn is opening, and the Session admits it.

        Mirrors what the Session refuses a turn it starts itself for: one
        already running, or a queue holding turns, paused or not.
        """
        return (
            self._scheduled_turn_opening
            and self._session.active_turn_id is None
            and not self._session.turn_queue.items
        )

    def _queued_turn_opens_next(self) -> bool:
        """Whether the Session promotes a queued turn at this boundary.

        Mirrors ``SessionTurnQueue.peek_next``: a paused queue, or a head the
        user is editing, promotes nothing, and pinning for it would take a
        session that follows the default off it with no turn having run.
        """
        if self._session.active_turn_id is not None:
            return False
        queue = self._session.turn_queue
        if queue.paused or not queue.items:
            return False
        editing = queue.editing
        return editing is None or editing.queue_item_id != queue.items[0].id

    async def _settle_reserved_turn_configuration(self) -> None:
        """Land a parked write before a reserved turn's Runtime is promoted.

        A deferred turn never passes the boundary ``_settle_configuration``
        runs on: it is reserved while its workspace is prepared, and promoted
        from there.
        """
        await self._load_remote_values(reserved_turn=True)
        try:
            settled = await self._deferred.settle_through(
                partial(self._push_configuration, allow_reserved_turn=True)
            )
        except HarnessConfigurationRejectedError as exc:
            await self._drop_refused_configuration(exc)
            return
        if settled:
            self._runtime_unannounced = True
        # The reserved turn's Core is built from the routes, so what a loaded
        # value moved reaches it only if they move now.
        await self._converge_integrations()
        await self._announce_unannounced_runtime()

    async def _drop_refused_configuration(
        self, exc: HarnessConfigurationRejectedError
    ) -> None:
        """Tell the user a parked change was refused, and let the turn open.

        A pick made mid-turn is checked against the context as it stood; the
        rest of the turn can grow it past what the picked model fits. The turn
        that was waiting runs on what Core already runs, and the session
        reports that again. The configuration keeps the change, and Core
        refuses it again, with its reason, whenever it is next sent while the
        context still does not fit.
        """
        logger.warning(
            "Dropped a parked configuration Core refused for session_id=%s: %s",
            self.session_id,
            exc,
        )
        async with self._deferred.exclusive():
            self._runtime = self._projected_runtime(self._running)
        await self._announce_runtime()
        self._publish_notice(f"Your configuration change was not applied: {exc}")

    async def _announce_runtime(self) -> None:
        """Tell subscribers what the session is running now.

        Dispatch emits ``runtime/updated`` for a write it applied, and there is
        nothing to emit one for a write it parked. A turn opens on every path
        that announces from here and the configuration has already landed, so a
        client that cannot be reached is logged rather than left to stop it.
        """
        self._runtime_unannounced = False
        try:
            await self._notify_runtime_updated()
        except Exception:
            logger.exception(
                "Failed to emit runtime/updated for session_id=%s", self.session_id
            )

    async def _announce_unannounced_runtime(self) -> None:
        if self._runtime_unannounced:
            await self._announce_runtime()

    async def _stop_background_work(self) -> None:
        """Stop what the session still owns before teardown continues."""
        tasks = (self._scheduled_loops_task, self._skill_watch_task)
        self._scheduled_loops_task = None
        self._skill_watch_task = None
        self._skill_watch_started = False
        for task in tasks:
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def start_skill_watch(self) -> None:
        """Sandboxed sessions read skills inside the sandbox, so they keep ``/reload``."""
        self._skill_watch_started = True
        self._follow_skill_roots()

    def start_registry_sync(self) -> None:
        self._registry_sync.start()

    def _skill_watch_wanted(self) -> bool:
        return (
            self._adapter_config.sandbox is None
            and self._context.config_orchestrator.config.file_watcher_for_autocomplete
        )

    def _current_skill_roots(self) -> SkillWatchRoots:
        context = self._context
        return skill_watch_roots(
            context.harness_files, context.config_orchestrator.config.skill_paths
        )

    def _follow_skill_roots(self) -> None:
        """Called on every adopted derivation (trust, config, worktree, move)."""
        if not self._skill_watch_started:
            return
        task = self._skill_watch_task
        # Restarting from inside the watch would cancel its own announce.
        if task is not None and task is asyncio.current_task():
            return
        wanted = self._skill_watch_wanted()
        roots = self._current_skill_roots() if wanted else None
        if task is not None and not task.done() and roots == self._skill_watch_roots:
            return
        if task is not None:
            task.cancel()
        self._skill_watch_task = None
        self._skill_watch_roots = roots
        if roots is None:
            return
        self._skill_watch_task = asyncio.create_task(
            self._run_skill_watch(), name="vibe-unified-skill-watch"
        )

    async def _run_skill_watch(self) -> None:
        """Retries a failed watch with backoff instead of dropping back to ``/reload``."""

        def roots() -> SkillWatchRoots:
            current = self._current_skill_roots()
            self._skill_watch_roots = current
            return current

        delay = _SKILL_WATCH_RETRY_INITIAL_S
        while not self._closed:
            try:
                async for _ in watch_skill_changes(roots):
                    if self._closed:
                        return
                    delay = _SKILL_WATCH_RETRY_INITIAL_S
                    await self._rederive_after_skill_change()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Skill directory watch failed for session_id=%s; retrying in %ss",
                    self.session_id,
                    delay,
                    exc_info=True,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, _SKILL_WATCH_RETRY_MAX_S)

    async def _rederive_after_skill_change(self) -> None:
        logger.debug(
            "Skill directories changed; re-deriving session_id=%s", self.session_id
        )
        context = self._context
        async with context.admin_config_lock:
            await context.config_orchestrator.reload(preflight=self._preflight_config)
            changed = await self._apply_derivation_when_idle()
        if changed:
            await self._announce_runtime()

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

    def expect_handed_task(self, hops: int) -> None:
        """The first turn carries a task another session handed over."""
        self._handed_task_pending = True
        self._message_hops = hops

    @property
    def message_hops(self) -> int:
        return self._message_hops

    @property
    def turn_started_sessions(self) -> int:
        if self._turn_counting_starts != self._session.active_turn_id:
            return 0
        return self._turn_started_sessions

    def count_turn_started_session(self, delta: int) -> None:
        turn = self._session.active_turn_id
        if turn != self._turn_counting_starts:
            self._turn_counting_starts = turn
            self._turn_started_sessions = 0
        self._turn_started_sessions = max(0, self._turn_started_sessions + delta)

    def _user_joined_the_waiting_turn(self) -> None:
        """The Session steered the user's queued message into a turn that only
        waited: the user wrote to it, as on `steer_queued_turn`.
        """
        self._message_hops = 0

    @property
    def started_by_session_id(self) -> str | None:
        return self._session.started_by_session_id

    async def start_turn(
        self, params: TurnStartParams
    ) -> SessionBackendResult[TurnStartResponse]:
        handed_task = self._handed_task_pending
        hops_before = self._message_hops
        if handed_task:
            params = self._with_report_back_instructions(params)
        else:
            self._message_hops = 0
        self._announce_first_use()
        try:
            result = await self._start_turn(params)
        except BaseException:
            # A refused start leaves the running turn its hops.
            self._message_hops = hops_before
            raise
        # Only once it started: a retry of a failed start is still the task.
        if handed_task:
            self._handed_task_pending = False
        await self._record_pending_seat()
        return result

    def _with_report_back_instructions(
        self, params: TurnStartParams
    ) -> TurnStartParams:
        started_by = self.started_by_session_id
        if started_by is None:
            return params
        return params.model_copy(
            update={
                "message": [
                    *params.message,
                    TextContentBlock(text=handed_task_text(started_by)),
                ]
            }
        )

    async def _start_turn(
        self, params: TurnStartParams, *, prepared: bool = False
    ) -> SessionBackendResult[TurnStartResponse]:
        self._require_no_session_rewrite()
        self._require_model_owned_root(params.session_id)
        # A failed preparation publishes its own worktree entry. Taking the
        # deferred path afterwards would publish that failure a second time,
        # including when turn/start runs during the failure notify.
        if (
            self._turns_deferred is not None
            and not self._deferred_turn_claimed
            and not self._setup_abandoned
            and self._deferred_turns is not None
            and self._session.ephemeral
        ):
            self._deferred_turn_claimed = True
            try:
                return await self._start_deferred_turn(params, prepared=prepared)
            except BaseException as exc:
                self._deferred_turn_claimed = False
                if not isinstance(exc, Exception) or self._session.ephemeral:
                    raise
        await self._await_deferred_setup()
        # Held here too, not only in session/ready/wait: a client may start a
        # turn without waiting for readiness.
        await self._wait_for_first_turn_fetches()
        runtime_updated = await self._pin_session_model_choice()
        if not prepared:
            params = await self._prepared_turn_params(params, inject_skill=True)
        # Set the message id before the harness session schedules the runtime
        # task, so the provider request metadata reads it on the first
        # completion rather than racing the adapter's snapshot reconciliation.
        self._set_client_message_id(params.client_user_message_id)
        self._user_request = _text_from_blocks(params.message) or None
        self._track_turn_attachments(params.message)
        await self._report_unlanded_worktree()
        was_ephemeral = self._session.ephemeral
        result = await _harness_call(self._commands.start_turn(params))
        await self._persist_scheduled_loops_after_promotion(was_ephemeral=was_ephemeral)
        await self._persist_session_metadata_after_promotion(
            was_ephemeral=was_ephemeral
        )
        turn = result.response
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
        self, params: TurnStartParams, *, prepared: bool
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
        self._user_request = _text_from_blocks(params.message) or None
        self._track_turn_attachments(params.message)

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
            context: HarnessDeferredTurnPreparationContext,
        ) -> HarnessDeferredTurnPreparationResult:
            nonlocal resolution
            try:
                # Left before the settled entry is returned, so no progress can
                # land on an entry that already completed.
                async with publishing_progress(
                    progress,
                    lambda: context.replace_pending_history_entries(pending_entries()),
                ):
                    setup = await self._await_deferred_workspace()
                if not isinstance(setup, WorktreeResolution):
                    raise RuntimeError("Deferred worktree setup returned no resolution")
                resolution = setup
                # After the resolution is recorded, so a pick that cannot be
                # written fails the turn without marking a ready worktree failed.
                await self._apply_model_pick_after_setup()
                await self._wait_for_first_turn_fetches()
                await self._settle_reserved_turn_configuration()
                if await self._pin_session_model_choice():
                    await self._announce_runtime()
                ready = (
                    params
                    if prepared
                    else await self._prepared_turn_params(params, inject_skill=True)
                )
            except Exception as exc:
                raise HarnessDeferredTurnPreparationError(
                    exc, failed_entries(exc)
                ) from exc
            return HarnessDeferredTurnPreparationResult(
                runtime_input=HarnessPreparedRuntimeInput(
                    turn=self._deferred_turn_request(ready)
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

    async def _emit_worktree_failure(self, error: BaseException) -> None:
        """Push a failed session-start worktree to the client as a history entry.

        Normally the worktree transcript effect settles with the first deferred
        turn, but a failed preparation leaves no turn to carry it and a client
        gating its UI on the worktree would otherwise wait out its deadline.
        """
        progress = self._worktree_progress
        if progress is None or self._services is None:
            return
        if self._deferred_turn_claimed:
            # The claimed turn carries the failure entry itself
            # (`failed_entries`); emitting here too would render the
            # failure twice for reserved-first-turn clients.
            return
        entry = self._worktree_public_entry(
            f"worktree-{uuid4()}", progress.detail, progress.failed_state(error)
        )
        await self._services.notify(
            "history/entryAdded",
            HistoryEntryAddedParams(
                event_id=0,
                session_id=self.session_id,
                turn_id=None,
                emitted_at=now_ms(),
                entry=entry,
            ),
        )

    def _worktree_history_entry(
        self,
        entry_id: str,
        detail: WorktreeEffectDetail,
        state: RunningEffectState | CompletedEffectState | FailedEffectState,
    ) -> HarnessPublicHistoryEntry:
        entry = self._worktree_public_entry(entry_id, detail, state)
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

    def _worktree_public_entry(
        self,
        entry_id: str,
        detail: WorktreeEffectDetail,
        state: RunningEffectState | CompletedEffectState | FailedEffectState,
    ) -> PublicEffectEntry:
        return PublicEffectEntry(
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
        # Who started the running turn decides; a queued message only decides
        # for the turn it starts.
        self._announce_first_use()
        self._require_no_session_rewrite()
        await self._await_deferred_setup()
        # The Session starts a queued turn itself, never through start_turn,
        # so the held catalog fetch and eval are waited for here too.
        await self._wait_for_first_turn_fetches()
        # The session model is deliberately left alone: a queued turn is
        # promoted by the Session, whose settle hook (`_settle_configuration`)
        # lands the parked configuration and pins what the turn runs on.
        # Recording here would resolve an empty pick to today's default and
        # take the session off the default the user asked to follow.
        self._refuse_a_queued_message_invoking_a_skill(params)
        try:
            params = _from_its_sender(await self._prepared_queue_params(params))
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        self._track_queue_attachments(params)
        if params.from_session:
            return await self._deliver_session_message(params)
        harness_params = _harness_enqueue_params(params)
        # Under the archive gate, so an archive lands wholly before or after.
        async with self._archive_gate:
            waits = self._session.turn_waits
            result = cast(
                Any, await _harness_call(self._session.enqueue_turn(harness_params))
            )
        # bumped_at orders the session list by the start of a new user turn.
        # A queued message only starts a turn once promoted, and the promotion
        # path bumps then; enqueueing must not move the session. Arm the
        # promotion bump here so the promoted turn's TurnStarted can land it.
        response = validate_backend_wire(
            TurnEnqueueResponse,
            {
                **result.response.model_dump(mode="json", by_alias=True),
                **({"waits": True} if waits else {}),
            },
        )
        queue_item_id = result.response.queue_item_id
        if queue_item_id is not None:
            self._promoted_queue_item_ids.add(queue_item_id)
        await self._record_pending_seat()
        return SessionBackendResult(
            response=response, after_response=result.after_response
        )

    async def _deliver_session_message(
        self, params: TurnEnqueueParams
    ) -> SessionBackendResult[TurnEnqueueResponse]:
        """A message from another session never waits in the queue the user
        edits: it joins the running turn, as a subagent's result does, waking a
        turn waiting for input. Otherwise it starts a turn of its own, or with
        ``delivery: "queue"`` waits in the model's context for the next turn.
        """
        entry = params.user_entry
        if entry is None:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, "A session message needs text"
            )
        message_id = entry.entry_id or str(uuid4())
        key = params.idempotency_key
        for attempt in range(_SESSION_MESSAGE_DELIVERY_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_SESSION_MESSAGE_RETRY_SECONDS)
            # Each attempt, not the waits between them, holds the archive gate,
            # so an archive lands wholly before or after it.
            async with self._archive_gate:
                if key is not None and (
                    response := self._session_messages_delivered.get(key)
                ):
                    return SessionBackendResult(response=response)
                await self._refuse_a_peer_message_if_archived()
                delivered = await self._join_running_turn(params, entry, message_id)
                waits = False
                if delivered is None and params.delivery == "queue":
                    delivered = await self._leave_session_message_for_next_turn(
                        entry, message_id
                    )
                    waits = delivered is not None
                elif delivered is None:
                    delivered = await self._start_session_message_turn(
                        params, entry, message_id
                    )
                if delivered is not None:
                    response = TurnEnqueueResponse(
                        queue_item_id=message_id, waits=waits
                    )
                    if key is not None:
                        self._session_messages_delivered[key] = response
                    return SessionBackendResult(
                        response=response, after_response=delivered.after_response
                    )
        raise _peer_refusal(PEER_REFUSAL_BUSY, "The session is busy. Try again.")

    def _refuse_a_queued_message_invoking_a_skill(
        self, params: TurnEnqueueParams
    ) -> None:
        """A message left for the next turn is saved as written, so a skill's
        body would land in public history, as `session/context/inject` refuses.
        """
        entry = params.user_entry
        if not params.from_session or params.delivery != "queue" or entry is None:
            return
        if self._resolved_invoked_skills(_text_from_session_blocks(entry.content)):
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                "A message that invokes a skill must start a turn: send it with "
                '`delivery: "start_turn"`.',
            )

    async def _start_session_message_turn(
        self, params: TurnEnqueueParams, entry: TurnUserInputEntry, message_id: str
    ) -> SessionBackendResult[TurnStartResponse] | None:
        """None when a turn started meanwhile, which the message then joins."""
        hops_before = self._message_hops
        # Counted before the turn runs: its first tool calls are limited too.
        self._message_hops = params.hops
        try:
            result = await self._start_turn(
                TurnStartParams(
                    idempotency_key=params.idempotency_key,
                    session_id=self.session_id,
                    message=vibe_content_blocks(entry.content),
                    client_user_message_id=message_id,
                    user_display_content=entry.annotations.vibe_user_display_content,
                ),
                prepared=True,
            )
        except BaseException as exc:
            self._message_hops = hops_before
            if (
                not isinstance(exc, SessionBackendError)
                or exc.code != ProtocolErrorCode.CONFLICT
            ):
                raise
            if self._session.turn_queue.paused and self._session.active_turn_id is None:
                raise _peer_refusal(
                    PEER_REFUSAL_PAUSED,
                    "The session's queue is paused until the user resumes it.",
                ) from exc
            return None
        await self._record_pending_seat()
        return result

    async def _archive_gate_once_settled(
        self, params: SessionArchiveParams
    ) -> asyncio.Lock:
        """Waits for a turn's work to settle, outside the gate, so a message
        can still land meanwhile. A background process is refused at once.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _PEER_ARCHIVE_SETTLE_SECONDS
        while params.require_idle and self._has_turn_work() and loop.time() < deadline:
            await asyncio.sleep(_PEER_ARCHIVE_POLL_SECONDS)
        return self._archive_gate

    def _has_turn_work(self) -> bool:
        """An older Harness has no ``has_turn_work``; its running turn is all
        the archive waits for.
        """
        try:
            return bool(self._session.has_turn_work)
        except AttributeError:
            return self._session.active_turn_id is not None

    def _refuse_while_paused_and_idle(self) -> None:
        """A turn that started meanwhile can still take the message."""
        if self._session.active_turn_id is None:
            raise _peer_refusal(
                PEER_REFUSAL_PAUSED,
                "The session's queue is paused until the user resumes it.",
            )

    async def _leave_session_message_for_next_turn(
        self, entry: TurnUserInputEntry, message_id: str
    ) -> SessionBackendResult[ContextInjectResponse] | None:
        """The message enters the model's context and public history without
        starting a turn. None when a turn started meanwhile, which the message
        then joins. Refused while the user's queue is paused, as a message
        that would start a turn is.
        """
        if self._session.turn_queue.paused:
            self._refuse_while_paused_and_idle()
            return None
        was_ephemeral = bool(getattr(self._session, "ephemeral", True))
        try:
            result = cast(
                Any,
                await _harness_call(
                    self._session.inject_context(
                        _SessionMessageInjectParams(
                            session_id=self.session_id,
                            input=vibe_content_blocks(entry.content),
                            as_message=True,
                            client_user_message_id=message_id,
                            user_display_content=(
                                entry.annotations.vibe_user_display_content
                            ),
                        )
                    )
                ),
            )
        except SessionBackendError as exc:
            if exc.code != ProtocolErrorCode.CONFLICT:
                raise
            if self._session.turn_queue.paused:
                self._refuse_while_paused_and_idle()
            return None
        await self._persist_scheduled_loops_after_promotion(was_ephemeral=was_ephemeral)
        await self._persist_session_metadata_after_promotion(
            was_ephemeral=was_ephemeral
        )
        self._set_client_message_id(message_id)
        await self._record_pending_seat()
        return SessionBackendResult(
            response=ContextInjectResponse(entries=[]),
            after_response=result.after_response,
        )

    async def _join_running_turn(
        self, params: TurnEnqueueParams, entry: TurnUserInputEntry, message_id: str
    ) -> CommandResult[Accepted] | None:
        """None when no turn runs, or it ended meanwhile."""
        turn_id = self._session.active_turn_id
        if turn_id is None:
            return None
        try:
            result = await _harness_call(
                self._commands.steer_turn(
                    TurnSteerParams(
                        idempotency_key=params.idempotency_key,
                        session_id=self.session_id,
                        expected_turn_id=turn_id,
                        message=vibe_content_blocks(entry.content),
                        client_user_message_id=message_id,
                        user_display_content=(
                            entry.annotations.vibe_user_display_content
                        ),
                    )
                )
            )
        except SessionBackendError as exc:
            if exc.code in {ProtocolErrorCode.STALE_TURN, ProtocolErrorCode.CONFLICT}:
                return None
            raise
        self._set_client_message_id(message_id)
        # Only raised: a message joining the user's turn must not let two
        # sessions keep it going past the hop limit by replying into it.
        self._message_hops = max(self._message_hops, params.hops)
        return result

    async def _refuse_a_peer_message_if_archived(self) -> None:
        # From disk: another process may have archived the session since this
        # one opened it.
        stored = await asyncio.to_thread(
            _read_unified_session_metadata, self._session_dir()
        )
        if _metadata_archived_at_ms(stored) or _metadata_archived_at_ms(self._metadata):
            raise _peer_refusal(PEER_REFUSAL_ARCHIVED, "The session is archived.")

    async def _persist_session_active_model(self, active_model: str) -> bool:
        """Record the model this session runs on the session itself.

        A reopened session restores its model from this pin, so a pick that
        only reached the configuration would come back as the model the last
        turn started with -- contradicting the transcript marker it wrote.

        The pick is recorded resolved, empty alias included: a catalog alias
        pins the model it names. Storing what the default resolves to today
        would pin the session to that model, and following the default is
        what picking it asked for.
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
        return await self._persist_pin(SessionPin.ACTIVE_MODEL, active_model)

    def settings(self) -> SessionSettings:
        return session_settings(
            self._context.config_orchestrator.config, self._context.agents
        )

    async def pin_started_agent(self) -> None:
        """Record the agent a start chose as the session's, so a reopen restores it."""
        await self._persist_pin(
            SessionPin.AGENT_NAME, self._context.agents.active_profile.name
        )

    async def _persist_pin(self, pin: SessionPin, value: str) -> bool:
        """Save one session pin and, on the Session API path, announce it.

        That path reads the pins into every runtime update it translates, and
        a pin is saved after the configuration push whose update it belongs
        to, so without this the client would see each pick one change late.
        """
        changed = await _harness_call(self._session.persist_pin(pin, value))
        state = self._translated_state
        if not changed or self._api_path is None or state is None:
            return changed
        pins = self._live_session_pins()
        session = state.session.model_copy(
            update={
                "model": pins.model,
                "reasoning_effort": pins.reasoning_effort,
                "agent": pins.agent,
            }
        )
        patch = make_json_patch(
            state.session.model_dump(mode="json", by_alias=True),
            session.model_dump(mode="json", by_alias=True),
        )
        if patch:
            self._publish_session_metadata(
                _SessionMetadataProjection(
                    session_id=self.session_id, patch=tuple(patch)
                )
            )
        return changed

    async def _persist_session_reasoning_effort(self) -> bool:
        """Record the level this session runs, so a reopen does not read the host's."""
        orchestrator = self._context.config_orchestrator
        model = orchestrator.config.get_active_model()
        # A model owned above the session has no level the session can call its
        # own, and the one recorded for a previous model is not it: the empty
        # pin is how a session says it follows the configuration. So does an
        # unset level: pinning its default "off" would turn it into an explicit
        # off on reopen, which a model that always reasons refuses.
        level = (
            model.thinking
            if model.thinking_is_set
            and model_thinking_is_session_writable(orchestrator, model.alias)
            else ""
        )
        if (self._session.pin(SessionPin.REASONING_EFFORT) or "") == level:
            return False
        return await self._persist_pin(SessionPin.REASONING_EFFORT, level)

    async def _pin_session_model_choice(self) -> bool:
        """Pin what the turn about to start runs on: the model, and its level.

        The agent is recorded too, but never changes what the turn runs on, so
        it never asks for a re-derivation.
        """
        await self._pin_session_agent()
        await self._persist_session_catalogue()
        orchestrator = self._context.config_orchestrator
        before = orchestrator.config
        pinned_model = await self._persist_session_active_model(
            before.get_active_model().alias
        )
        pinned_effort = await self._persist_session_reasoning_effort()
        if not (pinned_model or pinned_effort):
            return False
        # Both pins record what the session already runs, so when the config
        # moved only in ``active_model`` the derivation is stale only in its
        # pinned flag, and re-deriving would just repeat tool and skill discovery.
        # Writing the model pin reloads every layer from disk, though, which can
        # bring in edits no derivation has seen.
        if _moved_only_active_model(before, orchestrator.config):
            self._runtime = self._runtime.model_copy(
                update={
                    "config": self._runtime.config.model_copy(
                        update={
                            "active_model_pinned": active_model_is_pinned(orchestrator)
                        }
                    )
                }
            )
            return True
        self._adopt_derivation(await self.derive_configuration())
        return True

    async def _persist_session_catalogue(self) -> None:
        """Save the catalogue's answer for the model the session runs.

        A reopen reuses it, so the session keeps its context window and
        compaction threshold when the catalogue is down. A derivation the
        catalogue gave nothing keeps the last answer saved: it is for another
        model, which a reopen only reuses when it runs that model again.
        """
        snapshot = self._completion_catalogue
        if snapshot is None:
            return
        value = snapshot.to_pin()
        if self._session.pin(SessionPin.COMPLETION_CATALOGUE) == value:
            return
        await self._persist_pin(SessionPin.COMPLETION_CATALOGUE, value)

    async def _pin_session_agent(self) -> None:
        """Record the agent the turn about to start runs on.

        An agent switch already pins its pick, but a session that never switched
        kept no record of its agent, ``--agent`` included, so neither a reopen nor
        a reader of the session's storage could tell. A pin a reopen could not
        apply, because its agent is gone, is replaced by the agent that runs.
        """
        await self._persist_pin(
            SessionPin.AGENT_NAME, self._context.agents.active_profile.name
        )

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
            response=TurnQueueReadResponse(
                queue=_public_turn_queue(result.response.queue)
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
        # Replacing a queued message is queue editing, not the start of a turn;
        # the promoted turn bumps bumped_at when it actually starts. Re-arm the
        # promotion bump on the replacement item id, which the Harness may have
        # minted anew.
        queue_item_id = result.response.queue_item_id
        if queue_item_id is not None:
            self._promoted_queue_item_ids.add(queue_item_id)
        return SessionBackendResult(
            response=validate_backend_wire(
                TurnQueueReplaceResponse,
                result.response.model_dump(mode="json", by_alias=True),
            ),
            after_response=result.after_response,
        )

    async def start_queued_turn_edit(
        self, params: TurnQueueEditStartParams
    ) -> SessionBackendResult[TurnQueueEditStartResponse]:
        result = cast(
            Any,
            await _harness_call(
                self._session.start_queued_turn_edit(
                    HarnessTurnQueueEditStartParams.model_validate(
                        params.model_dump(mode="json", by_alias=True)
                    )
                )
            ),
        )
        return SessionBackendResult(
            response=validate_backend_wire(
                TurnQueueEditStartResponse,
                result.response.model_dump(mode="json", by_alias=True),
            ),
            after_response=result.after_response,
        )

    async def save_queued_turn_edit(
        self, params: TurnQueueEditSaveParams
    ) -> SessionBackendResult[TurnQueueEditSaveResponse]:
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
                self._session.save_queued_turn_edit(
                    HarnessTurnQueueEditSaveParams.model_validate(
                        params.model_dump(mode="json", by_alias=True)
                    )
                )
            ),
        )
        response = validate_backend_wire(
            TurnQueueEditSaveResponse,
            result.response.model_dump(mode="json", by_alias=True),
        )
        # Saving only edits the queue; advance bumped_at when the saved turn starts.
        self._promoted_queue_item_ids.add(response.queue_item_id)
        return SessionBackendResult(
            response=response, after_response=result.after_response
        )

    async def cancel_queued_turn_edit(
        self, params: TurnQueueEditCancelParams
    ) -> SessionBackendResult[TurnQueueEditCancelResponse]:
        result = cast(
            Any,
            await _harness_call(
                self._session.cancel_queued_turn_edit(
                    HarnessTurnQueueEditCancelParams.model_validate(
                        params.model_dump(mode="json", by_alias=True)
                    )
                )
            ),
        )
        return SessionBackendResult(
            response=validate_backend_wire(
                TurnQueueEditCancelResponse,
                result.response.model_dump(mode="json", by_alias=True),
            ),
            after_response=result.after_response,
        )

    async def abandon_queued_turn_edit(
        self, params: TurnQueueEditAbandonParams
    ) -> SessionBackendResult[TurnQueueEditAbandonResponse]:
        await _harness_call(
            self._session.abandon_queued_turn_edit(
                HarnessTurnQueueEditAbandonParams(
                    session_id=params.session_id,
                    queue_item_id=params.queue_item_id,
                    edit_id=params.edit_id,
                )
            )
        )
        return SessionBackendResult(response=TurnQueueEditAbandonResponse())

    async def abandon_active_queued_turn_edit(self) -> None:
        await _harness_call(self._session.abandon_active_queued_turn_edit())

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
        # Resuming the queue does not start a turn by itself; the promoted turn
        # bumps bumped_at when it starts.
        return SessionBackendResult(
            response=validate_backend_wire(
                TurnQueueResumeResponse,
                result.response.model_dump(mode="json", by_alias=True),
            ),
            after_response=result.after_response,
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
        # The user wrote, as on `steer_turn`, even if the steer then fails.
        self._message_hops = 0
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
        await self.flush_events()
        response = result.response
        # Steering a queued message is queue editing, not the start of a turn.
        return SessionBackendResult(
            response=TurnQueueSteerResponse(
                queue_item_id=response.queue_item_id,
                turn_id=response.turn_id,
                last_event_id=self._event_id,
            ),
            after_response=result.after_response,
        )

    async def steer_turn(
        self, params: TurnSteerParams
    ) -> SessionBackendResult[TurnSteerResponse]:
        self._message_hops = 0
        self._require_model_owned_root(params.session_id)
        params = await self._prepared_turn_params(
            params, inject_skill=params.inject_invoked_skill
        )
        self._set_client_message_id(params.client_user_message_id)
        self._user_request = _text_from_blocks(params.message) or None
        self._track_turn_attachments(params.message)
        result = await _harness_call(self._commands.steer_turn(params))
        response = result.response
        # Steering injects a message into the running turn; the turn already
        # started, so this is not the start of a new one.
        return SessionBackendResult(
            response=TurnSteerResponse(
                accepted=response.accepted,
                last_event_id=self._client_event_id(response),
            ),
            after_response=result.after_response,
        )

    async def interrupt_turn(
        self, params: TurnInterruptParams
    ) -> SessionBackendResult[TurnInterruptResponse]:
        self._require_model_owned_root(params.session_id)
        result = await _harness_call(self._commands.interrupt_turn(params))
        response = result.response
        harness_after_response = result.after_response
        harness_on_response_abandoned = result.on_response_abandoned
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
                accepted=response.accepted,
                last_event_id=self._client_event_id(response),
            ),
            after_response=after_response,
            on_response_abandoned=on_response_abandoned,
        )

    async def inject_context(
        self, params: ContextInjectParams
    ) -> SessionBackendResult[ContextInjectResponse]:
        self._require_model_owned_root(params.session_id)
        invoked = (
            await self._invoked_skill_blocks(params.input)
            if params.inject_invoked_skill
            else []
        )
        if invoked and params.as_message:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS,
                "A persisted context message cannot invoke a skill; start or steer "
                "a turn instead.",
            )
        try:
            params = await self._with_described_input_images(params)
            params = await self._with_mentioned_file_blocks_for_input(params)
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        if invoked:
            blocks = [block for _name, block in invoked]
            params = params.model_copy(update={"input": [*params.input, *blocks]})
        self._announce_first_use()
        was_ephemeral = bool(getattr(self._session, "ephemeral", True))
        result = cast(Any, await _harness_call(self._session.inject_context(params)))
        await self._persist_scheduled_loops_after_promotion(was_ephemeral=was_ephemeral)
        await self._persist_session_metadata_after_promotion(
            was_ephemeral=was_ephemeral
        )
        await self._record_pending_seat()
        response = result.response
        return SessionBackendResult(
            response=ContextInjectResponse(
                entries=[
                    projected
                    for entry in response["entries"]
                    for projected in project_history_entries(
                        entry, _display_roots(self.cwd, self._workspace_roots())
                    )
                ]
            ),
            after_response=result.after_response,
        )

    def open_callbacks(self) -> list[PublicCallbackEntry]:
        if self._host is not None:
            return [
                with_available_path_scopes(with_approval_display(callback))
                for raw in self._host.open_callbacks(self.session_id)
                if isinstance(
                    callback := validate_history_entry(raw), PublicCallbackEntry
                )
            ]
        return [
            with_available_path_scopes(callback)
            for callback in self._open_callbacks.values()
        ]

    async def announce_running_turn(self) -> None:
        if self._session.active_turn_id is None:
            return
        read = await _harness_call(
            self._session.read(
                HarnessSessionReadParams(session_id=self.session_id, history_limit=0)
            )
        )
        latest_turn = read.snapshot.state.latest_turn
        if latest_turn is None or latest_turn.id != self._session.active_turn_id:
            return
        turn = _public_turn(latest_turn)
        if turn.status is not PublicTurnStatus.IN_PROGRESS:
            return
        # The forwarder is not running yet. Hold the announcement until it
        # starts, ahead of the recovered turn's live events.
        self._publish_local_event(TurnStarted(turn), buffer=True)

    def references_child(self, session_id: str) -> bool:
        return self._host is not None and self._host.references_child(
            self.session_id, session_id
        )

    def _require_known_session(self, session_id: str) -> None:
        if session_id != self.session_id and not self.references_child(session_id):
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )

    async def reject_callback_delivery(
        self, session_id: str, callback_id: str, error: CallbackResultError
    ) -> None:
        self._require_known_session(session_id)
        params = CallbackResultParams(
            session_id=session_id,
            result=CallbackResult(callback_id=callback_id, error=error),
        )
        await _harness_call(self._commands.respond_to_callback(params))

    async def respond_to_callback(
        self, params: CallbackResultParams
    ) -> SessionBackendResult[CallbackResultResponse]:
        await self._record_approval_grant(params)
        result = await _harness_call(self._commands.respond_to_callback(params))
        response = result.response
        callback_response = CallbackResultResponse(
            accepted=response.accepted, last_event_id=self._client_event_id(response)
        )
        # Answering a callback (permission approval, question) continues the
        # running turn; it is not the start of a new one, so it must not move
        # the session in the activity-ordered list.
        return SessionBackendResult(
            response=callback_response, after_response=result.after_response
        )

    async def _record_approval_grant(self, params: CallbackResultParams) -> None:
        """Remember an approval that reaches past the call, before it is acted on."""
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
        try:
            required_permissions = approval_grant_permissions(
                callback.detail, output.decision
            )
        except ValueError:
            # The Runtime still owns callback settlement. A stale or malformed
            # client choice must not prevent it from rejecting or consuming the
            # callback, and must never create a broader persistent grant.
            return
        await self._context.permissions.grant(
            callback.detail.effect.tool_name,
            required_permissions,
            permanent=output.decision.type is ApprovalDecisionType.APPROVE_PERMANENTLY,
        )

    async def compact(
        self, params: SessionCompactParams
    ) -> SessionBackendResult[SessionCompactResponse]:
        self._require_session(params.session_id)
        if self._api_path is not None:
            return await self._api_path.compact(params)
        return await self._compact_direct(params)

    def _hold_events(self) -> None:
        """Hold the event flush while a compaction reply is on its way."""
        if self._defer_event_flush:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "Context compaction is already running"
            )
        self._defer_event_flush = True

    def _release_events_after_reply(
        self,
        after_response: Callable[[], None] | None,
        on_response_abandoned: Callable[[], None] | None,
        *,
        on_release: Callable[[], None] | None = None,
    ) -> tuple[Callable[[], None], Callable[[], None]]:
        """The reply callbacks that end the hold once the reply is sent or dropped.

        A late callback of an earlier compaction must not release this one.
        """

        def finish_response(callback: Callable[[], None] | None) -> None:
            if self._release_deferred_events is not released:
                return
            self._release_deferred_events = None
            self._defer_event_flush = False
            if on_release is not None:
                on_release()
            if callback is not None:
                callback()

        def released() -> None:
            finish_response(after_response)

        def abandoned() -> None:
            finish_response(on_response_abandoned)

        self._release_deferred_events = released
        return released, abandoned

    async def _compact_direct(
        self, params: SessionCompactParams
    ) -> SessionBackendResult[SessionCompactResponse]:
        """Compact on the Runtime session, replying before the compaction's updates.

        The Runtime buffers the compaction's state updates. They are translated
        here, before the reply, and the event stream yields them unchanged.
        """
        previous = self._translated_state
        if previous is None:
            baseline = await self._session.read(
                HarnessSessionReadParams(session_id=self.session_id, history_limit=500)
            )
            previous = self._read_response(baseline.snapshot).state
        self._hold_events()
        try:
            result = cast(Any, await _harness_call(self._session.compact(params)))
        except BaseException:
            self._defer_event_flush = False
            raise
        response = result.response
        try:
            for raw_event in result.buffered_events:
                # Other events change no state: the live stream sends them after the reply.
                if raw_event.get("type") != "session_state_updated":
                    continue
                raw_state = raw_event.get("state")
                if not isinstance(raw_state, dict):
                    _reject("a buffered compaction update without state")
                watermark = raw_event.get("eventId")
                if not isinstance(watermark, int):
                    _reject("a buffered compaction update without an event id")
                harness_event_state = HarnessPublicSessionState.model_validate(
                    raw_state
                )
                current = _read_response(
                    HarnessSessionSnapshot(
                        state=harness_event_state,
                        history_limit=len(harness_event_state.history.entries),
                        watermark=watermark,
                    ),
                    self.cwd,
                    event_id=self._event_id,
                    metadata=self._metadata,
                    workspace_roots=self._workspace_roots(),
                    scheduled_loops=self._scheduled_loops.snapshot(),
                    mcp_apps=self._mcp_apps,
                    started_by_session_id=self._session.started_by_session_id,
                ).state
                translated, previous = self._translate_snapshot_update(
                    previous, current
                )
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
            compacted = SessionCompactResponse(
                summary=response["summary"],
                state=state,
                session_log=await self._session_log_summary(),
            )
        except BaseException:
            # The Runtime already compacted: its updates must still reach the live stream.
            self._defer_event_flush = False
            if result.after_response is not None:
                result.after_response()
            raise

        after_response, on_response_abandoned = self._release_events_after_reply(
            result.after_response, result.on_response_abandoned
        )
        return SessionBackendResult(
            response=compacted,
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
        entry = await self._rewound_entry(params.entry_id)
        # Let the snapshots see every write of the settled turn before planning.
        await self.flush_events()
        await _harness_call(self._host.rewind(params.session_id, params.entry_id))
        # An in-place rewind keeps the session, and with it the executor closure the
        # todo list lives in: left alone, `todo read` would answer with entries the
        # truncated history no longer records. A fork rewind gets a fresh executor.
        self._context.vibe_tools.forget_todos(params.session_id)
        # The truncation lands in the Core before its events reach the Client, so
        # the response would otherwise carry the rewind checkpoint under an event
        # id that still predates it and the Client would apply the entry twice.
        await self.flush_events()
        if self._api_path is not None:
            await self._api_path.reset_after_rewind()
        # Only once the truncation landed: a failed rewind leaves the disk and
        # the snapshots untouched.
        restore_errors, restored_paths = (
            self._rewind_snapshots.restore(entry.id)
            if params.restore_files
            else ([], [])
        )
        self._rewind_snapshots.drop_from(entry.id)
        snapshot = await self.read(
            SessionReadParams(
                session_id=self.session_id,
                history=PageRequest(limit=500),
                turns=PageRequest(limit=500),
            )
        )
        if await self._restate_entered_worktree(snapshot.state.history or []):
            snapshot = await self.read(
                SessionReadParams(
                    session_id=self.session_id,
                    history=PageRequest(limit=500),
                    turns=PageRequest(limit=500),
                )
            )
        return SessionRewindResponse(
            message=entry.text,
            restore_errors=restore_errors,
            restored_paths=restored_paths,
            state=snapshot.state,
            session_log=await self._session_log_summary(),
        )

    async def _require_rewindable(self, params: SessionRewindParams) -> None:
        """Reject a rewind this backend cannot serve, before anything is dropped."""
        await self._require_settled()

    async def _rewound_entry(self, entry_id: str) -> PublicMessageEntry:
        """The user message a rewind is anchored on.

        Also the entry-id guard: the Harness raises on an anchor missing from its
        own projection, but the failure has to name the entry rather than read as
        a broken session, and it has to happen before the truncation runs.
        """
        state = await self._read_state_containing(entry_id)
        entry = next(
            (entry for entry in state.history or [] if entry.id == entry_id), None
        )
        if not isinstance(entry, PublicMessageEntry) or entry.role != "user":
            raise SessionBackendError(
                ProtocolErrorCode.NOT_FOUND,
                f"Rewindable history entry not found: {entry_id}",
            )
        return entry

    def announce_on_first_use(self) -> None:
        """Hold this session's lifecycle telemetry until its first message.

        For a session started ahead of need: one discarded unused was never a
        session the user started, so it reports neither its start nor its close.
        """
        self._announce_on_use = True
        self._telemetry_closed = True

    def record_ready(self) -> None:
        """Fix the init duration at readiness, for an announcement made later."""
        self._measured_init_duration_ms()

    @property
    def terminal_seat(self) -> TerminalSeat | None:
        return self._terminal_seat

    async def seat_terminal(
        self, seat: TerminalSeat | None, *, record_now: bool
    ) -> None:
        """Make this the terminal's continue target, now or at its first message."""
        self._terminal_seat = seat
        self._seat_pending = seat is not None and not record_now
        if seat is not None and record_now:
            await seat.record(self.session_id)

    async def _record_pending_seat(self) -> None:
        if not self._seat_pending or self._terminal_seat is None:
            return
        self._seat_pending = False
        await self._terminal_seat.record(self.session_id)

    def _announce_first_use(self) -> None:
        if not self._announce_on_use:
            return
        self._announce_on_use = False
        self._telemetry_closed = False
        # A session started ahead of need opens for the user only now.
        self.start_session_hooks(SessionStartSource.STARTUP)
        task = self._experiments_task
        if task is None or task.done():
            self._emit_fresh_start_telemetry()
            return
        task.add_done_callback(lambda _task: self._emit_fresh_start_telemetry())

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
        if not self._ready_reported:
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
            has_outside_workdir_deny=outside_workdir_denied(config),
            nb_post_agent_scoped_hooks=self._context.post_agent_scoped_hooks_count,
            nb_post_agent_main_hooks=self._context.post_agent_main_hooks_count,
            nb_hooks_by_type={
                hook_type.value: self._context.hook_type_counts.get(hook_type, 0)
                for hook_type in HookType
            },
        )

    def _measured_init_duration_ms(self) -> int:
        """Init duration in ms, measured from this adapter's start to now.

        Records the duration exactly once (mirroring the legacy AgentLoop)
        so the ``vibe.ready`` event and the ``session/ready/wait`` response
        report the same value.
        """
        if self._init_duration_ms is None:
            self._init_duration_ms = int(
                (time.monotonic() - self._init_started_monotonic) * 1000
            )
        return self._init_duration_ms

    def _emit_ready_telemetry(self) -> None:
        """Emit ``vibe.ready`` once the session is usable (fresh start only).

        Carries the measured init duration (session start -> ready), matching
        the legacy semantic "time from process start to full initialization".
        """
        self._telemetry.send_ready(init_duration_ms=self._measured_init_duration_ms())

    def _emit_session_closed_telemetry(self) -> None:
        """Emit ``vibe.session_closed`` once for this session.

        Idempotent: session replacement routes through ``shutdown`` while process
        exit routes through the host adapter's shutdown, and only the first wins.
        """
        if self._telemetry_closed:
            return
        self._telemetry_closed = True
        self._telemetry.send_session_closed(
            reason="client_disconnected"
            if self._client_disconnected
            else self._end_reason.value
        )

    def _emit_worktree_created_telemetry(
        self, resolution: WorktreeResolution | None
    ) -> None:
        """Emit ``vibe.worktree.created`` for a checkout this session created.

        Only when copy-on-write creation was asked for: the event compares its
        outcomes and checkout times, and ``copy_on_write`` is null on nothing
        else.
        """
        prepared = None if resolution is None else resolution.prepared_worktree
        if prepared is None or not prepared.created or prepared.copy_on_write is None:
            return
        self._telemetry.send_telemetry_event(
            "vibe.worktree.created",
            {
                "copy_on_write": prepared.copy_on_write.value,
                "checkout_ms": prepared.checkout_ms,
            },
        )

    def _emit_session_branched_telemetry(
        self, *, source_session_id: str, root_session_id: str
    ) -> None:
        self._telemetry.send_telemetry_event(
            "vibe.session_branched",
            {
                "source_session_id": source_session_id,
                "new_session_id": self.session_id,
                "root_session_id": root_session_id,
            },
        )

    async def _cache_session_lineage(self) -> None:
        """Cache root affinity and parent telemetry ids from session state.

        The harness session handle does not expose them, so resolve them once per
        open before the session receives a turn. Best-effort: if the read fails or
        an older state has no root id, affinity falls back to this session's id.
        """
        with contextlib.suppress(Exception):
            result = await self._session.read(
                _harness_read_params(
                    SessionReadParams(
                        session_id=self.session_id, history=PageRequest(limit=1)
                    )
                )
            )
            session = result.snapshot.state.session
            self._root_session_id = getattr(session, "root_session_id", None)
            self._parent_session_id = session.parent_session_id

    def _is_ready(self) -> bool:
        """Whether background init (experiments and a held catalog fetch) has
        settled. Connector and MCP loads are not part of it: nothing waits on
        them.

        ``False`` while the deferred work is in flight, ``True`` once it
        completes (or when there is nothing to wait on). Readiness endpoints
        report this so clients can show an "Initializing" loader until
        ``wait_until_ready`` resolves.
        """
        # The org config fetch is not here: a session start finishes it before
        # the session exists. A held catalog fetch is included: its cap bounds
        # it.
        task = self._experiments_task
        if task is not None and not task.done():
            return False
        wait = self._model_catalog_wait
        return wait is None or wait.done

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

    def _cancel_connector_resolve_task(self) -> asyncio.Task[None] | None:
        task = self._connector_resolve_task
        self._connector_resolve_task = None
        # Followed again once the next load settles, under the new context.
        self._unfollow_connector_catalog()
        # A task cancelled before it ran never reaches its ``finally``.
        if self._connectors_loaded is not None:
            self._connectors_loaded.set()
        if task is not None and not task.done():
            task.cancel()
            return task
        return None

    def _cancel_mcp_discovery_task(self) -> asyncio.Task[None] | None:
        task = self._mcp_discovery_task
        self._mcp_discovery_task = None
        if self._mcp_discovered is not None:
            self._mcp_discovered.set()
        if task is not None and not task.done():
            task.cancel()
            return task
        return None

    def _cancel_pr_discovery_task(self) -> asyncio.Task[None] | None:
        task = self._pr_discovery_task
        self._pr_discovery_task = None
        if task is not None and not task.done():
            task.cancel()
            return task
        return None

    def _stop_watching_pull_requests(self) -> list[asyncio.Task[None]]:
        self._release_pull_requests()
        announcements = list(self._background_announcements)
        for task in announcements:
            task.cancel()
        return announcements

    async def _read_history_entries(self) -> list[PublicHistoryEntry]:
        """Read the latest projected history entries (bounded to 500)."""
        state = await self._read_page_state(self.session_id)
        return state.history or []

    def end_with(self, reason: SessionEndReason) -> None:
        """The session_end reason of this session's coming close."""
        self._end_reason = reason

    async def shutdown(self, *, end_reason: SessionEndReason | None = None) -> None:
        if self._closed:
            return
        # Before ``_closed``: a session_end hook reads nothing the close tears down,
        # and runs in the background. The app server waits for it on its way out.
        self.end_session_hooks(end_reason or self._end_reason)
        self._closed = True
        self._close_org_config()
        self._unfollow_connector_catalog()
        # Flush any completion whose snapshot never drove a final state update
        # before teardown closes the telemetry client.
        self._forward_request_sent()
        self._forward_malformed_tool_calls()
        # A move that never landed is undone before the worktrees are released
        # and the metadata saved, or resume would treat the session as moved.
        with contextlib.suppress(Exception):
            await self._session.drop_workspace_move()
        # Before the slower parts of the close, so a sweep running concurrently
        # cannot read this worktree as still occupied by a session that is on
        # its way out.
        for held in self._held_worktrees():
            self._worktrees.release(
                held,
                self.session_id,
                trust_store=self._context.harness_files.trust_store,
            )
        if self._relocation_trust_root is not None:
            self._context.harness_files.trust_store.revoke_session_trust(
                self._relocation_trust_root
            )
            self._relocation_trust_root = None
        self._release_deferred_events = None
        self._defer_event_flush = False
        if self._api_path is not None:
            self._api_path.release_events()
        # Kill any `!` command still running, so a long-lived subprocess cannot
        # outlive the session that spawned it.
        with contextlib.suppress(Exception):
            await self._host_shell.controller.close()
        await self._stop_background_work()
        # Cancel the in-flight eval before announcing the close, so its ``after``
        # callback can never emit new_session/ready after session_closed. The
        # deferred connector resolve goes with it: it reconfigures the session,
        # which is being torn down. Awaited here so they are settled before
        # teardown continues.
        self._cancel_turn_end_pr_discovery()
        for pending in (
            self._cancel_experiments_task(),
            self._cancel_model_catalog_task(),
            self._cancel_registry_sync(),
            self._cancel_connector_resolve_task(),
            self._cancel_mcp_discovery_task(),
            self._cancel_pr_discovery_task(),
            *self._stop_watching_pull_requests(),
            self._cancel_history_image_warning(),
        ):
            if pending is not None:
                with contextlib.suppress(BaseException):
                    await pending
        await self._cancel_deferred_setup_task()
        self._emit_session_closed_telemetry()
        with contextlib.suppress(Exception):
            await self._context.experiment_manager.aclose()
        try:
            await self._persist_session_metadata_best_effort()
            try:
                await self._scheduled_loops.persist()
            except ScheduledLoopStoreError as exc:
                logger.warning("Failed to persist scheduled loops", exc_info=exc)
            # The Session API transport's event pumps follow this session and
            # its loaded subagents, which can outlive it, so they stop here.
            await self._commands.close()
            await self._session.shutdown()
        finally:
            await self._telemetry.aclose()

    def _client_event_id(self, response: Accepted) -> int:
        """The client's event ID for a command reply.

        A Runtime reply names its own watermark. A reply that names none takes
        the last event ID this adapter published.
        """
        last = response.last_event_id
        return self._event_id if last is None else last

    def _cwd_path(self) -> Path:
        # resolve() stats every path component on the shared event loop.
        cwd = self.cwd
        if self._resolved_cwd is None or self._resolved_cwd[0] != cwd:
            self._resolved_cwd = (cwd, Path(cwd or Path.cwd()).expanduser().resolve())
        return self._resolved_cwd[1]

    async def enter_worktree(
        self,
        args: EnterWorktreeArgs,
        *,
        announce_cwd: Callable[[str, str], Awaitable[None]],
    ) -> EnteredWorktree:
        """Raise or re-enter a worktree, and move there at the next model step.

        The context's workspace slot moves with the Session, so every later
        derivation keeps the session where it moved. The session's roots become
        the worktrees it entered plus the directories it was opened with beyond
        its checkout: the checkout it leaves is reached with approval, like any
        directory outside the workspace.
        """
        # One move per step: a second call would race the first for the cwd
        # and leave the model with two results that cannot both be true.
        if self._entering_worktree or self._session.next_workspace != (
            self._session.workspace
        ):
            raise EnterWorktreeError(
                "This session is already moving into a worktree; call "
                "enter_worktree again on your next step if you need another"
            )
        self._entering_worktree = True
        try:
            return await self._enter_worktree(args, announce_cwd=announce_cwd)
        finally:
            self._entering_worktree = False

    async def _enter_worktree(
        self,
        args: EnterWorktreeArgs,
        *,
        announce_cwd: Callable[[str, str], Awaitable[None]],
    ) -> EnteredWorktree:
        previous_cwd = self._session.workspace.cwd
        if args.path is None:
            try:
                resolved = await self._worktrees.raise_for_agent(
                    previous_cwd,
                    args.name,
                    branch=args.branch,
                    prompt=self._user_request,
                    trust_store=self._worktree_trust_store,
                    copy_on_write=self.config.worktree_copy_on_write,
                )
            except GitError as exc:
                raise EnterWorktreeError(str(exc)) from exc
            prepared = resolved.prepared
            assert prepared is not None
            target, branch, repo_root = (
                resolved.cwd,
                prepared.branch,
                prepared.repo_root,
            )
            pending_hold = resolved.pending_hold
        else:
            resolved = None
            pending_hold = None
            target, branch, repo_root = await asyncio.to_thread(
                self._reentered_worktree, Path(args.path)
            )
        held_before = any(
            Path(w.path) == target for w in self._artifact_service.list_worktrees()
        )
        try:
            await asyncio.to_thread(
                self._worktrees.hold,
                target,
                self.session_id,
                pending_hold,
                self._joining_session_dir(),
            )
        except BaseException:
            if resolved is not None:
                await self._worktrees.discard(resolved)
            raise
        recorded: list[Path] = []

        async def dropped() -> None:
            # Otherwise a later move would list a worktree the session never
            # ran in as one it may write to without asking.
            for path in recorded:
                await self._artifact_service.forget_worktree(path)
            if not held_before:
                await asyncio.to_thread(
                    self._worktrees.release,
                    target,
                    self.session_id,
                    trust_store=self._worktree_trust_store,
                )

        try:
            left = await self._record_entered_worktrees(
                previous_cwd, (target, branch, repo_root), recorded
            )
        except BaseException:
            await dropped()
            raise
        entered = tuple(Path(w.path) for w in self._artifact_service.list_worktrees())
        workspace = SessionWorkspace(
            cwd=target,
            roots=tuple(dict.fromkeys((target, *entered, *self._opened_extra_roots()))),
            path_space=self._session.workspace.path_space,
        )

        async def moved(landed: SessionWorkspace) -> None:
            # The resolver first: it is the step that can fail, and a failure
            # here makes the Session roll the move back, with nothing to undo.
            self._context.permissions.move_to(landed.cwd, entered)
            if self._context.workspace is not None:
                self._context.workspace.workspace = landed
                self._context.workspace.entered = entered
            self._adapter_config = self._adapter_config.with_workspace(landed)
            try:
                await announce_cwd(self.session_id, str(landed.cwd))
            except Exception as exc:
                logger.warning("Failed to announce a worktree move", exc_info=exc)
            # A continued branch may already have a PR, and no command may run
            # here for the turn-end pass to catch it.
            self.schedule_pr_discovery()

        async def move_dropped() -> None:
            await dropped()
            # The tool already told the model it moved.
            self._unlanded_worktree = target

        # A direct runtime call: the Session API has no workspace move yet.
        self._session.move_workspace(workspace, on_moved=moved, on_dropped=move_dropped)
        return EnteredWorktree(
            path=target,
            branch=branch,
            created=args.path is None,
            previous_cwd=previous_cwd,
            previous_kept=left is not None,
            branched_off_from=(
                args.branch
                if args.path is None and args.branch not in {None, branch}
                else None
            ),
        )

    async def _restate_entered_worktree(self, kept: Sequence[object]) -> bool:
        """Tell the model where it runs when a rewind cut the move it made.

        The session stays where it is, but the ``enter_worktree`` result was
        the only place the model learned it moved; the system prompt still
        names the checkout it started in. Best effort: the rewind already
        happened.
        """
        if self._context.workspace is None or not self._context.workspace.moved:
            return False
        if any(
            isinstance(entry, PublicEffectEntry)
            and entry.detail.tool_name
            == f"{VIBE_TOOL_GROUP}.{ENTER_WORKTREE_TOOL_NAME}"
            for entry in kept
        ):
            return False
        cwd = self._context.workspace.workspace.cwd
        try:
            await self.inject_context(
                ContextInjectParams(
                    session_id=self.session_id,
                    input=[
                        TextContentBlock(
                            text=(
                                f"You are working in the git worktree {cwd}, not in "
                                "the user's checkout: commands and relative paths "
                                "run there."
                            )
                        )
                    ],
                )
            )
        except Exception as exc:
            logger.warning(
                "Failed to restate the worktree after a rewind", exc_info=exc
            )
            return False
        return True

    async def _record_entered_worktrees(
        self, previous_cwd: Path, target: tuple[Path, str, Path], recorded: list[Path]
    ) -> tuple[Path, str, Path] | None:
        """Record the worktree being left and the one entered, before the move
        lands, so a close in between still lets the hold go: release walks the
        recorded worktrees. Appends each newly recorded path to ``recorded``
        and returns the worktree left, if any.
        """
        left = await asyncio.to_thread(_left_worktree, previous_cwd)
        for path, branch, repo_root in (*([left] if left else []), target):
            if await self._artifact_service.record_worktree(
                path=path, branch=branch, repo_root=repo_root
            ):
                recorded.append(path)
        return left

    async def _worktree_dropped_on_close(self) -> Path | None:
        """The worktree a resumed session was told it entered but never ran in.

        A move dropped by the close before this open leaves no flag, only the
        ``enter_worktree`` result in history naming a directory the session is
        not in.
        """
        if not self._unlanded_worktree_candidate:
            return None
        try:
            target = _last_entered_worktree(await self._read_history_entries())
        except Exception as exc:
            logger.warning("Failed to read history for a dropped move", exc_info=exc)
            return None
        return None if target == self._session.workspace.cwd else target

    async def _report_unlanded_worktree(self) -> None:
        """Correct an ``enter_worktree`` result whose move never landed.

        Best effort, like the rewind note: the turn starts either way.
        """
        target = self._unlanded_worktree
        if target is None and not self._unlanded_worktree_checked:
            self._unlanded_worktree_checked = True
            target = await self._worktree_dropped_on_close()
        self._unlanded_worktree = None
        if target is None:
            return
        cwd = self._session.workspace.cwd
        if cwd == target:
            return
        try:
            await self.inject_context(
                ContextInjectParams(
                    session_id=self.session_id,
                    input=[
                        TextContentBlock(
                            text=(
                                f"Your move into the git worktree {target} did "
                                f"not take effect: you are still in {cwd}. Treat "
                                "it as never entered, and follow the worktree "
                                "rules again before editing files."
                            )
                        )
                    ],
                )
            )
        except Exception as exc:
            logger.warning(
                "Failed to report a worktree move that never landed", exc_info=exc
            )

    def _reentered_worktree(self, requested: Path) -> tuple[Path, str, Path]:
        target = requested.expanduser().resolve()
        recorded = {
            Path(w.path): w for w in self._artifact_service.list_worktrees()
        }.get(target)
        managed = ManagedWorktree.at(target)
        if recorded is None or managed is None or not target.is_dir():
            raise EnterWorktreeError(
                f"{target} is not a worktree this session can re-enter; omit "
                "`path` to create one"
            )
        return target, recorded.branch, Path(recorded.repo_root)

    def _opened_extra_roots(self) -> tuple[Path, ...]:
        """The directories the session was opened with beyond its checkout."""
        opened = self._session.workspace
        entered = {Path(w.path) for w in self._artifact_service.list_worktrees()}
        if self._context.workspace is not None and self._context.workspace.moved:
            return tuple(root for root in opened.roots if root not in entered)
        return tuple(root for root in opened.roots if root != opened.cwd)

    def _held_worktrees(self) -> list[Path]:
        """Where this session holds a worktree: its cwd and every one it entered."""
        held = [Path(w.path) for w in self._artifact_service.list_worktrees()]
        if self.cwd is not None:
            held.append(Path(self.cwd))
        return list(dict.fromkeys(held))

    def _mention_roots(self) -> tuple[Path, ...]:
        """The roots a mention may be inlined from, as the file tools see them."""
        return self._adapter_config.workspace.roots

    def _workspace_roots(self) -> tuple[Path, ...]:
        """The session's working directory and access roots, as the Harness sees them."""
        workspace = self._adapter_config.workspace
        return (workspace.cwd, *workspace.roots)

    def _session_display_roots(self) -> tuple[Path, ...]:
        """Where this session's displayed file paths are made relative."""
        return _display_roots(self.cwd, self._workspace_roots())

    def _session_dir(self) -> Path:
        return Path(self._storage_root) / "unified" / self.session_id

    # Where the session will be saved, even before its first save.
    def _joining_session_dir(self) -> Path | None:
        if not self._context.session_logging_enabled:
            return None
        return self._session_dir()

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
        prompt = prepare_prompt_from_context(
            params.message, cwd=self._cwd_path(), session_dir=session_dir
        )
        skills = self._mentioned_skill_count(params.message)
        if not skills:
            return prompt
        mentions = prompt.mentions
        return prompt.model_copy(
            update={
                "mentions": mentions.model_copy(
                    update={
                        "count": mentions.count + skills,
                        "context_types": {**mentions.context_types, "skill": skills},
                    }
                )
            }
        )

    def _mentioned_skill_count(self, text: str) -> int:
        mentioned = set(mentioned_skill_names(text))
        return sum(
            1
            for name, _body in self._resolved_invoked_skills(text)
            if name.casefold() in mentioned
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

    async def _invoked_skill_blocks(
        self, blocks: list[ContentBlock]
    ) -> list[tuple[str, ContentBlock]]:
        resolved = self._resolved_invoked_skills(_text_from_blocks(blocks))
        if not resolved:
            return []
        loaded = await self._skills_already_loaded([name for name, _ in resolved])
        return [
            (
                name,
                TextContentBlock(
                    text=already_loaded_message(name) if name in loaded else body
                ),
            )
            for name, body in resolved
        ]

    def _resolved_invoked_skills(self, text: str) -> list[tuple[str, str]]:
        """The ``(name, body)`` of each skill the prompt invokes, in prompt order.

        A leading ``/name`` and every later ``/name`` mention count; unknown names,
        skills the user may not invoke, and ``/loop`` prompts invoke nothing.
        """
        if is_loop_prompt(text):
            return []
        resolved: list[tuple[str, str]] = []
        for typed in invoked_skill_names(text):
            skill = next(
                (s for s in self._runtime.skills if s.name.casefold() == typed), None
            )
            if skill is None or not skill.user_invocable:
                continue
            # A skill Core rejected is still in the client-facing list, and it has
            # no payload. Injecting nothing beats injecting a name with no body.
            body = self._skills.get(skill.name)
            if body is not None:
                resolved.append((skill.name, body))
        return resolved

    async def _skills_already_loaded(self, names: Sequence[str]) -> set[str]:
        """Report which skills this conversation already carries the body of.

        Core owns the model-visible history, so the check runs over the public
        projection: a body this adapter injected shows up as the marker its
        renderer opens with, and one the model loaded itself shows up as a
        ``skill`` effect. Only a resolved invocation gets this far, so the read
        costs a page on the turns legacy also scans for.
        """
        history = (await self._read_page_state(self.session_id)).history or ()
        return {
            name
            for name in names
            if any(
                _entry_loaded_skill(entry, name=name, marker=skill_content_marker(name))
                for entry in history
            )
        }

    async def _prepared_turn_params[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT, *, inject_skill: bool
    ) -> ParamsT:
        user_display_content = without_skill_invocation_display(
            params.user_display_content
        )
        invoked = (
            await self._invoked_skill_blocks(params.message) if inject_skill else []
        )
        try:
            # Ahead of the mentioned files, so an image description is steered
            # by what the user typed rather than by an inlined file's contents.
            params = await self._with_described_images(params)
            params = await self._with_mentioned_file_blocks(params)
        except PromptPreparationError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INVALID_PARAMS, str(exc)
            ) from exc
        return prepare_loop_turn(
            params.model_copy(
                update={
                    "message": [*params.message, *(block for _name, block in invoked)],
                    "user_display_content": with_skill_invocation_display(
                        user_display_content, [name for name, _block in invoked]
                    ),
                }
            )
        )

    async def _prepared_queue_params[
        ParamsT: TurnEnqueueParams | TurnQueueEditSaveParams | TurnQueueReplaceParams
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
        invoked_skills: list[tuple[str, str]] = []
        if user_index is not None:
            entries = list(params.entries)
            user_entry = entries[user_index]
            user_entry = user_entry.model_copy(
                update={
                    "annotations": user_entry.annotations.model_copy(
                        update={
                            "vibe_user_display_content": (
                                without_skill_invocation_display(
                                    user_entry.annotations.vibe_user_display_content
                                )
                            )
                        }
                    )
                }
            )
            entries[user_index] = user_entry
            params = params.model_copy(update={"entries": entries})
            # Queue input must stay stable while earlier turns change history,
            # so store the full body rather than the immediate-turn dedup hint.
            invoked_skills = self._resolved_invoked_skills(
                _text_from_session_blocks(user_entry.content)
            )

        params = await self._with_mentioned_file_entries(params)
        if user_index is None or not invoked_skills:
            return prepare_loop_queue(params)

        entries = list(params.entries)
        user_entry = entries[user_index]
        if not isinstance(user_entry, TurnUserInputEntry):
            raise RuntimeError("Queued user entry changed while preparing input")
        entries[user_index] = user_entry.model_copy(
            update={
                "content": [
                    *user_entry.content,
                    *(
                        SessionTextContentBlock(text=body)
                        for _name, body in invoked_skills
                    ),
                ],
                "annotations": user_entry.annotations.model_copy(
                    update={
                        "vibe_user_display_content": with_skill_invocation_display(
                            user_entry.annotations.vibe_user_display_content,
                            [name for name, _body in invoked_skills],
                        )
                    }
                ),
            }
        )
        return prepare_loop_queue(params.model_copy(update={"entries": entries}))

    async def _with_described_images[ParamsT: TurnStartParams | TurnSteerParams](
        self, params: ParamsT
    ) -> ParamsT:
        described = await self._image_describer.described_blocks(params.message)
        if described is params.message:
            return params
        return params.model_copy(update={"message": described})

    async def _with_described_image_entries[
        ParamsT: TurnEnqueueParams | TurnQueueEditSaveParams | TurnQueueReplaceParams
    ](self, params: ParamsT) -> ParamsT:
        # Core dequeues into a turn on its own, so the adapter gets no second
        # look at this content: an image left queued reaches the provider.
        entries = list(params.entries)
        changed = False
        for index, entry in enumerate(entries):
            if not isinstance(entry, TurnUserInputEntry | TurnContextInputEntry):
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
        ParamsT: TurnEnqueueParams | TurnQueueEditSaveParams | TurnQueueReplaceParams
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

    async def _history_list(
        self, params: SessionHistoryListParams
    ) -> SessionHistoryListResponse:
        return await self._history_windows.list(
            params, partial(self._read_page_state, params.session_id)
        )

    async def _context_breakdown(self, session_id: str) -> ContextBreakdown:
        """`/context`: the newest model input split by category against the
        API reading of that call.

        A subagent can run on its own model, so its window and threshold are
        read from its live Core; one that is not loaded falls back to the
        session's. A reopened session reads the split saved by the process
        that made its last call, which is the call its saved usage measured.
        """
        state = await self._read_page_state(session_id, history_limit=0)
        model_facts = self._running.core_config.settings.context.model_facts
        if session_id != self.session_id and self._host is not None:
            model_facts = self._host.session_model_facts(session_id) or model_facts
        requests = self._context.model_requests
        payload = requests.latest(session_id)
        if payload is None:
            payload = await asyncio.to_thread(requests.saved, session_id)
        return build_context_breakdown(
            state,
            payload=payload,
            context_window=model_facts.context_window,
            compaction_threshold=model_facts.compaction_threshold,
        )

    async def _read_state_containing(self, entry_id: str) -> PublicSessionState:
        """Widen the read until it holds `entry_id` or the whole history."""
        state, _, _, _ = await _read_widening_window(
            partial(self._read_page_state, self.session_id),
            enough=lambda history: any(entry.id == entry_id for entry in history),
        )
        return state

    async def _read_page_state(
        self, session_id: str, history_limit: int = _HISTORY_PAGE_WINDOW
    ) -> PublicSessionState:
        params = HarnessSessionReadParams(
            session_id=session_id, history_limit=history_limit + 1
        )
        if session_id != self.session_id:
            return (
                await self._read_child_response(params, probe_limit=history_limit)
            ).state
        result = await self._session.read(params)
        return _read_response(
            _without_probe(result.snapshot, history_limit),
            self.cwd,
            metadata=self._metadata,
            pins=self._live_session_pins(),
            workspace_roots=self._workspace_roots(),
            scheduled_loops=self._scheduled_loops.snapshot(),
            mcp_apps=self._mcp_apps,
            started_by_session_id=self._session.started_by_session_id,
        ).state

    async def seed_persisted_child_states(self) -> None:
        """Seed child summaries from the root's persisted child records.

        A resume never replays ``child_session_registered`` (children bind lazily),
        so without this the subagent list stays empty until each child is used
        again. Fresh starts and forks have no persisted children and no-op.
        """
        if self._host is None:
            return
        try:
            records = [
                record
                for record in self._host.child_session_records(self.session_id)
                if record.state.type in {"running", "idle", "turn_failed"}
                and record.child_session_id not in self._child_states
            ]
        except Exception as exc:
            # List restoration must never fail the resume itself.
            logger.warning(
                "Skipping persisted child summaries for %s",
                self.session_id,
                exc_info=exc,
            )
            return
        await asyncio.gather(*(self._seed_child_state(record) for record in records))

    async def _seed_child_state(self, record: ChildSessionRecord) -> None:
        child_id = record.child_session_id
        try:
            # Only the state fields matter here, so a one-entry page is enough.
            state = (
                await self._read_child_response(
                    HarnessSessionReadParams(session_id=child_id, history_limit=1)
                )
            ).state
        except Exception as exc:
            logger.warning(
                "Skipping unreadable persisted child session %s", child_id, exc_info=exc
            )
            return
        self._child_states[child_id] = state
        self._child_identities[child_id] = _ChildIdentity(
            name=record.agent_name,
            agent_type=record.agent_type or "subagent",
            spawned_at=state.session.created_at,
        )

    async def _read_child_response(
        self, params: HarnessSessionReadParams, *, probe_limit: int | None = None
    ) -> SessionReadResponse:
        if self._host is None:
            state = self._child_states.get(params.session_id)
            if state is None:
                raise SessionBackendError(
                    ProtocolErrorCode.NOT_FOUND,
                    f"Session not found: {params.session_id}",
                )
            return SessionReadResponse(state=state, last_event_id=state.event_id)
        result = await _harness_call(self._host.read(params))
        metadata = await asyncio.to_thread(
            _read_unified_session_metadata,
            _unified_session_dir(self._storage_root, params.session_id),
        )
        snapshot = (
            result.snapshot
            if probe_limit is None
            else _without_probe(result.snapshot, probe_limit)
        )
        # Child sessions have no loop store of their own, and no starter.
        return _read_response(
            snapshot,
            result.cwd,
            event_id=self._event_id,
            metadata=metadata,
            workspace_roots=self._workspace_roots(),
            scheduled_loops=(),
            mcp_apps=None,
            started_by_session_id=None,
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
        self._event_id = max(self._event_id, snapshot.watermark)
        return self._project_read(snapshot)

    def _project_read(self, snapshot: HarnessSessionSnapshot) -> SessionReadResponse:
        response = self._root_read_response(snapshot)
        state = self._state_with_child_summaries(response.state)
        # The Harness carries open callbacks outside persisted history; Vibe's
        # public replica carries them as entries. A resync must retain them.
        state = _with_callbacks_in_history(state)
        return response.model_copy(update={"state": state})

    def _root_read_response(
        self,
        snapshot: HarnessSessionSnapshot,
        *,
        event_id: int | None = None,
        projected_history: _ProjectedHistory | None = None,
    ) -> SessionReadResponse:
        """The root's read response for ``snapshot``, with this session's metadata."""
        return _read_response(
            snapshot,
            self.cwd,
            event_id=self._event_id if event_id is None else event_id,
            metadata=self._metadata,
            pins=self._live_session_pins(),
            worktree=self._public_worktree(),
            workspace_roots=self._workspace_roots(),
            scheduled_loops=self._scheduled_loops.snapshot(),
            projected_history=projected_history,
            mcp_apps=self._mcp_apps,
            started_by_session_id=self._session.started_by_session_id,
        )

    def _public_worktree(self) -> PublicSessionWorktree | None:
        """The worktree this session moved into, once the deferred setup lands.

        The deferred preparation runs behind `session/start`, so the field is
        None until it finishes and stays None on a session that asked for no
        worktree. Only the live backend can say this; a listed or resumed
        session keeps the default (its runs know their own worktrees).
        """
        resolution = self._deferred_setup_result
        prepared = resolution.prepared_worktree if resolution is not None else None
        if prepared is None:
            return None
        return PublicSessionWorktree(
            name=prepared.name,
            branch=prepared.branch,
            path=str(prepared.root),
            created=prepared.created,
        )

    async def _translated_events(  # noqa: PLR0915
        self, subscription: HarnessSessionSubscription
    ) -> AsyncIterator[SessionBackendEvent]:
        """The client's events, from the Runtime session's own events."""
        await self._mark_events_subscribed()
        try:
            async for event in self._with_host_events(subscription.events):
                previous = self._translated_state
                if previous is None:
                    raise RuntimeError("Unified event stream has no translated state")
                if isinstance(event, SessionBackendEvent):
                    yield event
                    await self._settle_host_event()
                    continue
                event_type = event.get("type")
                if event_type == "child_session_registered":
                    registered, previous, watermark = self._register_child_event(
                        event, previous
                    )
                    self._translated_state = previous
                    for child_event in registered:
                        yield child_event
                    await self._mark_harness_event_observed(watermark)
                    continue
                if event_type == "child_session_event":
                    translated, previous, watermark = await self._translate_child_event(
                        event, subscription.snapshot.history_limit, previous
                    )
                    self._translated_state = previous
                    for child_event in translated:
                        yield child_event
                    await self._mark_harness_event_observed(watermark)
                    continue
                signal = await self._signal_event(
                    cast(dict[str, object], event), session_id=self.session_id
                )
                if signal is not None:
                    translated_signal, watermark = signal
                    yield translated_signal
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
                    previous = previous.model_copy(
                        update={"event_id": self._event_id, "turn_queue": queue}
                    )
                    self._translated_state = previous
                    yield queue_event
                    await self._mark_harness_event_observed(watermark)
                    continue
                translated, previous, watermark = await self._root_state_update_events(
                    event, previous, subscription.snapshot.history_limit
                )
                # Publish metadata mutations against this state while events are
                # yielded. Otherwise a concurrent mark-as-seen can be overwritten
                # when this generator resumes after the yield.
                self._translated_state = previous
                async for backend_event in self._with_turn_start_effects(translated):
                    yield backend_event
                await self._mark_harness_event_observed(watermark)
        finally:
            await self._finish_event_stream()

    async def _mark_events_subscribed(self) -> None:
        async with self._events_condition:
            self._events_subscribed = True
            self._events_condition.notify_all()

    async def _finish_event_stream(self) -> None:
        # Nothing is left to forward what the Host queued, and a later
        # subscription is a different stream -- so drop it rather than
        # replay it there, and release whoever is waiting on it.
        while not self._host_shell.queue.empty():
            _ = self._host_shell.queue.get_nowait()
        async with self._events_condition:
            self._events_subscribed = False
            self._notices_subscribed = False
            self._host_shell.pending = 0
            for observed in self._pending_reads:
                if not observed.done():
                    observed.set_exception(
                        SessionBackendError(
                            ProtocolErrorCode.STALE_CURSOR,
                            "Session event stream ended before the read snapshot",
                        )
                    )
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
        so an event with no Harness event behind it -- a manual `!` command's
        ``shell`` effect, or a notice of the Host's own -- has nothing to ride
        out on. Merging here keeps it on the one stream, in the order the Host
        produced it, without putting a Host-authored entry into Core's history.
        """
        pending_host: asyncio.Task[SessionBackendEvent] | None = None
        pending_harness: asyncio.Task[dict[str, Any]] | None = None
        try:
            while True:
                # An event queued before this stream started (a recovered turn's
                # `turn/started`) goes out before the next harness event.
                if pending_host is None and not self._host_shell.queue.empty():
                    yield self._host_shell.queue.get_nowait()
                    continue
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

    async def _with_turn_start_effects(
        self, translated: Sequence[SessionBackendEvent]
    ) -> AsyncIterator[SessionBackendEvent]:
        """Yield the client's events of one state update, applying what a
        ``TurnStarted`` means to the session: whether the user started it (see
        ``_note_who_started_the_turn``) and the bump of a promoted queued turn.
        Both event streams, direct and Session API, yield their live updates
        through here.
        """
        for backend_event in translated:
            self._note_who_started_the_turn(backend_event.event)
            yield backend_event
            await self._bump_promoted_queued_turn(backend_event.event)

    def _note_who_started_the_turn(self, app_event: AppServerEvent) -> None:
        """A turn the user queued resets the hops a turn another session's
        message started counted; only the user edits the queue.
        """
        if not isinstance(app_event, TurnStarted):
            return
        if app_event.turn.queue_item_id is None:
            return
        if app_event.turn.id == self._turn_decided_who_started:
            return
        self._turn_decided_who_started = app_event.turn.id
        self._message_hops = 0

    async def _bump_promoted_queued_turn(self, app_event: AppServerEvent) -> None:
        """Advance bumped_at when a queued turn is promoted into a real turn.

        Queue promotion starts a user turn inside the Harness without going
        through ``start_turn``, so the request path cannot bump the session.
        The first TurnStarted carrying the armed queue item id is that start:
        land the bump there, exactly once per promoted turn.
        """
        if not self._promoted_queue_item_ids or not isinstance(app_event, TurnStarted):
            return
        queue_item_id = app_event.turn.queue_item_id
        if queue_item_id is None or queue_item_id not in self._promoted_queue_item_ids:
            return
        if queue_item_id in self._bumped_queue_item_ids:
            return
        self._promoted_queue_item_ids.discard(queue_item_id)
        self._bumped_queue_item_ids.add(queue_item_id)
        metadata = await self._persist_accepted_user_activity_metadata()
        if metadata is not None:
            self._publish_session_metadata(metadata)

    async def _root_state_update_events(
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
        return await self._state_update_events(event, previous, history_limit)

    def _translate_snapshot_update(
        self, previous: PublicSessionState, current: PublicSessionState
    ) -> tuple[list[SessionBackendEvent], PublicSessionState]:
        snapshot_state_changed = (
            previous.retrying != current.retrying
            or previous.background_processes != current.background_processes
            or previous.session.loops != current.session.loops
        )
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
        if not snapshot_state_changed:
            return translated, current

        self._event_id += 1
        current = current.model_copy(update={"event_id": self._event_id})
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
        state = self._child_read_state(child_session_id, snapshot)
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
            translated = [authorization]
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
                # Deprecated: "subagent.spawn" is the pre-rename spelling of
                # "agents.spawn", kept only so persisted sessions replay.
                and detail.tool_name in {"agents.spawn", "subagent.spawn"}
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
            # Deprecated: "subagent.stop" was retired without a replacement;
            # this branch only replays sessions persisted before then.
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
            # The Harness unloads a stopped child, which would leave this state
            # the only holder of its entries.
            if self._api_path is not None:
                self._api_path.forget_child(child_id)
            self._child_history_projections.pop(child_id, None)

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

    def _child_read_state(
        self,
        child_session_id: str,
        snapshot: HarnessSessionSnapshot,
        *,
        projected_history: _ProjectedHistory | None = None,
    ) -> PublicSessionState:
        """A subagent's public state for ``snapshot``, with its saved metadata."""
        metadata = _read_unified_session_metadata(
            _unified_session_dir(self._storage_root, child_session_id)
        )
        return _read_response(
            snapshot,
            self.cwd,
            event_id=self._event_id,
            metadata=metadata,
            workspace_roots=self._workspace_roots(),
            scheduled_loops=(),
            projected_history=projected_history,
            mcp_apps=None,
            started_by_session_id=None,
        ).state

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

    async def _state_update_events(
        self,
        event: Mapping[str, object],
        previous: PublicSessionState,
        history_limit: int,
    ) -> tuple[list[SessionBackendEvent], PublicSessionState, int]:
        current, watermark = self._updated_state(
            event,
            history_limit,
            cwd=self.cwd,
            metadata=self._metadata,
            projections=self._history_projections,
            scheduled_loops=self._scheduled_loops.snapshot(),
            started_by_session_id=self._session.started_by_session_id,
        )
        # Process shell effects collected by the sync state update above: the
        # detector is async (glab api), so it cannot run inside _updated_state.
        await self._drain_pending_shell_effects()
        self._rewind_snapshots.observe(current)
        current = await self._record_unseen_transition(previous, current)
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
            projections=self._child_history_projections.setdefault(
                previous.session.id, _HistoryProjections()
            ),
            record_root_telemetry=False,
            started_by_session_id=None,
        )
        return current

    def _updated_state(
        self,
        event: Mapping[str, object],
        history_limit: int,
        *,
        cwd: str | None,
        metadata: SessionMetadata | None,
        projections: _HistoryProjections,
        record_root_telemetry: bool = True,
        scheduled_loops: Sequence[ScheduledPrompt] = (),
        started_by_session_id: str | None,
    ) -> tuple[PublicSessionState, int]:
        if event.get("type") != "session_state_updated":
            _reject(f"the Harness session event {event.get('type', event)!r}")
        raw_state = event.get("state")
        if not isinstance(raw_state, dict):
            _reject("a Harness session update without state")
        watermark = _required_event_id(event, "session")
        state = _published_harness_state(raw_state)
        if record_root_telemetry:
            self._record_root_update(state)
        current = _read_response(
            HarnessSessionSnapshot(
                state=state, history_limit=history_limit, watermark=watermark
            ),
            cwd,
            event_id=self._event_id,
            metadata=metadata,
            projections=projections,
            workspace_roots=self._workspace_roots(),
            scheduled_loops=scheduled_loops,
            # A subagent names its connector routes in its own Runtime.
            mcp_apps=self._mcp_apps if record_root_telemetry else None,
            started_by_session_id=started_by_session_id,
        ).state
        self._track_context_tokens(current)
        return current, watermark

    def _record_root_update(self, state: HarnessPublicSessionState) -> None:
        """Record what a root update's entries report: telemetry and shell effects."""
        if state.latest_turn is not None:
            self._root_turn_ids.add(state.latest_turn.id)
        self._record_tool_telemetry(state)
        self._record_compaction_telemetry(state)
        self._collect_pending_shell_effects(state)
        self._forward_request_sent()
        self._forward_malformed_tool_calls()

    def _track_context_tokens(self, state: PublicSessionState) -> None:
        """Track the context size so the next compaction can report what it replaced.

        A compaction's own snapshot reads this, pre-reset, value. A missing
        measurement means "keep the last reading" (``_context_tokens`` returns
        None); a real compaction resets to 0, which is a value, not None.
        """
        context_tokens = _context_tokens(state.session.context_usage)
        if context_tokens is not None:
            self._context_tokens_before = context_tokens

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
            if isinstance(app_event, TurnCompleted):
                # Phase 4: refresh the session's PR artifacts when a turn
                # completes. Non-blocking: the pass runs as a background task
                # and its results arrive via artifacts/updated.
                if app_event.turn.session_id == self.session_id:
                    self.refresh_pull_requests(full=True)
                    self._schedule_turn_end_pr_discovery()
                self._notify_turn_ended(app_event.turn, current)
                self._record_turn_failure(app_event.turn)
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
        return translated, current.model_copy(update={"event_id": self._event_id})

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

    def _seed_artifact_effects(self, state: HarnessPublicSessionState) -> None:
        """Mark a resumed session's terminal shell effects as already processed.

        Mirrors ``_seed_tool_telemetry``: without this, a resume re-scans the
        whole history and re-detects PRs the session already recorded (the
        service's own dedupe would absorb them, but the scan is wasted work
        and a notification would fire for an artifact created before the
        resume only if the persisted record were lost).
        """
        for raw_entry in state.history.entries:
            effect = _terminal_shell_effect(raw_entry)
            if effect is not None:
                self._processed_effect_ids.add(effect.effect_id)

    def _pr_monitoring_enabled(self) -> bool:
        return bool(self._context.config_orchestrator.config.enable_pr_monitoring)

    def schedule_pr_discovery(self, *, every_worktree: bool = True) -> None:
        """Kick off background PR/MR discovery (Phase 2).

        Runs at session load and again whenever the session moves to another
        checkout, over the current checkout and every worktree the session
        entered before; ``every_worktree=False`` covers the current checkout
        only. Non-blocking: the task runs in parallel with session use
        and the created artifacts arrive later via ``artifacts/created``. A
        call while a pass runs reruns it once that pass ends, since the
        session may have moved after the pass read its cwd. Local
        sessions only — a sandboxed session's checkout is not on this disk,
        so there is nothing to discover from. Gated on the same
        ``enable_pr_monitoring`` setting as Phase 1 detection.
        """
        if self._closed:
            return
        if not self._pr_monitoring_enabled():
            return
        if self._adapter_config.sandbox is not None:
            return
        if self.cwd is None or self._setup_abandoned:
            return
        if self._setup_in_progress():
            # The session still sits in the checkout it was started from; the
            # setup schedules discovery once the session moves into its
            # worktree, whose branch is the one the session's PRs are on.
            self._pr_discovery_after_setup = True
            return
        self._pr_discovery_every_worktree |= every_worktree
        task = self._pr_discovery_task
        if task is not None and not task.done():
            self._pr_discovery_rerun = True
            return
        self._pr_discovery_task = asyncio.create_task(
            self._discover_pull_requests(), name="vibe-pr-discovery"
        )

    def _schedule_turn_end_pr_discovery(self) -> None:
        # Only flags and a timer on the loop: the git and gh work runs in
        # discovery's worker thread, and a branch that already has a PR is
        # skipped there before gh is spawned.
        if (
            not self._shell_ran_since_pr_discovery
            or self._turn_end_pr_discovery_timer is not None
        ):
            return
        loop = asyncio.get_running_loop()
        last = self._turn_end_pr_discovery_at
        delay = (
            0.0
            if last is None
            else last + _TURN_END_PR_DISCOVERY_INTERVAL_S - loop.time()
        )
        if delay > 0:
            self._turn_end_pr_discovery_timer = loop.call_later(
                delay, self._run_turn_end_pr_discovery
            )
            return
        self._run_turn_end_pr_discovery()

    def _run_turn_end_pr_discovery(self) -> None:
        self._turn_end_pr_discovery_timer = None
        self._shell_ran_since_pr_discovery = False
        self._turn_end_pr_discovery_at = asyncio.get_running_loop().time()
        # Frequent, so the current checkout only: a PR a command creates
        # elsewhere is read from its output, and earlier worktrees are covered
        # at load and after each move.
        self.schedule_pr_discovery(every_worktree=False)

    def _cancel_turn_end_pr_discovery(self) -> None:
        timer = self._turn_end_pr_discovery_timer
        self._turn_end_pr_discovery_timer = None
        if timer is not None:
            timer.cancel()

    async def _discover_pull_requests(self) -> None:
        """Discover open PRs/MRs for the session's branch.

        Background task; never blocks the load path and never raises — a
        failure is logged and discovery is skipped. The service skips a
        branch the session already holds a PR for.
        """
        while True:
            self._pr_discovery_rerun = False
            every_worktree = self._pr_discovery_every_worktree
            self._pr_discovery_every_worktree = False
            await self._discover_pull_requests_once(every_worktree=every_worktree)
            if self._closed or not self._pr_discovery_rerun:
                break
        # Keep the session's PRs fresh while it is open, whether or not
        # discovery created anything; a pass after a move adds its PRs.
        self._watch_pull_requests()

    async def _discover_pull_requests_once(self, *, every_worktree: bool) -> None:
        cwd = self.cwd
        if cwd is None:
            return
        current = Path(cwd)
        await self._discover_pull_requests_in(current)
        if not every_worktree:
            return
        # A PR can be opened on an earlier worktree's branch after the session
        # left it, so a full pass covers every worktree the session holds.
        held = [path for path in self._held_worktrees() if path != current]
        left = await asyncio.to_thread(lambda: [p for p in held if p.is_dir()])
        for checkout in left:
            if self._closed:
                return
            await self._discover_pull_requests_in(checkout)

    async def _discover_pull_requests_in(self, checkout: Path) -> None:
        try:
            created = await self._artifact_service.discover_and_create(checkout)
        except Exception:
            logger.warning(
                "PR discovery failed unexpectedly",
                extra={"session_id": self.session_id},
                exc_info=True,
            )
            return
        for artifact in created:
            if self._closed:
                return
            try:
                # Keep the adapter's metadata in step with the service so the
                # next metadata persist carries the artifacts over (same
                # update as _drain_pending_shell_effects).
                if self._metadata is not None:
                    self._metadata = self._metadata.model_copy(
                        update={"artifacts": self._artifact_service.list_artifacts()}
                    )
                await self._emit_artifact_created(artifact)
            except Exception:
                # One artifact's announcement failing (e.g. the client is
                # gone) must not drop the remaining artifacts or the task.
                logger.warning(
                    "Failed to announce discovered artifact",
                    extra={"session_id": self.session_id},
                    exc_info=True,
                )

    def _watch_pull_requests(self) -> None:
        """Keep the session's PRs/MRs fresh in the process's pull request
        cache, and announce each new read as ``artifacts/updated``.

        Local sessions only, gated on ``enable_pr_monitoring``.
        """
        if self._closed or not self._pr_monitoring_enabled():
            return
        if self._adapter_config.sandbox is not None:
            return
        cache = pull_request_cache()
        if not self._watching_pull_requests:
            self._watching_pull_requests = True
            cache.own(self, self._pull_request_read, attached=True)
        cache.watch(self, self.session_id, self._artifact_service.watched())

    def _release_pull_requests(self) -> None:
        if self._watching_pull_requests:
            self._watching_pull_requests = False
            pull_request_cache().release(self)

    def refresh_pull_requests(self, *, full: bool) -> None:
        """Read the session's PRs/MRs at once: after a turn, in full (it may
        have changed them); when the user asks, after the cheap check.

        When ``enable_pr_monitoring`` was turned off since the watch started
        (an org config or a reload), the watch is dropped instead, so the
        cache stops polling the provider for this session; once it is back
        on, the watch starts again here.
        """
        if not self._pr_monitoring_enabled():
            self._release_pull_requests()
            return
        if not self._watching_pull_requests:
            self._watch_pull_requests()
            if not self._watching_pull_requests:
                return
        pull_request_cache().refresh(
            (key for key, _details in self._artifact_service.watched()), full=full
        )

    def _pull_request_read(self, update: Read) -> None:
        read = update.entry
        artifact = next(
            (
                artifact
                for artifact in self._artifact_service.list_artifacts()
                if artifact.external_key == read.external_key
            ),
            None,
        )
        if artifact is None or self._closed:
            return
        # Telemetry counts this process's reads only, not their echoes in
        # the other processes.
        telemetry = self._session_telemetry if update.local else None
        if isinstance(read, ReadFailure):
            if telemetry is not None:
                telemetry.record_artifact_sync_failed(
                    provider=artifact.provider,
                    trigger="poll",
                    error_type=read.error_type,
                )
            return
        if update.changed and telemetry is not None:
            telemetry.record_artifact_synced(
                provider=artifact.provider, trigger="poll", changed=True
            )
        if update.changed and read.snapshot.state in {"merged", "closed"}:
            # Discovery skips a branch while its PR is open, and the turn-end
            # pass may have run before this read: a new PR on the branch
            # would wait for the next turn that runs a command.
            self.schedule_pr_discovery()
        task = asyncio.create_task(self._emit_artifact_updated(artifact))
        self._background_announcements.add(task)
        task.add_done_callback(self._background_announcements.discard)

    def _collect_pending_shell_effects(self, state: HarnessPublicSessionState) -> None:
        """Queue newly terminal shell effects for artifact detection.

        Runs inside the sync state update next to the tool telemetry, so
        only cheap dict extraction happens here — the regex matching and the
        glab api resolution stay in the async drain. Skipped entirely while
        ``enable_pr_monitoring`` is off, and for sandboxed sessions: like
        discovery and watching, the provider reads would run on the host.
        """
        if not self._pr_monitoring_enabled():
            return
        if self._adapter_config.sandbox is not None:
            return
        for raw_entry in state.history.entries:
            effect = _terminal_shell_effect(raw_entry)
            if effect is None or effect.effect_id in self._processed_effect_ids:
                continue
            self._processed_effect_ids.add(effect.effect_id)
            self._pending_shell_effects.append(effect)
            self._shell_ran_since_pr_discovery = True

    async def _drain_pending_shell_effects(self) -> None:
        """Detect PR/MR artifacts in queued shell effects and notify clients.

        Detection is best-effort: any failure (corrupted meta.json, unexpected
        detector error) is logged and skipped so session event processing is
        never broken by artifact monitoring.
        """
        if not self._pending_shell_effects:
            return
        pending = self._pending_shell_effects
        self._pending_shell_effects = []
        if not self._pr_monitoring_enabled():
            return
        for effect in pending:
            try:
                artifacts = await self._artifact_service.on_shell_effect(
                    effect.command,
                    effect.stdout,
                    effect.stderr,
                    effect.status,
                    # Attribution approximation: the most recent user message,
                    # not the effect's own turn (the queue carries no turn id).
                    message_id=self._last_client_message_id,
                    # A reopen confirmation carries no URL: the missing
                    # coordinates resolve from the checkout's remote.
                    cwd=self.cwd,
                )
            except Exception:
                logger.warning(
                    "Artifact detection failed; skipping shell effect",
                    extra={
                        "session_id": self.session_id,
                        "effect_id": effect.effect_id,
                    },
                    exc_info=True,
                )
                continue
            if not artifacts:
                continue
            for artifact in artifacts:
                await self._emit_artifact_created(artifact)
            self._watch_pull_requests()

    def _sync_metadata_artifacts(
        self, artifacts: list[SessionArtifact], worktrees: list[WorktreeArtifact]
    ) -> None:
        """Mirror the service's artifacts into in-memory sidecar metadata.

        Called under ``_metadata_lock`` from the artifact service so a concurrent
        full persist cannot replace ``self._metadata`` with a pre-artifact
        snapshot.
        """
        if self._metadata is None:
            return
        self._metadata = self._metadata.model_copy(
            update={"artifacts": artifacts, "worktrees": worktrees}
        )

    async def _emit_artifact_created(self, artifact: SessionArtifact) -> None:
        """Announce a newly recorded artifact on the session's event stream."""
        if self._services is None:
            return
        await self._services.notify(
            "artifacts/created",
            ArtifactCreatedParams(
                event_id=0,  # the server re-sequences notifications
                session_id=self.session_id,
                emitted_at=now_ms(),
                artifact=to_public_artifact(artifact),
            ),
        )

    async def _emit_artifact_updated(self, artifact: SessionArtifact) -> None:
        """Announce an artifact change (``markViewed``) on the event stream."""
        # Keep the adapter's metadata in step with the service so the next
        # metadata persist carries the viewed_at update over (same update as
        # _drain_pending_shell_effects).
        if self._metadata is not None:
            self._metadata = self._metadata.model_copy(
                update={"artifacts": self._artifact_service.list_artifacts()}
            )
        if self._services is None:
            return
        await self._services.notify(
            "artifacts/updated",
            ArtifactUpdatedParams(
                event_id=0,  # the server re-sequences notifications
                session_id=self.session_id,
                emitted_at=now_ms(),
                artifact=to_public_artifact(artifact),
            ),
        )

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
            if raw_entry.get("type") == "message":
                client_message_id = _entry_client_message_id(raw_entry)
                if client_message_id is not None:
                    self._last_client_message_id = client_message_id
                    self._sync_message_id_holder()
                    turn_id = _entry_turn_id(raw_entry)
                    if turn_id is not None:
                        self._turn_client_message_ids[turn_id] = client_message_id
                continue
            effect_id = _entry_effect_id(raw_entry)
            if effect_id is None or self._session_telemetry.is_recorded(effect_id):
                continue
            message_id = self._effect_message_id(raw_entry)
            subagent = _terminal_subagent_effect(raw_entry)
            if subagent is not None:
                effect_id, operation, outcome, profile_source = subagent
                if self._session_telemetry.claim(effect_id):
                    self._session_telemetry.record_subagent_tool_call_finished(
                        operation=operation,
                        outcome=outcome,
                        profile_source=profile_source,
                        caller_agent_path=_ROOT_AGENT_PATH,
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

    def _active_model_supports_images(self) -> bool:
        try:
            return (
                self._context.config_orchestrator.config.get_active_model().supports_images
                is True
            )
        except Exception:
            return False

    def _track_turn_attachments(self, message: Sequence[ContentBlock]) -> None:
        count = (
            sum(isinstance(block, ImageContentBlock) for block in message)
            if self._active_model_supports_images()
            else 0
        )
        self._turn_attachment_counts = {AttachmentKind.IMAGE: count} if count else {}

    def _track_queue_attachments(self, params: TurnEnqueueParams) -> None:
        last_user_entry = next(
            (
                entry
                for entry in reversed(params.entries)
                if isinstance(entry, TurnUserInputEntry)
            ),
            None,
        )
        count = (
            sum(
                isinstance(block, SessionImageContentBlock)
                for block in last_user_entry.content
            )
            if self._active_model_supports_images() and last_user_entry is not None
            else 0
        )
        self._turn_attachment_counts = {AttachmentKind.IMAGE: count} if count else {}

    def _active_agent_profile_name(self) -> str | None:
        agents = getattr(self._context, "agents", None)
        profile = getattr(agents, "active_profile", None)
        return getattr(profile, "name", None)

    def _auto_compact_threshold(self) -> int:
        # The snapshot's context window is the compiled compaction threshold.
        return self._runtime.context_window

    def _record_compaction_telemetry(self, state: HarnessPublicSessionState) -> None:
        """Emit compaction telemetry once per newly terminal compaction checkpoint.

        ``auto_compact_triggered`` mirrors the legacy auto/reactive path (so it is
        gated on the automatic trigger) and reads the pre-compaction context size
        from the gauge, since the compaction's own snapshot has already reset it.
        ``compaction_failed`` follows only when the reason maps to a legacy bucket.
        """
        threshold: int | None = None
        for raw_entry in state.history.entries:
            if raw_entry.get("type") != "checkpoint":
                continue
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

        Runs on the app loop (drained from the state update and session
        shutdown) so ``send_request_sent`` schedules on the loop that owns the
        telemetry client, mirroring the subagent path. The runtime reports the
        per-call model, so it is preferred over the session's active alias.
        ``_record_tool_telemetry`` runs before this method in the state update,
        so ``_last_client_message_id`` already reflects the current snapshot's
        user entry.
        """
        for payload in self._context.request_sent.drain():
            if payload.purpose == "agent" and payload.turn_id in self._root_turn_ids:
                self._model_calls += 1
            self._session_telemetry.record_request_sent(
                model=payload.model,
                nb_context_chars=payload.nb_context_chars,
                nb_context_messages=payload.nb_context_messages,
                nb_prompt_chars=payload.nb_prompt_chars,
                call_type=request_call_type(payload.purpose, payload.iteration),
                reasoning_effort=payload.reasoning_effort,
                message_id=self._last_client_message_id,
                attachment_counts=self._turn_attachment_counts,
            )

    def _forward_malformed_tool_calls(self) -> None:
        """Drain the runtime's malformed tool call reports to the client.

        Same loop and message-id rules as ``_forward_request_sent``, so each
        report joins the ``vibe.request_sent`` of the request it describes.
        """
        for payload in self._context.malformed_tool_calls.drain():
            self._session_telemetry.record_tool_call_detected(
                model=payload.model,
                backend=payload.backend,
                api_style=payload.api_style,
                call_type=request_call_type(payload.purpose, payload.iteration),
                reasons=list(payload.reasons),
                nb_unusable_attempts=payload.nb_unusable_attempts,
                nb_dropped_tool_calls=payload.nb_dropped_tool_calls,
                status="success" if payload.succeeded else "error",
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
            update["session_cached_tokens"] = usage.cached_input_tokens
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
        # Recorded from the first counted call, so a snapshot that made none
        # leaves the stats, and the stats events, as they were.
        if self._model_calls:
            update["model_calls"] = self._model_calls
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
        added_cached = max(
            0, usage.cached_input_tokens - (before.cached_input_tokens if before else 0)
        )
        update["session_prompt_tokens"] = usage.input_tokens
        update["session_completion_tokens"] = usage.output_tokens
        update["session_cached_tokens"] = usage.cached_input_tokens
        # A snapshot that spent nothing is not a turn: hold the last call's
        # figures rather than blanking the last-turn line.
        if added_prompt or added_completion:
            update["last_turn_prompt_tokens"] = added_prompt
            update["last_turn_completion_tokens"] = added_completion
            update["last_turn_cached_tokens"] = added_cached
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
            return authorization, _required_event_id(event, "authorization")
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
    ) -> SessionBackendEvent | None:
        event_type = event.get("type")
        if event_type == "connector_authorization_required":
            return await self._connector_authorization_event(
                event, session_id=session_id
            )
        if event_type == "mcp_authorization_required":
            return self._mcp_authorization_event(event, session_id=session_id)
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
        A Core warning, such as ``request_too_long`` before a compaction,
        rides the same way and keeps its own code.
        """
        params = ServerWarningParams(
            warning=PublicError(
                code=_optional_event_str(event, "code")
                or _optional_event_str(event, "level")
                or "warning",
                message=bounded_error_message(_required_event_str(event, "message")),
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
            for observed, snapshot in self._pending_reads.items():
                if snapshot.watermark <= watermark and not observed.done():
                    observed.set_result(self._project_read(snapshot))
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
            "error_type",
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
        callback = project_history_entry(
            raw_callback, _display_roots(self.cwd, self._workspace_roots())
        )
        if not isinstance(callback, PublicCallbackEntry):
            _reject("a Harness callback event with a non-callback entry")
        callback_key = (callback.session_id, callback.id)
        if event_type == "callback_requested":
            if callback_key not in self._open_callbacks:
                self._notify_callback_opened(callback)
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
        # The Harness persists a settled question in history; its next state
        # update must read as already delivered, not as a new entry.
        if isinstance(callback.detail, UserInputCallbackDetail):
            self._merge_translated_entry(callback)
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
    "agents.list": "list",
    "agents.list_configs": "list_configs",
    "agents.spawn": "spawn",
    "agents.send_message": "send_message",
    "agents.interrupt": "interrupt",
    # Deprecated: pre-rename "subagent.*" spellings, kept only so persisted
    # sessions keep their telemetry. Use the "agents.*" names above; "wait"
    # and "stop" were retired ("stop" without a replacement).
    "subagent.list": "list",
    "subagent.spawn": "spawn",
    "subagent.wait": "wait",
    "subagent.send_message": "send_message",
    "subagent.interrupt": "interrupt",
    "subagent.stop": "close",
}
_TERMINAL_EFFECT_STATES = frozenset({"completed", "failed", "cancelled", "skipped"})
# The agent path of the Session this adapter records tool telemetry for. Only
# the served Session's own effects are recorded, and the local Harness caps
# subagents at depth one (a child installs no subagent controller), so every
# recorded subagent operation is called by the root.
_ROOT_AGENT_PATH: Final = "/root"

# Tool names whose effects carry a shell command — the agent's bash, the
# namespaced equivalent, and the shells a user's own ``!`` command reports as.
_SHELL_EFFECT_TOOL_NAMES = frozenset({
    "bash",
    "file_system.bash",
    "shell",
    "git_bash",
    "powershell",
})


@dataclass(frozen=True, slots=True)
class _PendingShellEffect:
    """A terminal shell effect queued for artifact detection.

    Only the plain strings the detector needs — the async service call happens
    in the drain, so no effect models are reconstructed here.
    """

    effect_id: str
    command: str
    stdout: str
    stderr: str
    status: str


def _terminal_shell_effect(entry: object) -> _PendingShellEffect | None:
    """Reconstruct a terminal shell effect from a raw history entry.

    Mirrors ``_terminal_tool_effect`` but keeps only the command and output
    texts: the artifact detector works on plain strings, not effect models.
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
        or state.get("status") not in _TERMINAL_EFFECT_STATES
    ):
        return None
    raw_name = detail.get("toolName")
    if not isinstance(raw_name, str) or raw_name not in _SHELL_EFFECT_TOOL_NAMES:
        return None
    raw_input = detail.get("input")
    if not isinstance(raw_input, Mapping):
        return None
    command = raw_input.get("command")
    if not isinstance(command, str):
        return None
    stdout, stderr = _shell_effect_output_texts(state.get("output"))
    # A compound command that creates the PR and then fails (``gh pr create
    # ... && false``) leaves the created URL in the state's combined
    # transcript (``outputText``) only — search it too, but never twice.
    output_text = state.get("outputText")
    if (
        isinstance(output_text, str)
        and output_text
        and output_text not in stdout
        and output_text not in stderr
    ):
        stdout = f"{stdout}\n{output_text}" if stdout else output_text
    status = state.get("status")
    return _PendingShellEffect(
        effect_id=effect_id,
        command=command,
        stdout=stdout,
        stderr=stderr,
        status=status if isinstance(status, str) else "",
    )


def _shell_effect_output_texts(raw_output: object) -> tuple[str, str]:
    """Extract (stdout, stderr) from a shell effect's output envelope.

    Tool results nest under ``structured_content``; when the harness only
    populated the combined transcript (``output``), it fills in for stdout.
    """
    if not isinstance(raw_output, Mapping):
        return "", ""
    structured = raw_output.get("structured_content")
    source = structured if isinstance(structured, Mapping) else raw_output
    stdout = source.get("stdout")
    stderr = source.get("stderr")
    transcript = source.get("output")
    stdout_text = stdout if isinstance(stdout, str) else ""
    if not stdout_text and isinstance(transcript, str):
        stdout_text = transcript
    stderr_text = stderr if isinstance(stderr, str) else ""
    return stdout_text, stderr_text


# Harness completion-failure codes, in the vocabulary clients are given. A code
# left unmapped reaches them as a string no `TurnErrorCode` branch matches, so
# the turn is treated as an unknown failure.
_HARNESS_ERROR_CODES: Final[dict[str, TurnErrorCode]] = {
    "context_overflow": TurnErrorCode.CONTEXT_TOO_LONG,
    "images_not_supported": TurnErrorCode.IMAGES_NOT_SUPPORTED,
    "modality_not_supported": TurnErrorCode.IMAGES_NOT_SUPPORTED,
    "model_stream_failed": TurnErrorCode.BACKEND_ERROR,
    "model_refused": TurnErrorCode.REFUSAL,
    "model_stream_incomplete": TurnErrorCode.INCOMPLETE_STREAM,
    "model_unauthorized": TurnErrorCode.INVALID_API_KEY,
    "pricing_unavailable": TurnErrorCode.PRICING_UNAVAILABLE,
}


def _moved_only_active_model(before: VibeConfigSchema, after: VibeConfigSchema) -> bool:
    # Origins and validation warnings count too: derivation reads which layer
    # set a field, and the runtime projects the warnings.
    before_values = before.model_dump()
    after_values = after.model_dump()
    return before.validation_warnings == after.validation_warnings and all(
        before_values[name] == after_values[name]
        and before.origin_of(name) == after.origin_of(name)
        for name in VibeConfigSchema.model_fields
        if name != "active_model"
    )


def _config_write_field(path: str) -> str:
    """The top-level config field a write's JSON pointer lands in."""
    return path.lstrip("/").split("/", 1)[0]


def _turn_error_http_status(error: PublicError) -> int | None:
    details = error.details if isinstance(error.details, dict) else {}
    status = details.get("httpStatus")
    return status if isinstance(status, int) else None


def _harness_turn_error_code(public_error: PublicError) -> TurnErrorCode | None:
    if public_error.code == "model_stream_failed":
        details = public_error.details if isinstance(public_error.details, dict) else {}
        status = details.get("httpStatus")
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            return TurnErrorCode.RATE_LIMIT
        if status in _REFUSED_CREDENTIAL_STATUSES:
            return TurnErrorCode.INVALID_API_KEY
    return _HARNESS_ERROR_CODES.get(str(public_error.code))


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
    "file_system.edit": "edit",
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


def _user_entry_ids(history: Sequence[PublicHistoryEntry]) -> list[str]:
    """Ids of the user messages in ``history``, in order: the rewind anchors."""
    return [
        entry.id
        for entry in history
        if isinstance(entry, PublicMessageEntry) and entry.role == "user"
    ]


def _entry_effect_id(entry: object) -> str | None:
    if not isinstance(entry, Mapping) or entry.get("type") != "effect":
        return None
    effect_id = entry.get("id")
    return effect_id if isinstance(effect_id, str) else None


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
    which the projection leaves verbatim under ``detail.input``. Settled todo
    effects report as ``tool_name="todo"`` with no file metrics.
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
        or detail.get("kind") not in {"tool", "todo"}
        or state.get("status") not in _TERMINAL_EFFECT_STATES
    ):
        return None
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
    if detail.get("kind") == "todo":
        # Todo writes touch no files, but they are approvable like any other tool,
        # so the grant path has to survive into the event.
        return _ToolCallTelemetry(
            effect_id=effect_id,
            tool_name="todo",
            status=status,
            nb_files_created=0,
            nb_files_modified=0,
            file_extension=None,
            decision=cast(Literal["execute", "skip"] | None, decision),
            approval_type=cast(Literal["always", "never", "ask"] | None, approval_type),
            approval_source=cast(
                Literal["config", "smart", "user", "bypass", "never"] | None,
                approval_source,
            ),
        )
    raw_name = detail.get("toolName")
    # Subagent effects have their own content-free event; skip them here.
    if not isinstance(raw_name, str) or raw_name in _SUBAGENT_OPERATIONS:
        return None
    tool_name = _TOOL_NAME_ALIASES.get(raw_name, raw_name)
    nb_files_created = 0
    nb_files_modified = 0
    file_extension: str | None = None
    if status == "success":
        raw_input = detail.get("input")
        # read/write use ``path``; edit uses ``file_path``.
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


def _worktree_git_trust(
    cwd: Path, store: TrustedFoldersManager
) -> TrustedFoldersManager:
    """Retain source Git trust for this live session across its managed move.

    This copy is never used for project configuration or background retention.
    Only an already trusted repository root can receive the temporary grant.
    """
    scoped = store.for_session()
    try:
        with WorktreeRepository.open(cwd, trust_store=store) as repository:
            root = repository.root
    except GitError:
        return scoped
    if store.is_trusted(root) is True:
        scoped.trust_for_session(root)
    return scoped


def _relocated_worktree_git_trust(
    previous_cwd: Path,
    cwd: Path,
    previous_store: TrustedFoldersManager,
    store: TrustedFoldersManager,
) -> TrustedFoldersManager:
    """Keep a live source grant only across checkouts of the same repository."""
    scoped = store.for_session()
    try:
        with (
            WorktreeRepository.open(
                previous_cwd, trust_store=previous_store
            ) as previous,
            WorktreeRepository.open(cwd, trust_store=store) as destination,
        ):
            root = destination.root
            inherited = (
                previous.bucket == destination.bucket
                and previous_store.is_trusted(previous.root) is True
            )
            if inherited or store.is_trusted(root) is True:
                scoped.trust_for_session(root)
    except GitError as exc:
        logger.debug(
            "Dropping relocated source Git trust: repository lookup failed previous_cwd=%s cwd=%s: %s",
            previous_cwd,
            cwd,
            exc,
        )
    return scoped


def _session_cwd(options: SessionOptions) -> str:
    return str(Path(options.cwd or Path.cwd()).expanduser().resolve())


def _continue_candidates(sessions: list[PublicSession]) -> list[PublicSession]:
    return [session for session in sessions if session.archived_at is None]


def _terminal_seat(
    options: SessionOptions, context: UnifiedSessionContext
) -> TerminalSeat | None:
    if options.terminal_key is None or not context.session_logging_enabled:
        return None
    return TerminalSeat(
        storage_root=Path(context.storage_root),
        terminal_key=options.terminal_key,
        cwd=_session_cwd(options),
    )


def _requested_cwds(params: SessionListParams) -> list[str]:
    if params.cwds is not None:
        return params.cwds
    return [params.cwd] if params.cwd is not None else []


def _session_context_scope(
    requested: SessionOptions,
    resolved: SessionOptions,
    *,
    stored_cwd: str | None,
    restored: bool,
) -> _SessionContextScope:
    """Finalize cwd trust before rebuilding a stored Session's context."""
    requested_cwd = Path(_session_cwd(requested))
    resolved_cwd = Path(_session_cwd(resolved))
    stored_path = (
        None if stored_cwd is None else Path(stored_cwd).expanduser().resolve()
    )
    rebuild_context = stored_path is not None and (
        restored or resolved_cwd != requested_cwd
    )
    keep_request_trust = requested.trust_workspace and resolved_cwd == requested_cwd
    final_options = (
        resolved
        if resolved.trust_workspace == keep_request_trust
        else resolved.model_copy(update={"trust_workspace": keep_request_trust})
    )
    trust_grants_to_revoke = (
        (requested_cwd,)
        if rebuild_context
        and requested.trust_workspace
        and stored_path != requested_cwd
        else ()
    )
    return _SessionContextScope(
        options=final_options,
        rebuild_context=rebuild_context,
        trust_grants_to_revoke=trust_grants_to_revoke,
    )


def _fork_worktree_notices(resolution: WorktreeResolution) -> tuple[str, ...]:
    prepared = resolution.prepared_worktree
    if prepared is None or not prepared.source_had_uncommitted_changes:
        return ()
    return (
        "The fork starts from the source worktree's committed HEAD. "
        "Uncommitted and untracked changes remain only in the source worktree.",
    )


async def _config_read_response(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    harness_files: HarnessFilesManager,
    *,
    sandbox: SandboxSessionSkills | None = None,
) -> ConfigReadResponse:
    """A sandboxed session's skill count includes the project skills its ``sandbox``
    holds.
    """
    config = orchestrator.config
    skills = resolve_session_skills(
        lambda: config, harness_files=harness_files, sandbox=sandbox
    ).available_skills
    return ConfigReadResponse(
        config=project_config_view(
            config,
            active_model_pinned=active_model_is_pinned(orchestrator),
            image_fallback=True,
        ),
        skills_count=len(skills),
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

    The caller cwd commonly also appears in ``workspace_roots``. Pin that occurrence too,
    or a fork keeps the caller checkout as an extra writable root beside its worktree.

    Trust is finalized after worktree resolution by ``_session_context_scope`` because a
    managed fork can move again after this stored-cwd rewrite.
    """
    if cwd is None:
        return options
    previous_cwd = _session_cwd(options)
    update: dict[str, object] = {"cwd": cwd}
    if cwd != previous_cwd and options.workspace_roots:
        previous_path = Path(previous_cwd).expanduser().resolve()
        update["workspace_roots"] = [
            cwd if Path(root).expanduser().resolve() == previous_path else root
            for root in options.workspace_roots
        ]
    return options.model_copy(update=update)


def _last_entered_worktree(history: Sequence[PublicHistoryEntry]) -> Path | None:
    """The worktree the latest successful ``enter_worktree`` said it moved to."""
    name = f"{VIBE_TOOL_GROUP}.{ENTER_WORKTREE_TOOL_NAME}"
    for entry in reversed(history):
        if not (
            isinstance(entry, PublicEffectEntry)
            and entry.detail.tool_name == name
            and isinstance(entry.state, CompletedEffectState)
            and isinstance(entry.state.output, dict)
        ):
            continue
        structured = entry.state.output.get("structured_content")
        path = structured.get("path") if isinstance(structured, dict) else None
        if isinstance(path, str):
            return Path(path)
    return None


def _left_worktree(cwd: Path) -> tuple[Path, str, Path] | None:
    """The managed worktree a session leaves, so it is tracked like one it entered."""
    managed = ManagedWorktree.at(cwd)
    record = None if managed is None else managed.claim.read()
    if managed is None or record is None:
        return None
    return managed.root, record.branch, record.repo_root


def _entered_worktrees(storage_root: str, session_id: str) -> list[str]:
    """The worktrees a stored session entered that still exist."""
    metadata = _read_unified_session_metadata(
        _unified_session_dir(storage_root, session_id)
    )
    if metadata is None:
        return []
    return [w.path for w in metadata.worktrees if Path(w.path).is_dir()]


def _with_entered_worktrees(
    options: SessionOptions, entered: Sequence[str]
) -> SessionOptions:
    if not entered:
        return options
    roots = list(dict.fromkeys([*options.workspace_roots, *entered]))
    return options.model_copy(update={"workspace_roots": roots})


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
        params.model_dump(
            mode="json", by_alias=True, exclude={"sent_by_session", "sender"}
        )
    )


class _SessionMessageInjectParams(ContextInjectParams):
    """Not a client's: only another session's message carries its marker."""

    user_display_content: UserDisplayContent | None = None


def _from_its_sender(params: TurnEnqueueParams) -> TurnEnqueueParams:
    """The model reads who sent it from a block ahead of it; clients from its
    marker. Applied after prompt preparation, so the sender's name mentions
    nothing.
    """
    sender = params.sender
    if sender is None:
        return params
    entries = [
        entry.model_copy(
            update={
                "content": [
                    SessionTextContentBlock(text=session_message_header(sender)),
                    *entry.content,
                ],
                "annotations": entry.annotations.model_copy(
                    update={
                        "vibe_user_display_content": session_message_display(sender)
                    }
                ),
            }
        )
        if isinstance(entry, TurnUserInputEntry)
        else entry
        for entry in params.entries
    ]
    return params.model_copy(update={"entries": entries})


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
        session_cached_tokens=usage.cached_input_tokens if usage else 0,
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
    host: UnifiedHarnessSessionBackendHost,
    session_id: str,
    build_agents: Callable[[], AgentManager],
) -> _SessionPins:
    """The pins of a session that is not loaded, read without resuming it."""
    pins = await _harness_call(host.session_pins(session_id))
    model = pins[SessionPin.ACTIVE_MODEL]
    agent_name = pins[SessionPin.AGENT_NAME]
    reasoning_effort = pins[SessionPin.REASONING_EFFORT]
    agent = (
        _pinned_agent(await asyncio.to_thread(build_agents), agent_name)
        if agent_name
        else None
    )
    return _SessionPins(
        model=model or None, reasoning_effort=reasoning_effort or None, agent=agent
    )


_NO_SESSION_PINS = _SessionPins()


def _restore_saved_catalogue(context: _PinTarget, value: str) -> bool:
    """Hand the derivations the catalogue answer the session saved.

    Only a derivation for the saved model at the saved endpoint reads it; any
    other asks the catalogue again. A value that does not parse is ignored.
    """
    snapshot = CatalogueSnapshot.from_pin(value)
    if snapshot is None or snapshot == context.saved_catalogue.snapshot:
        return False
    context.saved_catalogue.snapshot = snapshot
    return True


def _harness_probe_read_params(params: SessionReadParams) -> HarnessSessionReadParams:
    return HarnessSessionReadParams(
        session_id=params.session_id, history_limit=params.history_limit + 1
    )


def _window_and_older(
    entries: list[dict[str, Any]], history_limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split history into the shown window and the entries above it."""
    window = entries[-history_limit:] if history_limit else []
    return window, entries[: len(entries) - len(window)]


def _without_probe(
    snapshot: HarnessSessionSnapshot, history_limit: int
) -> HarnessSessionSnapshot:
    """Drop the extra probed entry, recording it as the page's `cursor.before`.

    The Harness trims a read to its limit without setting `cursor.before`, so
    reading one entry more is the only sign that older history exists.
    """
    history = snapshot.state.history
    if len(history.entries) <= history_limit:
        return snapshot
    kept, older = _window_and_older(history.entries, history_limit)
    page = history.model_copy(
        update={
            "entries": kept,
            "cursor": history.cursor.model_copy(update={"before": older[-1]["id"]}),
        }
    )
    return snapshot.model_copy(
        update={
            "state": snapshot.state.model_copy(update={"history": page}),
            "history_limit": history_limit,
        }
    )


async def _read_widening_window(
    read: Callable[[int], Awaitable[PublicSessionState]],
    *,
    start_window: int = _HISTORY_PAGE_WINDOW,
    enough: Callable[[list[PublicHistoryEntry]], bool] | None = None,
) -> tuple[PublicSessionState, list[PublicHistoryEntry], int, bool]:
    """Read latest windows, doubling the limit until one fits or history runs out.

    Reads probe one entry past the window, so a missing `history_before_cursor`
    is exact; a read that ignores its limit stops growing instead of looping.
    """
    window = start_window
    loaded = -1
    while True:
        state = await read(window)
        history = state.history or []
        complete = state.history_before_cursor is None or len(history) == loaded
        if complete or (enough is not None and enough(history)):
            return state, history, window, complete
        loaded = len(history)
        window *= 2


class _HistoryWindows:
    """Page history out of Harness reads that each return a latest window.

    The Harness cannot read before a cursor, so a page needs a window reaching
    past it. Each session keeps the window its last page needed, so scrolling
    back reads about one window per page instead of doubling up from the tail.
    """

    def __init__(self) -> None:
        self._sizes: dict[str, int] = {}

    async def list(
        self,
        params: SessionHistoryListParams,
        read: Callable[[int], Awaitable[PublicSessionState]],
    ) -> SessionHistoryListResponse:
        backward = params.sort_direction == "backward"
        before = params.cursor if backward else None
        history, window, complete = await self._read(
            params.session_id, read, before, params.limit
        )
        page = history_page(
            history,
            turn_id=params.turn_id,
            before=before,
            after=None if backward else params.cursor,
            limit=params.limit,
        )
        next_cursor = page.cursor.before if backward else page.cursor.after
        # A window that starts mid-history still has older entries before it.
        if (
            backward
            and not complete
            and page.entries
            and page.entries[0].id == history[0].id
        ):
            next_cursor = page.entries[0].id
        # A complete window still serves the older pages; only the last one forgets it.
        if next_cursor is None:
            self._sizes.pop(params.session_id, None)
        else:
            self._remember(params.session_id, window)
        return SessionHistoryListResponse(
            items=page.entries,
            next_cursor=next_cursor,
            previous_cursor=page.cursor.after if backward else page.cursor.before,
        )

    async def _read(
        self,
        session_id: str,
        read: Callable[[int], Awaitable[PublicSessionState]],
        before: str | None,
        limit: int,
    ) -> tuple[list[PublicHistoryEntry], int, bool]:
        def window_reaches(history: list[PublicHistoryEntry]) -> bool:
            end = (
                len(history)
                if before is None
                else next(
                    (
                        index
                        for index, entry in enumerate(history)
                        if entry.id == before
                    ),
                    None,
                )
            )
            return end is not None and end >= limit

        _, history, window, complete = await _read_widening_window(
            read,
            start_window=self._sizes.get(session_id, _HISTORY_PAGE_WINDOW),
            enough=window_reaches,
        )
        return history, window, complete

    def _remember(self, session_id: str, window: int) -> None:
        """Keep the most recently paged sessions' windows, evicting the oldest."""
        self._sizes.pop(session_id, None)
        self._sizes[session_id] = window
        if len(self._sizes) > _HISTORY_WINDOW_SESSIONS:
            del self._sizes[next(iter(self._sizes))]


@dataclass(frozen=True, slots=True)
class _ProjectedEntry:
    source: Mapping[str, Any]
    history: list[PublicHistoryEntry]


# Projection is pure and public entries are never mutated, so states share them.
class _HistoryProjections:
    def __init__(self) -> None:
        self._by_id: dict[str, _ProjectedEntry] = {}
        self._display_roots: tuple[Path, ...] = ()

    def project(
        self, window: Sequence[Mapping[str, Any]], display_roots: tuple[Path, ...]
    ) -> list[PublicHistoryEntry]:
        previous, self._by_id = self._by_id, {}
        # Displayed paths depend on the roots, so a moved workspace reprojects.
        if display_roots != self._display_roots:
            previous, self._display_roots = {}, display_roots
        history: list[PublicHistoryEntry] = []
        for raw_entry in window:
            entry_id = raw_entry.get("id")
            if not isinstance(entry_id, str):
                history.extend(project_raw_history_entry(raw_entry, display_roots))
                continue
            projected = previous.get(entry_id)
            if projected is None or projected.source is not raw_entry:
                public_entries = (
                    projected.history
                    if projected is not None and projected.source == raw_entry
                    else project_raw_history_entry(raw_entry, display_roots)
                )
                # The Harness rebuilds an entry once when it persists it, then
                # repeats that object: keep the newer one to match it by identity.
                projected = _ProjectedEntry(raw_entry, public_entries)
            self._by_id[entry_id] = projected
            history.extend(projected.history)
        return history


def _lease_holder(holder: LeaseHolder | None) -> SessionLeaseHolder | None:
    return None if holder is None else SessionLeaseHolder(process_id=holder.process_id)


@dataclass(frozen=True, slots=True)
class _ProjectedHistory:
    """Public history already projected, and whether older entries exist."""

    entries: list[PublicHistoryEntry]
    has_older: bool


def _read_response(  # noqa: PLR0913 - one read input per keyword, all keyword-only
    snapshot: HarnessSessionSnapshot,
    cwd: str | None,
    *,
    event_id: int | None = None,
    metadata: SessionMetadata | None = None,
    pins: _SessionPins = _NO_SESSION_PINS,
    worktree: PublicSessionWorktree | None = None,
    projections: _HistoryProjections | None = None,
    workspace_roots: Sequence[Path] = (),
    # Required on purpose: a caller that forgets the session's loops or starter
    # must fail loudly instead of silently publishing a state without them.
    scheduled_loops: Sequence[ScheduledPrompt],
    # Required for the same reason: None means the session runs no connector
    # routes here (a subagent, or a session that is not open).
    mcp_apps: SessionMcpApps | None,
    started_by_session_id: str | None,
    projected_history: _ProjectedHistory | None = None,
) -> SessionReadResponse:
    display_roots = _display_roots(cwd, workspace_roots)
    if projected_history is not None:
        # Already projected from Session API entries, one entry at a time.
        history = projected_history.entries
        has_older = projected_history.has_older
    else:
        entries = snapshot.state.history.entries
        history_limit = getattr(snapshot, "history_limit", len(entries))
        window, older = _window_and_older(entries, history_limit)
        has_older = bool(older) or snapshot.state.history.cursor.before is not None
        history = (
            [
                entry
                for raw_entry in window
                for entry in project_raw_history_entry(raw_entry, display_roots)
            ]
            if projections is None
            else projections.project(window, display_roots)
        )
    if mcp_apps is not None:
        history = mcp_apps.apply(history)
    last_event_id = snapshot.watermark if event_id is None else event_id
    harness_retrying = getattr(snapshot.state, "retrying", None)
    return SessionReadResponse(
        state=PublicSessionState(
            event_id=last_event_id,
            session=_public_session(
                snapshot.state.session,
                cwd,
                metadata=metadata,
                pins=pins,
                worktree=worktree,
                scheduled_loops=scheduled_loops,
                started_by_session_id=started_by_session_id,
            ),
            history=history,
            history_before_cursor=history[0].id if history and has_older else None,
            turns=(
                [_public_turn(snapshot.state.latest_turn)]
                if snapshot.state.latest_turn is not None
                else []
            ),
            active_callbacks=_public_active_callbacks(
                snapshot.state.active_callbacks, display_roots
            ),
            turn_queue=_public_turn_queue(snapshot.state.turn_queue),
            background_processes=[
                validate_backend_wire(
                    PublicBackgroundProcess,
                    process.model_dump(mode="json", by_alias=True),
                )
                # A harness version predating public process summaries exposes no
                # background_processes attribute on its session-state model.
                for process in getattr(snapshot.state, "background_processes", [])
            ],
            retrying=(
                PublicRetryState(
                    turn_id=harness_retrying.turn_id,
                    category=PublicRetryCategory(harness_retrying.category),
                    detail=harness_retrying.detail,
                    retry_at=harness_retrying.retry_at,
                    retry_attempt=harness_retrying.retry_attempt,
                )
                if harness_retrying is not None
                else None
            ),
        ),
        last_event_id=last_event_id,
    )


def _session_api_commands(
    session: UnifiedHarnessSessionBackend,
    host: UnifiedHarnessSessionBackendHost,
    derivation: UnifiedRuntimeDerivation,
) -> SessionApiCommands | None:
    """The Session's Session API commands, or None on the direct path.

    The path follows the derivation the Session opened with, which also decides
    whether its Runtime records what the Session API serves.

    The Session API modules load only here and on the Session API path, so a
    Session the switch leaves on the direct path does not import them.
    """
    if not derivation.adapter_config.session_api:
        return None
    from vibe.app_server._session_api_commands import SessionApiCommands

    return SessionApiCommands(session, host)


def _session_api_path(
    adapter: UnifiedHarnessBackendAdapter, api: SessionApiCommands | None
) -> SessionApiPath | None:
    """The adapter's Session API path, or None on the direct path.

    Imported here so a Session on the direct path does not load the Session API.
    """
    if api is None:
        return None
    from vibe.app_server._session_api_path import SessionApiPath

    return SessionApiPath(adapter, api)


def _with_callbacks_in_history(state: PublicSessionState) -> PublicSessionState:
    """``state`` with its open callbacks as history entries, as a resync needs."""
    if not state.active_callbacks:
        return state
    history = {entry.id: entry for entry in state.history or []}
    for callback in state.active_callbacks:
        history[callback.id] = callback
    return state.model_copy(update={"history": list(history.values())})


def _display_roots(
    cwd: str | None, workspace_roots: Sequence[Path]
) -> tuple[Path, ...]:
    """Where displayed file paths are made relative: the cwd first, then the roots."""
    roots = [Path(os.path.normpath(cwd))] if cwd else []
    return tuple(dict.fromkeys([*roots, *workspace_roots]))


def _public_active_callbacks(
    raw_callbacks: Sequence[object], display_roots: Sequence[Path]
) -> list[PublicCallbackEntry]:
    callbacks: list[PublicCallbackEntry] = []
    for raw_callback in raw_callbacks:
        callback = project_history_entry(raw_callback, display_roots)
        if not isinstance(callback, PublicCallbackEntry):
            _reject("an active Harness callback with a non-callback entry")
        callbacks.append(callback)
    return callbacks


def _harness_event_watermark(event: dict[str, Any]) -> int:
    event_id = event.get("eventId")
    if isinstance(event_id, int):
        return event_id
    if isinstance(event_id, str) and event_id.isdecimal():
        return int(event_id)
    _reject("a Harness session event without an event id")


def _published_harness_state(raw_state: dict[str, Any]) -> HarnessPublicSessionState:
    # Window entries are validated when projected; validating the whole history
    # on every update would cost the session's length.
    raw_history = raw_state.get("history")
    entries = raw_history.get("entries") if isinstance(raw_history, dict) else None
    if not isinstance(raw_history, dict) or not (
        isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries)
    ):
        return HarnessPublicSessionState.model_validate(raw_state)
    state = HarnessPublicSessionState.model_validate({
        **raw_state,
        "history": {**raw_history, "entries": []},
    })
    return state.model_copy(
        update={"history": state.history.model_copy(update={"entries": entries})}
    )


def _vibe_token_usage(usage: HarnessTokenUsage | None) -> VibeTokenUsage | None:
    """Map harness token usage into the client model, tolerating field skew.

    The harness runtime upgrades independently of the client — a self-hosted
    install or a ``--with-editable`` dev harness can run ahead and add usage
    fields. This reads harness output, so it ignores unknown fields per ADR 0014
    rather than letting one new field fail the whole session read. Known fields
    still validate strictly.
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
    worktree: PublicSessionWorktree | None = None,
    scheduled_loops: Sequence[ScheduledPrompt] = (),
    started_by_session_id: str | None,
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
        public_status = VibeFailedSessionStatus(
            message=bounded_error_message(status.message)
        )
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
        started_by_session_id=started_by_session_id,
        title=session.title,
        preview=project_session_message_preview(
            project_loop_preview(public_session_preview(session.preview))
        ),
        status=public_status,
        created_at=session.created_at,
        updated_at=session.updated_at,
        bumped_at=_metadata_bumped_at_ms(metadata),
        pinned_at=_metadata_pinned_at_ms(metadata),
        archived_at=_metadata_archived_at_ms(metadata),
        is_unseen=SessionSeenState.from_metadata(metadata).is_unseen,
        cwd=cwd,
        worktree=worktree,
        model=pins.model,
        reasoning_effort=pins.reasoning_effort,
        agent=pins.agent,
        token_usage=token_usage,
        context_usage=context_usage,
        harness=harness,
        loops=[_project_scheduled_loop(loop) for loop in scheduled_loops],
        project=(
            _public_session_project(metadata.project)
            if metadata is not None and metadata.project is not None
            else None
        ),
    )


def _public_session_project(project: SessionProject) -> PublicSessionProject:
    return PublicSessionProject(kind=project.kind, id=project.id)


def _move_target(target: SessionMoveTarget) -> tuple[SessionProject, Path | None]:
    """The stamp a move writes, and the folder it moves to: the project's primary one."""
    if not isinstance(target, SessionMoveToLocalProject):
        return SessionProject(kind="none"), None
    try:
        projects = LocalProjectStore().list_projects()
    except LocalProjectStoreUnreadable as exc:
        raise SessionBackendError(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc
    project = next((project for project in projects if project.id == target.id), None)
    if project is None:
        raise SessionBackendError(
            ProtocolErrorCode.NOT_FOUND, f"Local project not found: {target.id}"
        )
    return SessionProject(kind="local", id=project.id), project.root_paths[0]


def _folder_stamps(
    projects: Sequence[LocalProject],
    project_ids: set[str],
    sessions: Sequence[PublicSession],
) -> dict[str, SessionProject]:
    """By session id, the stamp of each session no local project claims whose folder matches."""
    # Sessions share few folders, and each match resolves its folder's path.
    match = cache(ProjectFolders(projects).match)
    return {
        session.id: SessionProject(kind="local", id=project.id)
        for session in sessions
        if _claims_no_project(projects, project_ids, session.project)
        and (project := match(session.cwd)) is not None
    }


def _claims_no_project(
    projects: Sequence[LocalProject],
    project_ids: set[str],
    stamp: PublicSessionProject | None,
) -> bool:
    # The runner lists a cloud stamp no local project goes by (as the system default project's), or a removed local project's, under no project.
    if stamp is None:
        return True
    if stamp.kind == "local":
        return stamp.id not in project_ids
    return (
        stamp.kind == "cloud"
        and stamp.id is not None
        and session_project(projects, stamp.id).kind == "cloud"
    )


def _stored_session_project(session: PublicSession) -> SessionProject | None:
    if session.project is None:
        return None
    return SessionProject(kind=session.project.kind, id=session.project.id)


def _stored_session_artifacts(
    storage_root: str, session_id: str
) -> SessionArtifactService:
    session_dir = _unified_session_dir(storage_root, session_id)
    metadata = _read_unified_session_metadata(session_dir)
    service = SessionArtifactService(session_id=session_id, session_dir=session_dir)
    if metadata is not None and metadata.session_id == session_id:
        service.load(metadata)
    return service


def _listed_pull_requests(
    storage_root: str, session_ids: Sequence[str]
) -> dict[str, list[SessionArtifact]]:
    pull_requests: dict[str, list[SessionArtifact]] = {}
    for session_id in session_ids:
        artifacts = _stored_session_artifacts(storage_root, session_id).list_artifacts()
        if artifacts:
            pull_requests[session_id] = artifacts
    return pull_requests


def _public_pull_requests(
    pull_requests: dict[str, list[SessionArtifact]],
) -> dict[str, list[PublicSessionArtifact]]:
    """On the event loop: the projection reads the process's pull request
    cache (``to_public_artifact``).
    """
    return {
        session_id: [to_public_artifact(artifact) for artifact in artifacts]
        for session_id, artifacts in pull_requests.items()
    }


def _project_listed_row(
    storage_root: str, item: HarnessSessionListItem, prior: _ListedRow | None
) -> _ListedRow:
    # Without pins: a listing feeds a session list, not a composer, and reading
    # one costs a full Harness catalogue parse per row.
    session_dir = _unified_session_dir(storage_root, item.session.id)
    metadata_signature = file_signature(session_dir / METADATA_FILENAME)
    loops_path = session_dir / "scheduled-loops.json"
    loops_signature = file_signature(loops_path)
    if (
        prior is not None
        and prior.metadata_signature == metadata_signature
        and prior.loops_signature == loops_signature
        and prior.item == item
    ):
        return prior
    public = _public_session(
        item.session,
        item.cwd,
        harness="unified",
        metadata=_read_unified_session_metadata(session_dir),
        scheduled_loops=read_stored_loops(loops_path),
        started_by_session_id=item.started_by_session_id,
    )
    return _ListedRow(
        item=item,
        metadata_signature=metadata_signature,
        loops_signature=loops_signature,
        public=public,
    )


def _merge_listed_sessions(
    params: SessionListParams,
    unified: Sequence[PublicSession],
    legacy: Sequence[PublicSession],
) -> list[PublicSession]:
    merged = [*unified, *legacy]
    if params.session_ids is not None:
        asked = set(params.session_ids)
        merged = [session for session in merged if session.id in asked]
    if not params.include_archived:
        merged = [session for session in merged if session.archived_at is None]
    merged.sort(key=lambda session: (session.updated_at, session.id), reverse=True)
    return merged


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


def _project_scheduled_loop(loop: ScheduledPrompt) -> ScheduledLoop:
    return ScheduledLoop(
        id=loop.id,
        prompt=loop.prompt,
        interval_seconds=loop.interval_seconds,
        cron=loop.cron,
        next_fire_at=loop.next_fire_at,
    )


_BUDGET_STOP_REASONS: Final = frozenset(get_args(BudgetStopReason))


def _public_turn(turn: object) -> PublicTurn:
    raw = cast(Any, turn)
    error = getattr(raw, "error", None)
    reason = getattr(raw, "reason", None)
    return PublicTurn(
        id=raw.id,
        session_id=raw.session_id,
        status=PublicTurnStatus(str(raw.status)),
        started_at=raw.started_at,
        completed_at=getattr(raw, "completed_at", None),
        error=_public_turn_error(error),
        stop_reason=getattr(raw, "stop_reason", None),
        budget_stop_reason=reason if reason in _BUDGET_STOP_REASONS else None,
        queue_item_id=getattr(raw, "queue_item_id", None),
        input_entry_id=getattr(raw, "input_entry_id", None),
    )


def _public_turn_queue(queue: object) -> PublicTurnQueue:
    raw = cast(Any, queue)
    projected = project_loop_queue(
        validate_backend_wire(
            PublicTurnQueue, raw.model_dump(mode="json", by_alias=True)
        )
    )
    return projected.model_copy(
        update={
            "items": [
                item.model_copy(
                    update={
                        "entries": [
                            _project_queued_entry(entry) for entry in item.entries
                        ]
                    }
                )
                for item in projected.items
            ]
        }
    )


def _project_queued_entry(
    entry: TurnContextInputEntry | TurnUserInputEntry,
) -> TurnContextInputEntry | TurnUserInputEntry:
    if not isinstance(entry, TurnUserInputEntry):
        return entry
    entry = entry.model_copy(
        update={
            "content": public_session_message_content(
                entry.content,
                is_session_message(entry.annotations.vibe_user_display_content),
            )
        }
    )
    display_names = skill_invocation_display_names(
        entry.annotations.vibe_user_display_content
    )
    public_annotations = entry.annotations.model_copy(
        update={
            "vibe_user_display_content": without_skill_invocation_display(
                entry.annotations.vibe_user_display_content
            )
        }
    )
    payloads = invoked_skill_payloads(
        [
            block.text if isinstance(block, SessionTextContentBlock) else None
            for block in entry.content
        ],
        display_names,
    )
    indices = {index for index, _name in payloads}
    return entry.model_copy(
        update={
            "content": [
                block
                for index, block in enumerate(entry.content)
                if index not in indices
            ],
            "annotations": public_annotations,
        }
    )


def _public_turn_error(error: object | None) -> PublicError | None:
    if error is None:
        return None
    raw_error = cast(Any, error)
    public_error = validate_backend_wire(
        PublicError, raw_error.model_dump(mode="json", by_alias=True)
    )
    public_error = public_error.model_copy(
        update={"message": bounded_error_message(public_error.message)}
    )
    # The Harness has its own error vocabulary; clients only know TurnErrorCode.
    if mapped := _harness_turn_error_code(public_error):
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
    history: Sequence[PublicHistoryEntry], session_id: str
) -> list[PublicTurn]:
    turns: dict[str, PublicTurn] = {}
    for entry in history:
        if entry.turn_id is None:
            continue
        previous = turns.get(entry.turn_id)
        input_entry_id = (
            entry.input_entry_id
            if entry.input_entry_id is not None
            else previous.input_entry_id
            if previous is not None
            else None
        )
        turns[entry.turn_id] = PublicTurn(
            id=entry.turn_id,
            session_id=session_id,
            status=PublicTurnStatus.COMPLETED,
            started_at=entry.created_at if previous is None else previous.started_at,
            completed_at=entry.updated_at,
            input_entry_id=input_entry_id,
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
        self._terminal_state: EffectState | None = None

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
        self._terminal_state = state
        return self._updated(
            previous, self._projector.complete_effect(self._entry_id, state)
        )

    @property
    def entry(self) -> PublicEffectEntry:
        """The entry in its current, terminal-when-completed form."""
        return cast(PublicEffectEntry, self._entry)

    @property
    def record(self) -> PublicEffectEntry:
        """The completed entry with the full terminal state, before the client cap.

        The store applies its own, tighter model-output limit; recording the
        client-capped state instead could leave it nothing structured to cap.
        """
        if self._terminal_state is None:
            return self.entry
        return self.entry.model_copy(update={"state": self._terminal_state})

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
        connector_effect_tool_name(tool.group_name, tool.programmatic_name): (
            f"connector_{tool.alias}_{tool.remote_name}"
        )
        for group in snapshot.groups
        for tool in group.tools
    }


def _session_mcp_state(
    snapshot: HarnessMCPRouteSnapshot,
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    plugin_mcp: PluginMCPCatalog,
) -> SessionMCPState:
    # The harness runs configured and plugin servers alike, so both are read
    # off its snapshot; a configured server wins a name, as in the merge.
    servers: dict[str, MCPServer] = {
        source.name: source.entry.server for source in plugin_mcp.sources()
    }
    servers.update((server.name, server) for server in orchestrator.config.mcp_servers)
    judged = _judged_mcp_tools(snapshot, servers, _mcp_tool_filter(orchestrator.config))
    sources = tuple(
        SessionMCPSourceState(
            name=source.name,
            transport=cast(Any, servers[source.name].transport),
            status=source.status,
            tools=tuple(
                judged[source.name, descriptor.remote_name]
                for descriptor in source.descriptors
            ),
            descriptor_revision=source.descriptor_revision,
            error=source.error,
        )
        for source in snapshot.sources
        if source.name in servers
    )
    return SessionMCPState(
        catalog_revision=snapshot.catalog_revision,
        route_revision=snapshot.route_revision,
        sources=sources,
        # A server still being discovered has not failed: no "failed to
        # connect" notice for it.
        discovery_errors={
            source.name: source.error
            for source in snapshot.sources
            if source.error is not None and source.error != MCP_DISCOVERY_PENDING
        },
    )


def _judged_mcp_tools(
    snapshot: HarnessMCPRouteSnapshot,
    servers: Mapping[str, MCPServer],
    tool_filter: MCPToolFilter | None,
) -> dict[tuple[str, str], SessionMCPToolDescriptor]:
    # Unjudged names every source for display; judged keeps only the servers the
    # harness routes, since disabled ones would shift colliding names.
    def published(*, judged: bool) -> dict[tuple[str, str], HarnessMCPToolDescriptor]:
        every_source = build_route_snapshot(
            catalog_revision=snapshot.catalog_revision,
            resolved=[
                (
                    HarnessResolvedMCPServerConfig(
                        name=source.name,
                        transport=servers[source.name].transport,
                        disabled_tools=frozenset(servers[source.name].disabled_tools)
                        if judged
                        else frozenset(),
                    ),
                    source.descriptors,
                )
                for source in snapshot.sources
                if source.name in servers
                and not (judged and servers[source.name].disabled)
            ],
            sources=(),
            tool_filter=tool_filter if judged else None,
        )
        return _published_mcp_tools(every_source)

    allowed = published(judged=True)
    routed = _published_mcp_tools(snapshot)
    return {
        key: SessionMCPToolDescriptor(
            remote_name=tool.remote_name,
            description=tool.description,
            enabled=key in allowed,
            display_name=routed.get(key, tool).display_name,
        )
        for key, tool in published(judged=False).items()
    }


def _published_mcp_tools(
    snapshot: HarnessMCPRouteSnapshot,
) -> dict[tuple[str, str], HarnessMCPToolDescriptor]:
    return {
        (tool.server_name, tool.remote_name): tool
        for group in snapshot.groups
        for tool in group.tools
    }


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


_DIRECT_SESSION_ERROR_CODES: dict[str, ProtocolErrorCode] = {
    "callback_closed": ProtocolErrorCode.CALLBACK_CLOSED,
    "callback_not_found": ProtocolErrorCode.NOT_FOUND,
    "unknown_process": ProtocolErrorCode.NOT_FOUND,
    "callback_conflict": ProtocolErrorCode.CONFLICT,
    "configuration_rejected": ProtocolErrorCode.INVALID_PARAMS,
    "history_entry_conflict": ProtocolErrorCode.CONFLICT,
    "turn_queue_edit_conflict": ProtocolErrorCode.CONFLICT,
}

# Internal harness errors the client explains to the user: not Vibe bugs.
_EXPLAINED_HARNESS_CODES = frozenset({"store_requires_newer_reader"})


def _peer_refusal(refusal: PeerRefusal, message: str) -> SessionBackendError:
    return SessionBackendError(
        ProtocolErrorCode.CONFLICT, message, {"refusal": refusal}
    )


def _raise_session_backend_error(exc: HarnessSessionError) -> Never:
    direct_code = _DIRECT_SESSION_ERROR_CODES.get(exc.code)
    if direct_code is not None:
        raise SessionBackendError(direct_code, str(exc)) from exc
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
            bounded_error_message(str(exc)),
            data,
            after_response=getattr(exc, "after_response", None),
            on_response_abandoned=getattr(exc, "on_response_abandoned", None),
        ) from exc
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
    if exc.code == "invalid_cursor":
        raise SessionBackendError(
            ProtocolErrorCode.INVALID_PARAMS, str(exc), exc.details
        ) from exc
    code = {
        "child_session_requires_parent": ProtocolErrorCode.CONFLICT,
        "client_command_conflict": ProtocolErrorCode.CONFLICT,
        "fork_active_turn": ProtocolErrorCode.CONFLICT,
        "fork_entry_not_found": ProtocolErrorCode.INVALID_PARAMS,
        "fork_entry_required": ProtocolErrorCode.CONFLICT,
        "session_busy": ProtocolErrorCode.CONFLICT,
        "turn_conflict": ProtocolErrorCode.CONFLICT,
        "turn_queue_pending": ProtocolErrorCode.CONFLICT,
        "unfinished_work_migration": ProtocolErrorCode.CONFLICT,
    }.get(exc.code, ProtocolErrorCode.INTERNAL_ERROR)
    data = {"harnessCode": exc.code}
    if exc.details is not None:
        data["details"] = exc.details
        reason = exc.details.get("reason")
        if isinstance(reason, str):
            data["reason"] = reason
    raise SessionBackendError(
        code, str(exc), data, expected=exc.code in _EXPLAINED_HARNESS_CODES
    ) from exc


def _history_limit(params: SessionReadParams) -> int:
    history = params.history
    return history.limit if history is not None else _HISTORY_PAGE_WINDOW


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
