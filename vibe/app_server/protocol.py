from __future__ import annotations

from enum import StrEnum, auto
from typing import Annotated, Any, Literal

from pydantic import (
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    TypeAdapter,
    model_validator,
)

from vibe.app_server._connection_protocol import (
    CallbackKind as CallbackKind,
    ClientCapabilities as ClientCapabilities,
    ClientInfo as ClientInfo,
    ClientToolCapability as ClientToolCapability,
    ClientToolMethod as ClientToolMethod,
    ClientToolReadTextFileParams as ClientToolReadTextFileParams,
    ClientToolReadTextFileResponse as ClientToolReadTextFileResponse,
    ClientToolTerminalCreateParams as ClientToolTerminalCreateParams,
    ClientToolTerminalCreateResponse as ClientToolTerminalCreateResponse,
    ClientToolTerminalOutputResponse as ClientToolTerminalOutputResponse,
    ClientToolTerminalParams as ClientToolTerminalParams,
    ClientToolTerminalWaitResponse as ClientToolTerminalWaitResponse,
    ClientToolWriteTextFileParams as ClientToolWriteTextFileParams,
    InitializeParams as InitializeParams,
    InitializeResponse as InitializeResponse,
    ServerCapabilities as ServerCapabilities,
    ServerInfo as ServerInfo,
    TransportKind as TransportKind,
)
from vibe.app_server._model import ProtocolModel
from vibe.app_server.config import ConfigView, ProxySettingsView, ThinkingLevel
from vibe.app_server.models import (
    AccountView,
    AgentStatsSnapshot,
    AgentSummary,
    CallbackOutput,
    ConfigIssue,
    ConnectorCounts,
    ContentBlock,
    DebugLogPage,
    JsonPatchOperation,
    MCPState,
    MentionStats,
    PreparedPrompt,
    PublicCallbackEntry,
    PublicError,
    PublicHistoryEntry,
    PublicHistoryPage,
    PublicSessionState,
    PublicTurn,
    SavedSessionSummary,
    ScheduledLoop,
    SessionLogSummary,
    SkillSummary,
    TeleportEvent,
    ToolSummary,
    UserDisplayContent,
    VibeCodePickerPurpose,
    VibeCodePickerView,
    VibeCodeProject,
    WorkspaceTrustDecision,
    WorkspaceTrustDetails,
    WorkspaceTrustStatus,
)
from vibe.app_server.review import (
    ReviewFile,
    ReviewFileStatus,
    ReviewHunk,
    ReviewOwner,
    ReviewScope,
    ReviewTarget,
)
from vibe.utils.mcp import MCPAddTransport

SERVER_METHODS: tuple[str, ...] = (
    "account/read",
    "agents/install",
    "agents/list",
    "agents/uninstall",
    "callback/respond",
    "config/fields/read",
    "config/patch",
    "config/proxy/read",
    "config/proxy/write",
    "config/read",
    "config/reload",
    "config/schema",
    "config/thinking/write",
    "connectors/auth/read",
    "connectors/read",
    "connectors/refresh",
    "diagnostics/list",
    "diagnostics/logs/read",
    "feedback/record",
    "feedback/shouldShow",
    "history/list",
    "loops/clear",
    "loops/create",
    "loops/delete",
    "loops/list",
    "mcp/add",
    "mcp/login",
    "mcp/logout",
    "mcp/read",
    "mcp/refresh",
    "mcp/toggle",
    "narration/summarize",
    "projectLinks/create",
    "projectLinks/link",
    "projectLinks/list",
    "projectLinks/picker/load",
    "projectLinks/picker/loadMore",
    "projectLinks/resolveRoot",
    "projectLinks/unlink",
    "review/approve",
    "review/baseline",
    "review/hunks",
    "review/revert",
    "review/state",
    "review/turnDiff",
    "runtime/read",
    "session/agent/update",
    "session/close",
    "session/compact/start",
    "session/continue",
    "session/context/inject",
    "session/delete",
    "session/fork",
    "session/history/clear",
    "session/list",
    "session/log/read",
    "session/read",
    "session/ready/read",
    "session/ready/wait",
    "session/resume",
    "session/rewind",
    "session/rewind/read",
    "session/settings/update",
    "session/start",
    "session/title/update",
    "shell/interrupt",
    "shell/run",
    "skills/list",
    "stats/read",
    "telemetry/record",
    "tools/list",
    "turn/interrupt",
    "turn/start",
    "turn/steer",
    "vibeCode/projects/cancel",
    "vibeCode/projects/create",
    "vibeCode/projects/loadMore",
    "vibeCode/projects/open",
    "vibeCode/projects/recover",
    "vibeCode/projects/select",
    "vibeCode/projects/unlink",
    "vibeCode/teleport/cancel",
    "vibeCode/teleport/push/respond",
    "vibeCode/teleport/start",
    "workspace/prompt/prepare",
    "workspace/trust/decision",
    "workspace/trust/status",
)


class EmptyResponse(ProtocolModel):
    pass


class SessionMCPHttpServer(ProtocolModel):
    transport: Literal["http", "streamable-http"]
    name: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class SessionMCPStdioServer(ProtocolModel):
    transport: Literal["stdio"] = "stdio"
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


type SessionMCPServer = Annotated[
    SessionMCPHttpServer | SessionMCPStdioServer, Field(discriminator="transport")
]


class SessionOptions(ProtocolModel):
    cwd: str | None = None
    workspace_roots: list[str] = Field(default_factory=list)
    agent: str | None = None
    auto_approve: bool = False
    enabled_tools: list[str] | None = None
    disabled_tools: list[str] = Field(default_factory=list)
    max_turns: int | None = None
    max_price: float | None = None
    max_session_tokens: int | None = None
    headless: bool = False
    trust_workspace: bool = False
    mcp_servers: list[SessionMCPServer] = Field(default_factory=list)


class SessionOpenParams(SessionOptions):
    history_limit: int = Field(default=200, ge=1, le=500)


class SessionStartParams(SessionOpenParams):
    pass


class SessionStartResponse(ProtocolModel):
    state: PublicSessionState


class SessionReadParams(ProtocolModel):
    session_id: str
    history_limit: int = Field(default=200, ge=1, le=500)


class SessionReadResponse(ProtocolModel):
    state: PublicSessionState


class SessionResumeParams(SessionOpenParams):
    session_id: str


class SessionResumeResponse(ProtocolModel):
    state: PublicSessionState


class SessionContinueParams(SessionOpenParams):
    pass


class SessionContinueResponse(ProtocolModel):
    state: PublicSessionState


class SessionForkParams(ProtocolModel):
    source_session_id: str
    entry_id: str | None = None
    history_limit: int = Field(default=200, ge=1, le=500)
    attach: bool = True


class SessionForkResponse(ProtocolModel):
    source_session_id: str
    state: PublicSessionState


class SessionCloseParams(ProtocolModel):
    session_id: str


class SessionCloseResponse(ProtocolModel):
    closed: bool = True


class SessionListParams(ProtocolModel):
    cwd: str | None = None


class SessionListResponse(ProtocolModel):
    sessions: list[SavedSessionSummary]


class SessionDeleteParams(ProtocolModel):
    session_id: str


class SessionTitleUpdateParams(ProtocolModel):
    session_id: str
    title: str


class SessionTitleUpdateResponse(ProtocolModel):
    title: str
    updated_at: str | None = None


class HistoryListParams(ProtocolModel):
    session_id: str
    turn_id: str | None = None
    before: str | None = None
    after: str | None = None
    limit: int = Field(default=200, ge=1, le=500)


class HistoryListResponse(ProtocolModel):
    history: PublicHistoryPage


class SessionReadyWaitParams(ProtocolModel):
    session_id: str


class SessionReadyReadParams(ProtocolModel):
    session_id: str


class SessionReadyReadResponse(ProtocolModel):
    ready: bool


class SessionReadyWaitResponse(ProtocolModel):
    ready: bool = True


class AccountReadParams(ProtocolModel):
    session_id: str


class AccountReadResponse(ProtocolModel):
    account: AccountView


class SessionRewindReadParams(ProtocolModel):
    session_id: str
    entry_id: str


class SessionRewindReadResponse(ProtocolModel):
    has_file_changes: bool
    paths: list[str] = Field(default_factory=list)


class SessionRewindParams(ProtocolModel):
    session_id: str
    entry_id: str
    restore_files: bool = False
    inplace: bool = False


class SessionRewindResponse(ProtocolModel):
    message: str
    restore_errors: list[str]
    restored_paths: list[str]
    state: PublicSessionState
    session_log: SessionLogSummary


class ReviewStateParams(ProtocolModel):
    session_id: str


class ReviewStateResponse(ProtocolModel):
    files: list[ReviewFile]
    scopes: list[ReviewScope]


class ReviewBaselineParams(ProtocolModel):
    session_id: str
    path: str


class ReviewBaselineResponse(ProtocolModel):
    content: str


class ReviewTurnDiffParams(ProtocolModel):
    session_id: str
    path: str
    owner: ReviewOwner


class ReviewTurnDiffResponse(ProtocolModel):
    status: ReviewFileStatus
    baseline: str
    current: str


class ReviewHunksParams(ProtocolModel):
    session_id: str
    path: str
    owner: ReviewOwner | None = None


class ReviewHunksResponse(ProtocolModel):
    hunks: list[ReviewHunk]


class ReviewMutationParams(ProtocolModel):
    session_id: str
    target: ReviewTarget


class ConfigReadParams(ProtocolModel):
    session_id: str


class ConfigReadResponse(ProtocolModel):
    config: ConfigView
    base_config: ConfigView
    stripped_history_images: int = 0


class ConfigSchemaReadParams(ProtocolModel):
    pass


class ConfigSchemaReadResponse(ProtocolModel):
    config_schema_version: str
    config_schema: dict[str, JsonValue] = Field(alias="schema")


class ConfigReloadParams(ProtocolModel):
    session_id: str
    reload_runtime: bool = True


class ConfigThinkingWriteParams(ProtocolModel):
    session_id: str
    level: ThinkingLevel


class ConfigProxyReadParams(ProtocolModel):
    session_id: str


class ConfigProxyReadResponse(ProtocolModel):
    settings: ProxySettingsView


class ConfigProxyWriteParams(ProtocolModel):
    session_id: str
    changes: dict[str, str | None]


class AgentsListParams(ProtocolModel):
    session_id: str


class AgentsListResponse(ProtocolModel):
    active: AgentSummary
    agents: list[AgentSummary]


class AgentSwitchParams(ProtocolModel):
    session_id: str
    agent_name: str


type NonNegativeStrictInt = Annotated[StrictInt, Field(ge=0)]


class SessionSettingsUpdateParams(ProtocolModel):
    session_id: str
    max_turns: NonNegativeStrictInt | None = None
    max_tokens: NonNegativeStrictInt | None = None

    @model_validator(mode="after")
    def require_update(self) -> SessionSettingsUpdateParams:
        if self.max_turns is None and self.max_tokens is None:
            raise ValueError("At least one session setting must be provided")
        return self


class RuntimeSnapshot(ProtocolModel):
    config: ConfigView
    base_config: ConfigView
    active_agent: AgentSummary
    agents: list[AgentSummary]
    skills: list[SkillSummary]
    tools: list[ToolSummary]
    stats: AgentStatsSnapshot
    context_window: int
    issues: list[ConfigIssue]
    hooks_count: int
    connectors: ConnectorCounts
    mcp: MCPState


class RuntimeReadParams(ProtocolModel):
    session_id: str


class RuntimeReadResponse(ProtocolModel):
    runtime: RuntimeSnapshot
    session_log: SessionLogSummary
    ready: bool


class RuntimeMutationResponse(ProtocolModel):
    runtime: RuntimeSnapshot


class RuntimeUpdatedParams(ProtocolModel):
    session_id: str
    runtime: RuntimeSnapshot


class ServerWarningParams(ProtocolModel):
    warning: PublicError


class ServerErrorParams(ProtocolModel):
    error: PublicError


class ConfigMutationResponse(RuntimeMutationResponse):
    stripped_history_images: int = 0


class ConfigFieldKind(StrEnum):
    BOOL = auto()
    ENUM = auto()
    INT = auto()
    FLOAT = auto()
    STR = auto()
    LIST = auto()
    COMPLEX = auto()


class ConfigLayerValueWire(ProtocolModel):
    layer: str
    value: JsonValue = None


class ConfigFieldWire(ProtocolModel):
    name: str
    kind: ConfigFieldKind
    description: str
    value: JsonValue = None
    path: str
    popular: bool = False
    enum_choices: list[str] = Field(default_factory=list)
    layer_values: list[ConfigLayerValueWire] = Field(default_factory=list)

    @property
    def origin(self) -> str:
        return self.layer_values[0].layer if self.layer_values else "default"


class ConfigFieldsReadParams(ProtocolModel):
    session_id: str


class ConfigFieldsReadResponse(ProtocolModel):
    fields: list[ConfigFieldWire]
    targets: list[str]


class ConfigPatchOpWire(ProtocolModel):
    op: Literal["set", "remove"]
    path: str
    value: JsonValue = None
    target_layer: str | None = None


class ConfigPatchParams(ProtocolModel):
    session_id: str
    ops: list[ConfigPatchOpWire]
    reason: str = "config screen edit"
    reload_runtime: bool = False


class ConfigPatchResponse(ConfigMutationResponse):
    rejected: bool = False
    failures: list[str] = Field(default_factory=list)


class AgentInstallParams(ProtocolModel):
    session_id: str
    agent_name: str


class SkillsListParams(ProtocolModel):
    session_id: str


class SkillsListResponse(ProtocolModel):
    skills: list[SkillSummary]


class ToolsListParams(ProtocolModel):
    session_id: str


class ToolsListResponse(ProtocolModel):
    tools: list[ToolSummary]


class StatsReadParams(ProtocolModel):
    session_id: str


class StatsReadResponse(ProtocolModel):
    stats: AgentStatsSnapshot
    context_window: int


class DiagnosticsListParams(ProtocolModel):
    session_id: str


class DiagnosticsListResponse(ProtocolModel):
    issues: list[ConfigIssue]
    hooks_count: int


class DiagnosticsLogsReadParams(ProtocolModel):
    session_id: str
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class DiagnosticsLogsReadResponse(ProtocolModel):
    logs: DebugLogPage


class VibeCodeProjectsOpenParams(ProtocolModel):
    session_id: str
    purpose: VibeCodePickerPurpose = "configure"
    prompt: str | None = None


class VibeCodeProjectsOpenResponse(ProtocolModel):
    picker_id: str
    view: VibeCodePickerView
    resolved_project_id: str | None = None


class VibeCodeProjectsLoadMoreParams(ProtocolModel):
    session_id: str
    picker_id: str


class VibeCodeProjectsLoadMoreResponse(ProtocolModel):
    view: VibeCodePickerView
    focus_option_id: str | None = None


class VibeCodeProjectCreateParams(ProtocolModel):
    session_id: str
    picker_id: str
    name: str
    default_branch: str


class VibeCodeProjectCreateResponse(ProtocolModel):
    view: VibeCodePickerView
    project: VibeCodeProject


class VibeCodeProjectSelectParams(ProtocolModel):
    session_id: str
    picker_id: str
    project_id: str


class VibeCodeProjectSelectResponse(ProtocolModel):
    view: VibeCodePickerView
    project: VibeCodeProject


class VibeCodeProjectUnlinkParams(ProtocolModel):
    session_id: str
    picker_id: str


class VibeCodeProjectUnlinkResponse(ProtocolModel):
    view: VibeCodePickerView


class VibeCodeProjectCancelParams(ProtocolModel):
    session_id: str
    picker_id: str


class VibeCodeProjectRecoverParams(ProtocolModel):
    session_id: str
    picker_id: str


class VibeCodeProjectRecoverResponse(ProtocolModel):
    recovered: bool
    view: VibeCodePickerView


class TeleportStartParams(ProtocolModel):
    session_id: str
    picker_id: str
    operation_id: str
    prompt: str | None = None
    project_id: str


class TeleportStartResponse(ProtocolModel):
    operation_id: str


class TeleportCancelParams(ProtocolModel):
    session_id: str
    operation_id: str


class TeleportCancelResponse(ProtocolModel):
    cancelled: bool


class TeleportPushRespondParams(ProtocolModel):
    session_id: str
    operation_id: str
    approved: bool


class TeleportEventParams(ProtocolModel):
    event: TeleportEvent


class ConnectorsReadParams(ProtocolModel):
    session_id: str


class ConnectorsReadResponse(ProtocolModel):
    counts: ConnectorCounts


class ConnectorAuthReadParams(ProtocolModel):
    session_id: str
    name: str


class ConnectorAuthReadResponse(ProtocolModel):
    url: str | None = None


class ConnectorRefreshParams(ProtocolModel):
    session_id: str
    name: str


class ConnectorRefreshResponse(ProtocolModel):
    tool_count: int
    runtime: RuntimeSnapshot


class MCPReadParams(ProtocolModel):
    session_id: str


class MCPReadResponse(ProtocolModel):
    mcp: MCPState


class MCPRefreshParams(ProtocolModel):
    session_id: str


class MCPToggleParams(ProtocolModel):
    session_id: str
    name: str
    source: Literal["server", "connector"]
    disabled: bool
    tool_name: str | None = None


class MCPAddParams(ProtocolModel):
    session_id: str
    url: str
    name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    transport: MCPAddTransport = "streamable-http"


class MCPAddResponse(ProtocolModel):
    name: str
    url: str
    created: bool
    runtime: RuntimeSnapshot


class MCPLogoutParams(ProtocolModel):
    session_id: str
    name: str


class MCPLoginParams(ProtocolModel):
    session_id: str
    name: str


class MCPAuthUrlParams(ProtocolModel):
    name: str
    url: str


class ShellRunParams(ProtocolModel):
    session_id: str
    operation_id: str
    command: str
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)


class ShellRunResponse(ProtocolModel):
    operation_id: str
    command: str
    cwd: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int
    timed_out: bool = False
    interrupted: bool = False


class ShellInterruptParams(ProtocolModel):
    session_id: str
    operation_id: str


class ShellInterruptResponse(ProtocolModel):
    interrupted: bool


class SessionLogReadParams(ProtocolModel):
    session_id: str


class SessionLogReadResponse(ProtocolModel):
    log: SessionLogSummary


class WorkspacePromptPrepareParams(ProtocolModel):
    session_id: str
    message: str
    title_content: list[ContentBlock] | None = None


class WorkspacePromptPrepareResponse(ProtocolModel):
    prompt: PreparedPrompt


class WorkspaceTrustStatusParams(ProtocolModel):
    cwd: str | None = None


class WorkspaceTrustStatusResponse(ProtocolModel):
    status: WorkspaceTrustStatus
    details: WorkspaceTrustDetails | None = None


class WorkspaceTrustDecisionParams(ProtocolModel):
    decision: WorkspaceTrustDecision
    cwd: str | None = None
    session_id: str | None = None


class ProjectLinksListParams(ProtocolModel):
    pass


class ProjectLinksLinkedProject(ProtocolModel):
    project_id: str
    repo_local_paths: list[str]


class ProjectLinksListResponse(ProtocolModel):
    projects: list[ProjectLinksLinkedProject]


type ProjectLinksResolveRootRejectReason = Literal[
    "not_git", "unsupported_remote", "nested_unresolvable", "no_commits"
]


class ProjectLinksResolvedRoot(ProtocolModel):
    repo_local_path: str
    repo_name: str
    current_branch: str | None
    default_branch: str | None


class ProjectLinksResolveRootParams(ProtocolModel):
    root_path: str = Field(min_length=1)


class ProjectLinksResolveRootResponse(ProtocolModel):
    eligible: bool
    reject_reason: ProjectLinksResolveRootRejectReason | None = None
    root: ProjectLinksResolvedRoot | None = None


class ProjectLinksPickerCandidate(ProtocolModel):
    project_id: str
    name: str
    match_kind: Literal["exact_repo", "multi_repo"]
    recommended: bool


class ProjectLinksPickerCandidates(ProtocolModel):
    items: list[ProjectLinksPickerCandidate]
    next_cursor: str | None


class ProjectLinksPickerLoadParams(ProtocolModel):
    root_path: str = Field(min_length=1)


class ProjectLinksSavedLink(ProtocolModel):
    project_id: str
    project_name: str


class ProjectLinksPickerLoadResponse(ProtocolModel):
    root: ProjectLinksResolvedRoot
    saved_link: ProjectLinksSavedLink | None = None
    stale_link_cleared: bool
    candidates: ProjectLinksPickerCandidates


class ProjectLinksPickerLoadMoreParams(ProtocolModel):
    root_path: str = Field(min_length=1)
    cursor: str = Field(min_length=1)


class ProjectLinksPickerLoadMoreResponse(ProtocolModel):
    candidates: ProjectLinksPickerCandidates
    focus_project_id: str | None


class ProjectLinksCreateParams(ProtocolModel):
    root_path: str = Field(min_length=1)
    name: str = Field(min_length=1)
    default_branch: str = Field(min_length=1)


class ProjectLinksLinkParams(ProtocolModel):
    root_path: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    project_name: str = Field(min_length=1)


class ProjectLink(ProtocolModel):
    project_id: str
    project_name: str
    repo_local_path: str


class ProjectLinkMutationResponse(ProtocolModel):
    link: ProjectLink


class ProjectLinksUnlinkParams(ProtocolModel):
    root_path: str = Field(min_length=1)


class ProjectLinksUnlinkResponse(ProtocolModel):
    unlinked: Literal[True]


class LoopsListParams(ProtocolModel):
    session_id: str


class LoopsListResponse(ProtocolModel):
    loops: list[ScheduledLoop]


class LoopsCreateParams(ProtocolModel):
    session_id: str
    interval: str
    prompt: str


class LoopsCreateResponse(ProtocolModel):
    loop: ScheduledLoop


class LoopsDeleteParams(ProtocolModel):
    session_id: str
    loop_id: str


class LoopsDeleteResponse(ProtocolModel):
    loop: ScheduledLoop


class LoopsClearParams(ProtocolModel):
    session_id: str


class LoopsClearResponse(ProtocolModel):
    count: int


class TelemetryRecordParams(ProtocolModel):
    session_id: str
    name: str
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    correlate_last_request: bool = False


class NarrationSummarizeParams(ProtocolModel):
    session_id: str
    user_message: str
    assistant_text: str
    error: str | None = None
    message_id: str | None = None


class NarrationSummarizeResponse(ProtocolModel):
    summary: str | None = None


class FeedbackShouldShowParams(ProtocolModel):
    session_id: str
    pending_user_messages: int = 0


class FeedbackShouldShowResponse(ProtocolModel):
    show: bool


class FeedbackRecordParams(ProtocolModel):
    session_id: str
    action: Literal["asked", "given", "snoozed"]


class TurnStartParams(ProtocolModel):
    session_id: str
    input: list[ContentBlock]
    client_user_message_id: str | None = None
    auto_title: str | None = None
    user_display_content: UserDisplayContent | None = None
    mention_stats: MentionStats | None = None


class TurnStartResponse(ProtocolModel):
    turn: PublicTurn


class TurnSteerParams(ProtocolModel):
    session_id: str
    expected_turn_id: str
    input: list[ContentBlock]
    client_user_message_id: str | None = None
    inject_invoked_skill: bool = True
    mention_stats: MentionStats | None = None


class TurnSteerResponse(ProtocolModel):
    turn_id: str


class TurnInterruptParams(ProtocolModel):
    session_id: str
    expected_turn_id: str


class TurnInterruptResponse(ProtocolModel):
    interrupted: bool


class ContextInjectParams(ProtocolModel):
    session_id: str
    input: list[ContentBlock]
    as_message: bool = False
    inject_invoked_skill: bool = False
    client_user_message_id: str | None = None
    mention_stats: MentionStats | None = None


class ContextInjectResponse(ProtocolModel):
    entries: list[PublicHistoryEntry]


class CallbackCallParams(ProtocolModel):
    callback: PublicCallbackEntry


class CallbackCallResponse(ProtocolModel):
    callback_id: str
    accepted: bool = True


class CallbackRespondParams(ProtocolModel):
    session_id: str
    callback_id: str
    output: CallbackOutput


class CallbackRespondResponse(ProtocolModel):
    status: Literal["accepted", "duplicate"]


class SessionHistoryClearParams(ProtocolModel):
    session_id: str


class SessionHistoryClearResponse(ProtocolModel):
    state: PublicSessionState
    session_log: SessionLogSummary


class SessionCompactParams(ProtocolModel):
    session_id: str
    extra_instructions: str = ""


class SessionCompactResponse(ProtocolModel):
    summary: str
    state: PublicSessionState
    session_log: SessionLogSummary


class EventNotificationParams(ProtocolModel):
    event_id: int = Field(ge=0, strict=True)
    session_id: str
    emitted_at: int


class HistoryEntryAddedParams(EventNotificationParams):
    turn_id: str | None = None
    entry: PublicHistoryEntry


class HistoryEntryUpdatedParams(EventNotificationParams):
    turn_id: str | None = None
    entry_id: str
    patch: list[JsonPatchOperation]


class SessionSnapshotParams(EventNotificationParams):
    state: PublicSessionState


class SessionHandoffParams(EventNotificationParams):
    old_session_id: str
    state: PublicSessionState
    session_log: SessionLogSummary


class SessionCompactedParams(SessionHandoffParams):
    summary_length: int = Field(ge=0)


class SessionContextClearedParams(SessionHandoffParams):
    plan_file_path: str | None = None


class SessionUpdatedParams(EventNotificationParams):
    patch: list[JsonPatchOperation]


class TurnStartedParams(EventNotificationParams):
    turn: PublicTurn


class TurnCompletedParams(EventNotificationParams):
    turn: PublicTurn


class StatsUpdatedParams(EventNotificationParams):
    stats: AgentStatsSnapshot
    context_window: int


class Notification(ProtocolModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, JsonValue]


class ServerRequest(ProtocolModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: StrictInt | StrictStr
    method: str
    params: dict[str, JsonValue]


class ProtocolErrorCode(StrEnum):
    INVALID_REQUEST = auto()
    INVALID_PARAMS = auto()
    NOT_INITIALIZED = auto()
    NOT_FOUND = auto()
    CONFLICT = auto()
    STALE_TURN = auto()
    NOT_STEERABLE = auto()
    COMPACTION_FAILED = auto()
    UNAUTHORIZED = auto()
    FORBIDDEN = auto()
    METHOD_NOT_FOUND = auto()
    INTERNAL_ERROR = auto()


class ProtocolError(ProtocolModel):
    code: ProtocolErrorCode
    message: str
    data: JsonValue = None


class AppServerResponseError(RuntimeError):
    def __init__(self, error: ProtocolError) -> None:
        self.error = error
        super().__init__(error.message)


class JsonRpcProtocolError(RuntimeError):
    pass


class JsonRpcSuccessResponse(ProtocolModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: StrictInt | StrictStr
    result: dict[str, JsonValue]


class JsonRpcErrorResponse(ProtocolModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: StrictInt | StrictStr
    error: ProtocolError


type JsonRpcEnvelope = (
    Notification | ServerRequest | JsonRpcSuccessResponse | JsonRpcErrorResponse
)

_JSON_RPC_ENVELOPE_ADAPTER = TypeAdapter(JsonRpcEnvelope)


def validate_json_rpc_envelope(value: object) -> JsonRpcEnvelope:
    return _JSON_RPC_ENVELOPE_ADAPTER.validate_python(
        value, by_alias=True, by_name=False
    )


def protocol_value(value: ProtocolModel | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, ProtocolModel):
        return value.model_dump(mode="json", by_alias=True)
    return value


def validate_callback_acknowledgement(
    callback_id: str, response: CallbackCallResponse
) -> CallbackCallResponse:
    if callback_id != response.callback_id:
        raise ValueError("Callback acknowledgement does not match the request")
    return response
