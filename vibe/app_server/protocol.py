from __future__ import annotations

from enum import StrEnum, auto
from functools import cache
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
    ServerInfo as ServerInfo,
    TransportKind as TransportKind,
)
from vibe.app_server._model import ProtocolModel
from vibe.app_server.config import ConfigView, ProxySettingsView
from vibe.app_server.models import (
    AccountView,
    AgentStatsSnapshot,
    AgentSummary,
    CallbackOutput,
    ConfigIssue,
    ConnectorCounts,
    ContentBlock,
    DebugLogPage,
    IdentityView,
    JsonPatchOperation,
    MCPState,
    MentionStats,
    PluginCatalogComponent as PluginCatalogComponent,
    PluginCatalogDropped as PluginCatalogDropped,
    PluginCatalogEntry as PluginCatalogEntry,
    PluginCatalogState,
    PluginComponent as PluginComponent,
    PluginComponentKind as PluginComponentKind,
    PluginInfo,
    PreparedPrompt,
    PublicCallbackEntry,
    PublicError,
    PublicHistoryEntry,
    PublicRetryCategory,
    PublicSession,
    PublicSessionState,
    PublicTurn,
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
    "callback/result",
    "config/fields/read",
    "config/proxy/read",
    "config/proxy/write",
    "config/read",
    "config/reload",
    "config/schema",
    "config/write",
    "connectors/auth/read",
    "connectors/read",
    "connectors/refresh",
    "connector_catalog/auth/request",
    "connector_catalog/read",
    "connector_catalog/refresh",
    "connector_catalog/toggle",
    "diagnostics/list",
    "diagnostics/logs/read",
    "events/read",
    "feedback/record",
    "feedback/shouldShow",
    "session/history/get",
    "identity/read",
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
    "mcp_catalog/add",
    "mcp_catalog/login",
    "mcp_catalog/logout",
    "mcp_catalog/read",
    "mcp_catalog/refresh",
    "mcp_catalog/remove",
    "mcp_catalog/toggle",
    "narration/summarize",
    "plugin/info",
    "plugin/reload",
    "plugins/read",
    "plugin_catalog/read",
    "projectLinks/create",
    "projectLinks/inspectRoot",
    "projectLinks/link",
    "projectLinks/list",
    "projectLinks/picker/load",
    "projectLinks/picker/loadMore",
    "projectLinks/resolveRoot",
    "projectLinks/save",
    "projectLinks/unlink",
    "review/approve",
    "review/baseline",
    "review/hunks",
    "review/revert",
    "review/state",
    "review/turnDiff",
    "runtime/read",
    "session/agent/update",
    "session/compact",
    "session/continue",
    "session/context/inject",
    "session/delete",
    "session/fork",
    "session/history/clear",
    "session/history/list",
    "session/list",
    "session/log/read",
    "session/read",
    "session/ready/read",
    "session/ready/wait",
    "session/relocate",
    "session/rename",
    "session/resume",
    "session/rewind",
    "session/rewind/read",
    "session/settings/update",
    "session/shellCommand",
    "session/start",
    "session/stop",
    "session/turns/list",
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
    "workspace/git/checkouts",
    "workspace/git/worktrees/list",
    "workspace/git/worktrees/remove",
    "workspace/prompt/prepare",
    "workspace/trust/decision",
    "workspace/trust/untrustedConfig",
    "workspace/trust/status",
)


class EmptyResponse(ProtocolModel):
    pass


class EventWatermarkResponse(ProtocolModel):
    last_event_id: int = 0


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


class PageRequest(ProtocolModel):
    cursor: str | None = None
    limit: int = Field(default=200, ge=1, le=500)
    direction: Literal["forward", "backward"] = "backward"


class EventsFilter(ProtocolModel):
    session_ids: list[str] = Field(default_factory=list)
    root_session_ids: list[str] = Field(default_factory=list)
    parent_session_ids: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)


class EventsReadParams(ProtocolModel):
    after_event_id: int | None = None
    filters: EventsFilter = Field(default_factory=EventsFilter)
    batch_size: int = Field(default=100, ge=1)


class EventBatch(ProtocolModel):
    type: Literal["events"] = "events"
    events: list[JsonValue] = Field(default_factory=list)


class CompletionConfig(ProtocolModel):
    type: str = "mistral"
    model: str = "mistral-small-latest"


class ToolDefinition(ProtocolModel):
    type: Literal["client_tool"] = "client_tool"
    name: str
    description: str = ""
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] = Field(default_factory=dict)


class HookDefinition(ProtocolModel):
    type: str
    name: str
    matcher: dict[str, JsonValue] = Field(default_factory=dict)


class ExistingWorktreeInput(ProtocolModel):
    kind: Literal["existing"] = "existing"
    cwd: str = Field(min_length=1)


class NewWorktreeInput(ProtocolModel):
    kind: Literal["create"] = "create"
    branch: str = Field(min_length=1)
    name: str = Field(min_length=1)


class AutoWorktreeInput(ProtocolModel):
    kind: Literal["auto"] = "auto"
    prompt: str | None = None


type WorktreeInput = Annotated[
    ExistingWorktreeInput | NewWorktreeInput | AutoWorktreeInput,
    Field(discriminator="kind"),
]


class AgentConfig(ProtocolModel):
    """App-server configuration plus Vibe's runtime launch options."""

    completion: CompletionConfig | None = None
    sandbox: dict[str, JsonValue] | None = None
    instructions: str = ""
    workdir: str | None = None
    tools: list[ToolDefinition] = Field(default_factory=list)
    hooks: list[HookDefinition] = Field(default_factory=list)
    cwd: str | None = None
    workspace_roots: list[str] = Field(default_factory=list)
    worktree: WorktreeInput | None = None
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


SessionOptions = AgentConfig


class SessionOpenParams(ProtocolModel):
    agent_config: AgentConfig = Field(default_factory=AgentConfig)
    history_limit: int = Field(default=200, ge=1, le=500)

    @property
    def cwd(self) -> str | None:
        return self.agent_config.cwd or self.agent_config.workdir


class SessionKind(StrEnum):
    """Lifecycle role of a session as seen by the server.

    ``NORMAL`` — a genuine user-initiated session; emits new-session telemetry
    and is persisted to disk as soon as a turn runs.

    ``EPHEMERAL`` — a throwaway session used to warm up the runtime while the
    in-app picker is shown; it is discarded on resume and must not emit
    new-session telemetry or be counted as a new session.
    """

    NORMAL = auto()
    EPHEMERAL = auto()


class SessionStartParams(SessionOpenParams):
    idempotency_key: str | None = None
    kind: SessionKind = SessionKind.NORMAL


class SessionStartResponse(EventWatermarkResponse):
    state: PublicSessionState


class SessionReadParams(ProtocolModel):
    session_id: str
    history: PageRequest | None = Field(default_factory=PageRequest)
    turns: PageRequest | None = Field(default_factory=PageRequest)

    @property
    def include_history(self) -> bool:
        return self.history is not None

    @property
    def include_turns(self) -> bool:
        return self.turns is not None

    @property
    def history_limit(self) -> int:
        return self.history.limit if self.history is not None else 1

    @property
    def turns_limit(self) -> int:
        return self.turns.limit if self.turns is not None else 1


class SessionReadResponse(EventWatermarkResponse):
    state: PublicSessionState


class SessionResumeParams(SessionOpenParams):
    session_id: str


class SessionResumeResponse(EventWatermarkResponse):
    state: PublicSessionState


class SessionContinueParams(SessionOpenParams):
    """Vibe extension that resumes the latest eligible session."""


class SessionContinueResponse(EventWatermarkResponse):
    state: PublicSessionState


class SessionForkParams(ProtocolModel):
    idempotency_key: str | None = None
    source_session_id: str
    entry_id: str | None = None
    agent_config: AgentConfig | None = None
    after_turn_id: str | None = None
    history_limit: int = Field(default=200, ge=1, le=500)
    attach: bool = True


class SessionForkResponse(EventWatermarkResponse):
    source_session_id: str
    state: PublicSessionState


class SessionStopParams(ProtocolModel):
    session_id: str
    reason: str | None = None


class SessionStopResponse(ProtocolModel):
    closed: bool = True


class SessionCloseParams(ProtocolModel):
    session_id: str


class SessionCloseResponse(ProtocolModel):
    closed: bool = True


class SessionListParams(ProtocolModel):
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    root_session_id: str | None = None
    parent_session_id: str | None = None
    cwd: str | None = None


class SessionListResponse(ProtocolModel):
    items: list[PublicSession] = Field(default_factory=list)
    next_cursor: str | None = None
    previous_cursor: str | None = None
    # The session `--continue` would resume: the tty-scoped last-session
    # pointer when it still exists, else the most recently updated session.
    # Resolved server-side so the pointer stays behind the app-server boundary.
    continue_session_id: str | None = None

    @property
    def data(self) -> list[PublicSession]:
        return self.items


class SessionDeleteParams(ProtocolModel):
    session_id: str


class SessionTitleUpdateParams(ProtocolModel):
    session_id: str
    title: str


class SessionTitleUpdateResponse(ProtocolModel):
    title: str
    updated_at: str | None = None
    last_event_id: int | None = None


class SessionHistoryListParams(ProtocolModel):
    session_id: str
    turn_id: str | None = None
    page: PageRequest = Field(default_factory=PageRequest)

    @property
    def cursor(self) -> str | None:
        return self.page.cursor

    @property
    def limit(self) -> int:
        return self.page.limit

    @property
    def sort_direction(self) -> Literal["forward", "backward"]:
        return self.page.direction


class SessionHistoryListResponse(ProtocolModel):
    items: list[PublicHistoryEntry] = Field(default_factory=list)
    next_cursor: str | None = None
    previous_cursor: str | None = None

    @property
    def data(self) -> list[PublicHistoryEntry]:
        return self.items

    @property
    def backwards_cursor(self) -> str | None:
        return self.previous_cursor


class SessionHistoryGetParams(ProtocolModel):
    session_id: str
    history_limit: int = Field(default=200, ge=1, le=500)


class SessionHistoryGetResponse(ProtocolModel):
    history: list[PublicHistoryEntry]


class SessionTurnsListParams(ProtocolModel):
    session_id: str
    page: PageRequest = Field(default_factory=PageRequest)

    @property
    def cursor(self) -> str | None:
        return self.page.cursor

    @property
    def limit(self) -> int:
        return self.page.limit

    @property
    def sort_direction(self) -> Literal["forward", "backward"]:
        return self.page.direction


class SessionTurnsListResponse(ProtocolModel):
    items: list[PublicTurn] = Field(default_factory=list)
    next_cursor: str | None = None
    previous_cursor: str | None = None

    @property
    def data(self) -> list[PublicTurn]:
        return self.items

    @property
    def backwards_cursor(self) -> str | None:
        return self.previous_cursor


class SessionShellCommandParams(ProtocolModel):
    session_id: str
    command: str | None = None
    cwd: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    operation_id: str | None = None
    action: Literal["run", "interrupt"] = "run"

    @model_validator(mode="after")
    def validate_action(self) -> SessionShellCommandParams:
        if self.action == "run" and (self.command is None or not self.command.strip()):
            raise ValueError("command is required for action='run'")
        if self.action == "interrupt" and self.operation_id is None:
            raise ValueError("operation_id is required for action='interrupt'")
        return self


class SessionShellCommandResponse(ProtocolModel):
    accepted: Literal[True] = True
    last_event_id: int


class SessionReadyWaitParams(ProtocolModel):
    session_id: str


class SessionReadyReadParams(ProtocolModel):
    session_id: str


class SessionReadyReadResponse(ProtocolModel):
    ready: bool


class SessionReadyWaitResponse(ProtocolModel):
    ready: bool = True
    init_duration_ms: int | None = None


class AccountReadParams(ProtocolModel):
    session_id: str


class AccountReadResponse(ProtocolModel):
    account: AccountView


class IdentityReadParams(ProtocolModel):
    session_id: str


class IdentityReadResponse(ProtocolModel):
    identity: IdentityView | None = None


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


class SessionRelocateParams(ProtocolModel):
    session_id: str
    cwd: str


class SessionRelocateResponse(ProtocolModel):
    state: PublicSessionState


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


class ConfigSchemaReadParams(ProtocolModel):
    pass


class ConfigSchemaReadResponse(ProtocolModel):
    config_schema_version: str
    config_schema: dict[str, JsonValue] = Field(alias="schema")


class ConfigReloadParams(ProtocolModel):
    session_id: str
    reload_runtime: bool = True


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


class PluginInfoParams(ProtocolModel):
    session_id: str


class PluginInfoResponse(ProtocolModel):
    info: PluginInfo


class PluginReloadParams(ProtocolModel):
    session_id: str


class PluginReloadResponse(ProtocolModel):
    """Empty: reload allocates no identity, and ``plugin/info`` reads the result."""


class PluginCatalogReadParams(ProtocolModel):
    session_id: str


class PluginCatalogReadResponse(ProtocolModel):
    plugins: PluginCatalogState


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


class TurnRetryingParams(ProtocolModel):
    session_id: str
    category: PublicRetryCategory
    detail: str


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
    # Display labels for specific raw values, e.g. {"": "default (currently …)"}.
    # Used for the value column and choice picker; the stored value is unchanged.
    value_labels: dict[str, str] = Field(default_factory=dict)
    layer_values: list[ConfigLayerValueWire] = Field(default_factory=list)

    @property
    def origin(self) -> str:
        return self.layer_values[0].layer if self.layer_values else "default"


class ConfigFieldsReadParams(ProtocolModel):
    session_id: str


class ConfigFieldsReadResponse(ProtocolModel):
    fields: list[ConfigFieldWire]
    targets: list[str]


class ConfigWriteOpWire(ProtocolModel):
    op: Literal["set", "remove"]
    path: str
    value: JsonValue = None
    target_layer: str | None = None


class ConfigWriteParams(ProtocolModel):
    session_id: str
    ops: list[ConfigWriteOpWire]
    reason: str = "config write"
    reload_runtime: bool = False


class ConfigWriteResponse(ConfigMutationResponse):
    rejected: bool = False
    failures: list[str] = Field(default_factory=list)


class ConfigReadParams(ProtocolModel):
    session_id: str | None = None
    cwd: str | None = None


class ConfigReadResponse(ProtocolModel):
    config: ConfigView
    stripped_history_images: int = 0
    skills_count: int = 0
    hooks_count: int = 0
    mcp_servers_total: int = 0
    mcp_servers_enabled: int = 0


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


class ConnectorCatalogToolView(ProtocolModel):
    name: str
    description: str | None = None


class ConnectorCatalogEntryView(ProtocolModel):
    alias: str
    display_name: str
    readiness: Literal["ready", "needs_auth", "needs_setup", "unavailable"]
    auth_action: Literal["none", "oauth", "credentials_setup", "unknown"]
    tools: list[ConnectorCatalogToolView] = Field(default_factory=list)
    diagnostic: str | None = None


class ConnectorCatalogView(ProtocolModel):
    disposition: Literal["memory", "fresh_cache", "not_loaded", "unavailable"]
    catalog_revision: str | None = None
    connectors: list[ConnectorCatalogEntryView] = Field(default_factory=list)


class ConnectorSelectionView(ProtocolModel):
    alias: str
    disabled: bool
    disabled_tools: list[str] = Field(default_factory=list)
    state: Literal["resolved", "pending"]


class SessionConnectorToolView(ProtocolModel):
    name: str
    description: str | None = None
    enabled: bool


class SessionConnectorSourceView(ProtocolModel):
    alias: str
    display_name: str
    status: Literal["disabled", "connected", "needs_auth", "needs_setup", "unavailable"]
    tools: list[SessionConnectorToolView] = Field(default_factory=list)
    error: str | None = None


class SessionConnectorStateView(ProtocolModel):
    accepted_catalog_revision: str
    accepted_selection_revision: str
    route_revision: str
    sources: list[SessionConnectorSourceView] = Field(default_factory=list)


class ConnectorCatalogReadParams(ProtocolModel):
    session_id: str | None = None


class ConnectorCatalogReadResponse(ProtocolModel):
    catalog: ConnectorCatalogView
    selections: list[ConnectorSelectionView] = Field(default_factory=list)
    session: SessionConnectorStateView | None = None


class ConnectorCatalogRefreshParams(ProtocolModel):
    session_id: str | None = None


class ConnectorCatalogMutationResponse(ProtocolModel):
    catalog_revision: str | None = None
    selection_revision: str | None = None
    accepted_catalog_revision: str | None = None
    accepted_selection_revision: str | None = None
    route_revision: str | None = None
    runtime: RuntimeSnapshot | None = None
    pending_selection: bool = False


class ConnectorCatalogToggleParams(ProtocolModel):
    alias: str
    disabled: bool
    tool_name: str | None = None
    session_id: str | None = None


class ConnectorCatalogAuthRequestParams(ProtocolModel):
    session_id: str
    alias: str


class ConnectorCatalogAuthRequestResponse(ProtocolModel):
    request_id: str
    session_id: str
    alias: str
    accepted_catalog_revision: str


class ConnectorAuthRequiredParams(ProtocolModel):
    session_id: str
    alias: str
    accepted_catalog_revision: str
    reason: Literal["needs_auth", "needs_setup", "gateway_rejected"]


class ConnectorAuthUrlParams(ConnectorAuthRequiredParams):
    request_id: str
    url: str


class ConnectorAuthFailedParams(ConnectorAuthRequiredParams):
    request_id: str
    code: Literal["auth_url_unavailable", "stale_request"]


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
    session_id: str | None = None
    name: str
    source: Literal["server", "connector"]
    disabled: bool
    tool_name: str | None = None


class MCPAddParams(ProtocolModel):
    session_id: str | None = None
    url: str
    name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    transport: MCPAddTransport = "streamable-http"


class MCPAddResponse(ProtocolModel):
    name: str
    url: str
    created: bool
    runtime: RuntimeSnapshot | None = None


class MCPCatalogMutationResponse(ProtocolModel):
    runtime: RuntimeSnapshot | None = None


class MCPRemoveParams(ProtocolModel):
    session_id: str | None = None
    name: str


class MCPRemoveResponse(ProtocolModel):
    name: str
    removed: bool
    runtime: RuntimeSnapshot | None = None


class MCPLogoutParams(ProtocolModel):
    session_id: str | None = None
    name: str


class MCPLoginParams(ProtocolModel):
    session_id: str | None = None
    name: str


class MCPAuthUrlParams(ProtocolModel):
    name: str
    url: str


class MCPAuthRequiredParams(ProtocolModel):
    session_id: str
    name: str
    descriptor_revision: str
    observed_connection_revision: str | None = None


class ShellRunParams(ProtocolModel):
    """Internal DTO driving ``ShellController.run`` (no longer a wire model)."""

    session_id: str
    operation_id: str
    command: str
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    cwd: str | None = None


class ShellRunResponse(ProtocolModel):
    """Internal DTO carrying a shell result to the effect/context builders."""

    operation_id: str
    command: str
    cwd: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int
    timed_out: bool = False
    interrupted: bool = False


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


class WorkspaceWorktreeListParams(ProtocolModel):
    cwd: str = Field(min_length=1)
    # Off by default because this listing sits on the read path: it resolves
    # the checkout behind every session read and enumerates a project's
    # directories for every session list. The details cost a merge base and a
    # diff per branch plus a second repository open, which only a caller that
    # renders them should pay.
    include_details: bool = False


class WorkspaceGitBranchChanges(ProtocolModel):
    additions: int
    deletions: int


class WorkspaceLinkedWorktree(ProtocolModel):
    name: str
    branch: str
    cwd: str
    root: str
    repo_root: str
    # Absent unless asked for, and null when there is no base to measure
    # against, which is not the same as a branch that has changed nothing.
    branch_changes: WorkspaceGitBranchChanges | None = None


class WorkspaceWorktreeListResponse(ProtocolModel):
    worktrees: list[WorkspaceLinkedWorktree]
    # The branch the main checkout is on. Absent unless details were asked for,
    # and null for a detached one. The worktree entries never name it: this
    # listing reports the linked worktrees, and the main checkout is not one.
    repository_branch: str | None = None
    # Where the position this listing was taken from sits in the main checkout,
    # under the same checks the worktree entries pass. Null when it does not
    # sit there at all -- a subdirectory that exists only on a feature branch
    # has no counterpart. A caller offering the main checkout as a destination
    # must take this rather than joining the root itself, because a path this
    # omits is one a move would refuse.
    repository_cwd: str | None = None


# No session_id on the wire: the caller is deleting a session that has already
# closed and dropped its holder, and accepting one would let a client name an
# arbitrary holder file to unlink.
class WorkspaceWorktreeRemoveParams(ProtocolModel):
    cwd: str = Field(min_length=1)


# Spelled out here rather than imported from vibe.core.git.worktree: the protocol
# is the wire contract and must not pull core into the app-server clients.
type WorktreeRemoveOutcome = Literal[
    "removed",
    "kept_dirty",
    "kept_in_use",
    "kept_unmanaged",
    # Distinct from kept_unmanaged: the worktree is Vibe's and the removal
    # itself failed. Collapsing the two would report a failure as "not ours".
    "kept_error",
    "not_found",
]


class WorkspaceWorktreeRemoveResponse(ProtocolModel):
    # A kept worktree is a normal outcome the caller has to render, not a fault,
    # so every case answers with a result rather than a JSON-RPC error.
    outcome: WorktreeRemoveOutcome
    root: str | None = None
    branch: str | None = None
    branch_deleted: bool = False
    reasons: list[str] = Field(default_factory=list)


class WorkspaceGitCheckoutsParams(ProtocolModel):
    # Every repository the project links, asked for together, because which one
    # holds the session cannot be decided from any single one. A managed
    # worktree lives outside the repository it belongs to, and a repository
    # linked inside another would otherwise let both claim the session.
    repo_local_paths: list[str]
    # Absent for a session with no working directory, which on a cloud host is
    # every session.
    session_cwd: str | None = None


class WorkspaceGitCheckout(ProtocolModel):
    repo_local_path: str
    # False when the repository could not be read; `message` says why and the
    # rest is absent. Carried rather than raised so one unreadable repository
    # does not cost the answer for the others.
    ok: bool
    # The repository the session is standing in. At most one is.
    is_primary: bool = False
    repo_url: str | None = None
    root: str | None = None
    # Absent when the session sits in the repository's own checkout rather than
    # in one of its worktrees.
    worktree: str | None = None
    branch: str | None = None
    base_branch: str | None = None
    message: str | None = None


class WorkspaceGitCheckoutsResponse(ProtocolModel):
    checkouts: list[WorkspaceGitCheckout] = Field(default_factory=list)


class WorkspaceTrustDecisionParams(ProtocolModel):
    decision: WorkspaceTrustDecision
    cwd: str | None = None
    session_id: str | None = None


class WorkspaceUntrustedConfigParams(ProtocolModel):
    cwd: str | None = None


class WorkspaceUntrustedConfigResponse(ProtocolModel):
    dirs: list[str] = Field(default_factory=list)
    settings_path: str = ""


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


class ProjectLinksInspectRootParams(ProtocolModel):
    root_path: str = Field(min_length=1)


class ProjectLinksInspectedRoot(ProjectLinksResolvedRoot):
    repo_url: str


class ProjectLinksSavedLink(ProtocolModel):
    project_id: str
    project_name: str


class ProjectLinksInspectRootResponse(ProtocolModel):
    eligible: bool
    reject_reason: ProjectLinksResolveRootRejectReason | None = None
    root: ProjectLinksInspectedRoot | None = None
    saved_link: ProjectLinksSavedLink | None = None
    stale_link_cleared: bool
    stale_link_clear_failed: bool = False


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


class ProjectLinksSaveParams(ProtocolModel):
    root_path: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    expected_repo_url: str = Field(min_length=1)


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
    idempotency_key: str | None = None
    session_id: str
    message: list[ContentBlock]
    injected: bool = False
    client_user_message_id: str | None = None
    auto_title: str | None = None
    user_display_content: UserDisplayContent | None = None
    mention_stats: MentionStats | None = None

    @property
    def input(self) -> list[ContentBlock]:
        return self.message


class TurnStartResponse(EventWatermarkResponse):
    turn: PublicTurn


class TurnSteerParams(ProtocolModel):
    idempotency_key: str | None = None
    session_id: str
    expected_turn_id: str
    message: list[ContentBlock]
    client_user_message_id: str | None = None
    inject_invoked_skill: bool = True
    mention_stats: MentionStats | None = None

    @property
    def input(self) -> list[ContentBlock]:
        return self.message


class TurnSteerResponse(EventWatermarkResponse):
    accepted: Literal[True] = True


class TurnInterruptParams(ProtocolModel):
    session_id: str
    expected_turn_id: str


class TurnInterruptResponse(EventWatermarkResponse):
    accepted: Literal[True] = True


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


class CallbackResultError(ProtocolModel):
    message: str
    code: str | None = None
    details: JsonValue = None


class CallbackResult(ProtocolModel):
    callback_id: str
    output: JsonValue = None
    error: CallbackResultError | None = None


class CallbackResultParams(ProtocolModel):
    session_id: str
    result: CallbackResult

    @property
    def callback_id(self) -> str:
        return self.result.callback_id


class CallbackResultResponse(EventWatermarkResponse):
    accepted: Literal[True] = True


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
    CALLBACK_CLOSED = auto()
    COMPACTION_FAILED = auto()
    UNAUTHORIZED = auto()
    FORBIDDEN = auto()
    METHOD_NOT_FOUND = auto()
    NOT_IMPLEMENTED = auto()
    STALE_CURSOR = auto()
    INTERNAL_ERROR = auto()


class InvalidParamsIssue(ProtocolModel):
    path: list[str | int]
    message: str


class InvalidParamsData(ProtocolModel):
    error_count: int
    issues: list[InvalidParamsIssue]


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


@cache
def _json_rpc_envelope_adapter() -> TypeAdapter[JsonRpcEnvelope]:
    return TypeAdapter(JsonRpcEnvelope)


def validate_json_rpc_envelope(value: object) -> JsonRpcEnvelope:
    return _json_rpc_envelope_adapter().validate_python(
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
