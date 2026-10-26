from __future__ import annotations

import base64
import binascii
from enum import StrEnum, auto
from functools import cache
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import (
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
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
    PlatformCredentialsCapability as PlatformCredentialsCapability,
    PlatformCredentialsInvalidatedParams as PlatformCredentialsInvalidatedParams,
    PlatformCredentialsResolveParams as PlatformCredentialsResolveParams,
    PlatformCredentialsResolveResponse as PlatformCredentialsResolveResponse,
    PlatformCredentialsSnapshot as PlatformCredentialsSnapshot,
    PlatformCredentialsUnavailable as PlatformCredentialsUnavailable,
    ServerInfo as ServerInfo,
    TransportKind as TransportKind,
)
from vibe.app_server._model import ProtocolModel
from vibe.app_server.config import ConfigView, ProxySettingsView
from vibe.app_server.models import (
    AccountView,
    AgentStatsSnapshot,
    AgentSummary,
    ConfigIssue,
    ConnectorCounts,
    ContentBlock,
    DebugLogPage,
    IdentityView,
    ImportHarness,
    ImportHarnessDiscovery,
    ImportJob,
    JsonPatchOperation,
    MCPState,
    MentionStats,
    MessageAnnotations as MessageAnnotations,
    PluginCatalogComponent as PluginCatalogComponent,
    PluginCatalogDropped as PluginCatalogDropped,
    PluginCatalogEntry as PluginCatalogEntry,
    PluginCatalogState,
    PluginComponent as PluginComponent,
    PluginComponentKind as PluginComponentKind,
    PluginInfo,
    PreparedPrompt,
    PublicCallbackEntry,
    PublicChildSession,
    PublicError,
    PublicHistoryEntry,
    PublicRetryCategory,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnQueue,
    ScheduledLoop,
    SessionContentBlock,
    SessionEmbeddedResourceContentBlock as SessionEmbeddedResourceContentBlock,
    SessionImageContentBlock as SessionImageContentBlock,
    SessionLogSummary,
    SessionResourceLinkContentBlock as SessionResourceLinkContentBlock,
    SessionTextContentBlock as SessionTextContentBlock,
    SkillCatalogEntry,
    SkillDetailView,
    SkillSummary,
    SkillUpdateView,
    SkillVersionView,
    TeleportEvent,
    ToolSummary,
    TurnContextInputEntry as TurnContextInputEntry,
    TurnInputEntry,
    TurnUserInputEntry,
    UserDisplayContent,
    VibeCodePickerPurpose,
    VibeCodePickerView,
    VibeCodeProject,
    WorkspaceTrustDecision,
    WorkspaceTrustDetails,
    WorkspaceTrustStatus,
    validate_turn_input_entries,
)
from vibe.app_server.provider_auth import ProviderAuthView
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
    "artifacts/list",
    "artifacts/markViewed",
    "artifacts/sync",
    "callback/result",
    "config/effective/read",
    "config/fields/read",
    "config/proxy/read",
    "config/proxy/write",
    "config/model/write",
    "config/read",
    "config/reload",
    "config/schema",
    "config/write",
    "connectors/auth/read",
    "connectors/read",
    "connectors/refresh",
    "connector_catalog/auth/request",
    "connector_catalog/connect",
    "connector_catalog/read",
    "connector_catalog/refresh",
    "connector_catalog/toggle",
    "diagnostics/logs/read",
    "events/read",
    "feedback/record",
    "feedback/shouldShow",
    "session/history/get",
    "identity/read",
    "imports/discover",
    "imports/read",
    "imports/start",
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
    "providerAuth/read",
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
    "session/backgroundProcess/output",
    "session/backgroundProcess/stop",
    "session/compact",
    "session/continue",
    "session/context/inject",
    "session/delete",
    "session/fork",
    "session/history/clear",
    "session/history/list",
    "session/lease/subscribe",
    "session/list",
    "session/log/read",
    "session/markAsSeen",
    "session/pin",
    "session/archive",
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
    "session/subagents/interrupt",
    "session/subagents/read",
    "session/subagents/wait",
    "session/title/update",
    "session/turns/list",
    "setup/status",
    "setup/store-credential",
    "setup/submit-choices",
    "shell/interrupt",
    "shell/run",
    "skills/catalog",
    "skills/convertLocal",
    "skills/detail",
    "skills/import",
    "skills/installed",
    "skills/list",
    "skills/remove",
    "skills/setAlias",
    "skills/setEnabled",
    "skills/setLatest",
    "skills/setVersion",
    "skills/updates",
    "skills/versions",
    "telemetry/record",
    "session/turn/enqueue",
    "session/turn/queue/edit/cancel",
    "session/turn/queue/edit/abandon",
    "session/turn/queue/edit/save",
    "session/turn/queue/edit/start",
    "session/turn/queue/read",
    "session/turn/queue/remove",
    "session/turn/queue/replace",
    "session/turn/queue/steer",
    "session/turn/queue/resume",
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
    "workspace/git/worktrees/limit/update",
    "workspace/git/worktrees/list",
    "workspace/git/worktrees/prune",
    "workspace/git/worktrees/remove",
    "workspace/prompt/prepare",
    "workspace/trust/decision",
    "workspace/trust/untrustedConfig",
    "workspace/trust/status",
)

EXPERIMENTAL_SERVER_METHODS: frozenset[str] = frozenset({
    "session/subagents/interrupt",
    "session/subagents/read",
    "session/subagents/wait",
})
"""Advertised methods whose shapes may still change in a minor release.

A client that calls one should tolerate its removal: the method is then
absent from the ``initialize`` method list, and a call fails as unknown.
"""


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

    @field_validator("cwd", "workdir")
    @classmethod
    def _reject_empty_cwd(cls, value: str | None) -> str | None:
        # An empty cwd would silently resolve to the server process cwd,
        # making a directory the caller never named trustable. A client
        # that wants the default omits the field instead.
        if value == "":
            raise ValueError("Session cwd must not be empty")
        return value


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


class SessionStartTurn(ProtocolModel):
    message: list[ContentBlock] = Field(min_length=1)
    client_user_message_id: str | None = None


class SessionStartParams(SessionOpenParams):
    idempotency_key: str | None = None
    kind: SessionKind = SessionKind.NORMAL
    # Applied before the first turn, as the matching mid-session changes are.
    title: str | None = Field(default=None, min_length=1)
    model: str | None = None
    reasoning_effort: str | None = None
    # As on `config/model/write`: otherwise the model and level also become
    # the user's default.
    session_only: bool = False
    first_turn: SessionStartTurn | None = None
    # The session that started this one to hand it first_turn. The new session
    # cannot start sessions until the user writes to it, so started sessions
    # cannot chain on their own.
    started_by_session_id: str | None = None


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


class SessionLeaseHolder(ProtocolModel):
    process_id: int | None = None


class SessionReadResponse(EventWatermarkResponse):
    state: PublicSessionState
    lease_holder: SessionLeaseHolder | None = None


class SessionLeaseUpdate(ProtocolModel):
    session_id: str
    lease_holder: SessionLeaseHolder | None = None


class SessionLeaseSubscribeResponse(ProtocolModel):
    held: list[SessionLeaseUpdate]


class SessionLeaseEnded(ProtocolModel):
    pass


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


class SessionSubagentsWaitParams(ProtocolModel):
    """Experimental: wait until no subagent of the session has a turn running.

    Starts no turn of the session's own: a subagent's late result stays
    queued for it.
    """

    session_id: str
    timeout_ms: int = Field(ge=0, le=86_400_000)


class SubagentRef(ProtocolModel):
    session_id: str
    name: str
    agent_type: str | None = None


class SessionSubagentsWaitResponse(ProtocolModel):
    """``settled`` is false when the timeout ran out first; ``running`` lists
    the subagents still running then.
    """

    settled: bool
    running: list[SubagentRef] = Field(default_factory=list)


class SessionSubagentsInterruptParams(ProtocolModel):
    """Experimental: interrupt every running subagent of the session.

    ``reason`` becomes the interrupted turn's reason. Does not wait for the
    subagents to settle; ``session/subagents/wait`` does.
    """

    session_id: str
    reason: str = Field(min_length=1)


class SubagentInterruptOutcome(StrEnum):
    INTERRUPTED = "interrupted"
    NOT_RUNNING = "notRunning"
    FAILED = "failed"


class SubagentInterruptResult(ProtocolModel):
    """One subagent that was running. ``notRunning``: it ended on its own
    first. ``failed``: the interrupt raised ``error``, and the subagent may
    still be running.
    """

    session_id: str
    name: str
    outcome: SubagentInterruptOutcome
    error: str | None = None


class SessionSubagentsInterruptResponse(ProtocolModel):
    results: list[SubagentInterruptResult] = Field(default_factory=list)


class SessionSubagentsReadParams(ProtocolModel):
    """Experimental: what each subagent of the session did, in spawn order.

    Each store read is given up when ``timeoutMs`` runs out: the subagent is
    still listed, without the fields that read would have filled.
    """

    session_id: str
    timeout_ms: int = Field(default=5_000, ge=0, le=600_000)


class SubagentRunStatus(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SubagentUsage(ProtocolModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0


class SubagentRun(ProtocolModel):
    """One subagent of the session.

    ``spawnCallId`` is the model's tool call that spawned it. ``sessionDir``
    is its store, when one is on disk. ``model`` is the provider model its
    agent called, absent unless this process saw it call exactly one.
    ``usage`` and ``costUsd`` are absent when its store could not be read in
    time or is gone. ``costUsd`` is priced at the rates of ``pricingModel``,
    a configured model's alias: the subagent's own model, or the root's when
    that is unknown, and ``pricingFallback`` is then true. ``steps`` counts
    its agent model calls this process saw, absent when none.
    """

    session_id: str
    parent_session_id: str
    root_session_id: str
    name: str
    agent_type: str | None = None
    spawn_action_id: str
    spawn_call_id: str | None = None
    status: SubagentRunStatus
    session_dir: str | None = None
    model: str | None = None
    usage: SubagentUsage | None = None
    cost_usd: float | None = None
    pricing_model: str | None = None
    pricing_fallback: bool = False
    steps: int | None = None


class SessionSubagentsReadResponse(ProtocolModel):
    subagents: list[SubagentRun] = Field(default_factory=list)


class SessionListParams(ProtocolModel):
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    root_session_id: str | None = None
    parent_session_id: str | None = None
    cwd: str | None = None
    # Union of `cwd` matching over several checkouts.
    cwds: list[str] | None = None
    # ``True`` keeps only pinned sessions, ``False`` only unpinned ones, and
    # ``None`` asks for both.
    pinned: bool | None = None
    include_archived: bool = False


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


class SessionArchiveParams(ProtocolModel):
    session_id: str
    archived: bool


class SessionArchiveResponse(ProtocolModel):
    archived_at: int | None = None


class SessionTitleUpdateParams(ProtocolModel):
    session_id: str
    title: str


class SessionTitleUpdateResponse(ProtocolModel):
    title: str
    updated_at: str | None = None
    last_event_id: int | None = None


class SessionPinParams(ProtocolModel):
    session_id: str
    pinned: bool


class SessionPinResponse(ProtocolModel):
    # Absent while the session is unpinned, so the response says both whether
    # the session is pinned and, when it is, how it should sort against the
    # rest of the shelf.
    pinned_at: int | None = None


class SessionMarkAsSeenParams(ProtocolModel):
    session_id: str


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


class ImportJobStartParams(ProtocolModel):
    harnesses: list[ImportHarness] = Field(min_length=1)

    @field_validator("harnesses")
    @classmethod
    def validate_unique_harnesses(
        cls, harnesses: list[ImportHarness]
    ) -> list[ImportHarness]:
        if len(harnesses) != len(set(harnesses)):
            raise ValueError("harnesses must not contain duplicates")
        return harnesses


class ImportDiscoveryParams(ProtocolModel):
    pass


class ImportDiscoveryResponse(ProtocolModel):
    harnesses: list[ImportHarnessDiscovery]


class ImportJobStartResponse(ProtocolModel):
    job: ImportJob


class ImportJobReadParams(ProtocolModel):
    after_event_id: int = Field(default=0, ge=0, strict=True)
    wait_ms: int = Field(default=0, ge=0, le=5_000, strict=True)


class ImportJobReadResponse(ProtocolModel):
    event_id: int = Field(ge=0, strict=True)
    job: ImportJob | None


class ImportJobUpdatedParams(ProtocolModel):
    event_id: int = Field(ge=0, strict=True)
    job: ImportJob | None


class BackgroundProcessStopParams(ProtocolModel):
    session_id: str
    process_id: str


class BackgroundProcessStopResponse(ProtocolModel):
    process_id: str
    status: Literal["running", "completed", "failed", "stopped", "orphaned"]
    exit_code: int | None = None


class BackgroundProcessOutputParams(ProtocolModel):
    session_id: str
    process_id: str
    from_end: bool
    cursor: int = Field(ge=0, strict=True)
    wait_ms: int = Field(ge=0, le=5_000, strict=True)
    max_bytes: int = Field(ge=1, le=64_000, strict=True)


class BackgroundProcessOutputAvailableResponse(ProtocolModel):
    availability: Literal["available"] = "available"
    process_id: str
    output_base64: str
    output_start_cursor: int = Field(ge=0, strict=True)
    next_cursor: int = Field(ge=0, strict=True)
    bytes_available: int = Field(ge=0, strict=True)
    has_more: bool
    truncated_before: bool
    is_final: bool

    @model_validator(mode="after")
    def validate_cursor_range(self) -> Self:
        if not (self.output_start_cursor <= self.next_cursor <= self.bytes_available):
            raise ValueError("process output cursors are not ordered")
        try:
            output = base64.b64decode(self.output_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("process output is not valid base64") from error
        if len(output) > self.next_cursor - self.output_start_cursor:
            raise ValueError("process output bytes exceed its cursor range")
        return self


class BackgroundProcessOutputUnavailableResponse(ProtocolModel):
    availability: Literal["unavailable"] = "unavailable"
    process_id: str


BackgroundProcessOutputResponse = Annotated[
    BackgroundProcessOutputAvailableResponse
    | BackgroundProcessOutputUnavailableResponse,
    Field(discriminator="availability"),
]


class SessionReadyWaitParams(ProtocolModel):
    session_id: str
    # Also wait for the connector and MCP loads, each bounded by its own
    # timeout: for a client that sends one message and never a second, which
    # would otherwise run without tools found after it started.
    integrations: bool = False


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


class ProviderAuthReadParams(ProtocolModel):
    session_id: str


class ProviderAuthReadResponse(ProtocolModel):
    auth: ProviderAuthView


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


# --- Session artifacts (mirrors ts/packages/code-server-contract/src/artifact) ---
# Spelled out here rather than imported from vibe.core.session.artifacts, same
# as the other wire models: the wire contract is the stable surface, the core
# models are the storage format behind it.

PullRequestLifecycleStateWire = Literal["draft", "open", "merged", "closed"]


class PublicPullRequestSharedEnrichment(ProtocolModel):
    """Enrichment fields shared by both PR/MR detail variants.

    ``pr_status`` and ``checks`` arrive in Phase 4 (provider sync); they stay
    optional on the wire — shared with cloud sessions where Phase 4 does not
    run, and optional in both directions for rolling deploys (Zod strips
    unknown keys; older clients drop them, newer clients parse payloads
    without them).

    ``pr_status`` and ``checks`` are app-server-wire-only: the Rust CLI's
    bottom bar and artifacts modal read them here, while the Code Web
    contract (``code-server-contract/artifact/details``) does not declare
    them — TS consumers derive the status from the raw ``checks_status`` /
    ``review_decision`` / ``merge_status`` fields instead.
    """

    source_branch: str | None = None
    target_branch: str | None = None
    author: str | None = None
    state: PullRequestLifecycleStateWire | None = None
    provider_created_at: str | None = None
    provider_updated_at: str | None = None
    provider_closed_at: str | None = None
    provider_merged_at: str | None = None
    merge_commit_sha: str | None = None
    pr_status: Literal["action_needed", "waiting", "mergeable"] | None = None
    checks: PublicChecksSummary | None = None
    checks_status: Literal["none", "pending", "passing", "failing"] | None = None
    review_decision: (
        Literal["none", "review_required", "changes_requested", "approved"] | None
    ) = None
    merge_status: Literal["unknown", "mergeable", "blocked", "conflicting"] | None = (
        None
    )


class PublicCheckStatus(ProtocolModel):
    """One CI check entry (Phase 4, mirrors CheckStatusSchema)."""

    name: str
    status: Literal["pass", "fail", "pending"]
    url: str = ""


class PublicChecksSummary(ProtocolModel):
    """CI checks summary (Phase 4, the FR3.1 detail).

    ``null`` means "no CI data"; ``state: "none"`` means the provider
    answered and there are no checks.
    """

    state: Literal["pass", "fail", "pending", "none"]
    checked_at: str = ""
    checks: list[PublicCheckStatus] = Field(default_factory=list)


class PublicGitHubPullRequestDetails(PublicPullRequestSharedEnrichment):
    kind: Literal["pull_request"] = "pull_request"
    schema_version: int = 1
    repo_host: str
    repo_owner: str
    repo_name: str
    pull_request_number: int | None = None


class PublicGitLabMergeRequestDetails(PublicPullRequestSharedEnrichment):
    kind: Literal["pull_request"] = "pull_request"
    schema_version: int = 1
    repo_host: str
    # Optional here (documented deviation from the Code Web contract, which
    # requires repoId): when glab cannot resolve the numeric id, the artifact
    # is still recorded with a project_path-based external key.
    repo_id: int | None = None
    project_path: str
    merge_request_iid: int


type PublicPullRequestArtifactDetails = (
    PublicGitHubPullRequestDetails | PublicGitLabMergeRequestDetails
)


class PublicSessionArtifact(ProtocolModel):
    """Wire format of a session artifact — identical to the Code Web contract."""

    id: str
    session_id: str
    kind: Literal["pull_request"] = "pull_request"
    provider: Literal["github", "gitlab"]
    # Stable dedupe key: "github.com/org/repo/pull/123" or
    # "gitlab.com/42/merge_requests/7".
    external_key: str
    external_url: str
    display_title: str | None = None
    discovered_at: str
    viewed_at: str | None = None
    details: PublicPullRequestArtifactDetails | None = None


class ArtifactsListParams(ProtocolModel):
    session_id: str


class ArtifactsListResponse(ProtocolModel):
    artifacts: list[PublicSessionArtifact]


class ArtifactsMarkViewedParams(ProtocolModel):
    session_id: str
    artifact_ids: list[str]


class ArtifactsSyncParams(ProtocolModel):
    """Client-driven provider sync (Phase 4): the Desktop fires it for a
    project's local sessions when the user switches project in the sidebar.
    """

    session_id: str


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
    session_id: str | None = None


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
    bypass_tool_permissions: bool = False


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


class RuntimeMutationStatus(StrEnum):
    APPLIED = auto()
    PENDING = auto()


class RuntimeMutationResponse(ProtocolModel):
    """What the mutation produced, and whether the session is running it yet.

    ``runtime`` is always the configuration the mutation produced, so a client
    can render what the user asked for. ``PENDING`` says the session is still
    running the previous one until the turn it is in ends: the Core reads its
    settings when a turn starts, so what it holds cannot be replaced under it.
    """

    runtime: RuntimeSnapshot
    status: RuntimeMutationStatus = RuntimeMutationStatus.APPLIED

    @property
    def applied(self) -> bool:
        return self.status is RuntimeMutationStatus.APPLIED


class RuntimeUpdatedParams(ProtocolModel):
    session_id: str
    runtime: RuntimeSnapshot


class TurnRetryingParams(ProtocolModel):
    session_id: str
    category: PublicRetryCategory
    detail: str


# The ``warning`` code of a connector or MCP server that failed to load in the
# background. Its message names what failed; clients show it once.
INTEGRATIONS_UNAVAILABLE_WARNING: Final = "integrations_unavailable"


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
    value_labels: dict[str, str] = Field(default_factory=dict)
    layer_values: list[ConfigLayerValueWire] = Field(default_factory=list)

    @property
    def origin(self) -> str:
        return self.layer_values[0].layer if self.layer_values else "default"


class ConfigEffectiveReadParams(ProtocolModel):
    session_id: str


class ConfigEffectiveReadResponse(ProtocolModel):
    """The whole config the session runs with, credentials redacted.

    Resolved after the agent profile and the session's own options, with
    approval bypass as the session applies it; for records such as a headless
    run's export, not for editing.
    """

    config: dict[str, JsonValue]


class ConfigFieldsReadParams(ProtocolModel):
    session_id: str


class ConfigFieldsReadResponse(ProtocolModel):
    fields: list[ConfigFieldWire]
    targets: list[str]


class ConfigWriteOpWire(ProtocolModel):
    """One config mutation in a ``config/write`` batch.

    Version skew (ADR 0014): this model is strict (``extra="forbid"``), so an
    older server answers a newer client's unknown field with ``invalidParams``
    and writes nothing.
    """

    op: Literal["set", "remove"]
    path: str
    value: JsonValue = None
    target_layer: str | None = None


class ModelConfigWriteParams(ProtocolModel):
    """A model pick: which model answers, and how hard it thinks."""

    session_id: str
    model_alias: str | None = None
    reasoning_effort: str | None = None
    # Otherwise the pick also becomes the user's default, as a picker's does.
    session_only: bool = False


class ConfigWriteParams(ProtocolModel):
    session_id: str
    ops: list[ConfigWriteOpWire]
    reason: str = "config write"
    reload_runtime: bool = False


class ConfigWriteResponse(ConfigMutationResponse):
    rejected: bool = False
    # Why a rejected write was refused, in words the user can act on.
    reason: str | None = None
    failures: list[str] = Field(default_factory=list)

    @property
    def applied(self) -> bool:
        return super().applied and not self.rejected and not self.failures


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
    """Without a session, lists what a session opened in ``cwd`` would offer."""

    session_id: str | None = None
    cwd: str | None = None
    trust_workspace: bool = False


class SkillsListResponse(ProtocolModel):
    skills: list[SkillSummary]


SkillScopeArg = str


class SkillsInstalledParams(ProtocolModel):
    session_id: str


class SkillsInstalledResponse(ProtocolModel):
    skills: list[SkillSummary]


class SkillsCatalogParams(ProtocolModel):
    session_id: str


class SkillsCatalogResponse(ProtocolModel):
    skills: list[SkillCatalogEntry]
    updates: dict[str, int] = Field(default_factory=dict)
    loaded: bool = False
    project_available: bool = False
    authenticated: bool = True


class SkillsVersionsParams(ProtocolModel):
    session_id: str
    skill_id: str


class SkillsVersionsResponse(ProtocolModel):
    versions: list[SkillVersionView]


class SkillsUpdatesParams(ProtocolModel):
    session_id: str


class SkillsUpdatesResponse(ProtocolModel):
    updates: list[SkillUpdateView]


class SkillsDetailParams(ProtocolModel):
    session_id: str
    skill_id: str
    version: int | None = None


class SkillsDetailResponse(ProtocolModel):
    detail: SkillDetailView | None = None
    body: str | None = None


class SkillsImportParams(ProtocolModel):
    session_id: str
    skill_id: str
    version: int | None = None
    alias: str | None = None
    scope: SkillScopeArg = "global"


class SkillsSetVersionParams(ProtocolModel):
    session_id: str
    name: str
    version: int
    scope: SkillScopeArg = "global"


class SkillsSetLatestParams(ProtocolModel):
    session_id: str
    name: str
    scope: SkillScopeArg = "global"


class SkillsSetAliasParams(ProtocolModel):
    session_id: str
    name: str
    alias: str
    scope: SkillScopeArg = "global"


class SkillsRemoveParams(ProtocolModel):
    session_id: str
    name: str
    scope: SkillScopeArg = "global"


class SkillsSetEnabledParams(ProtocolModel):
    session_id: str
    name: str
    enabled: bool


class SkillsConvertLocalParams(ProtocolModel):
    session_id: str
    name: str
    scope: SkillScopeArg = "global"


class SkillsConvertResponse(RuntimeMutationResponse):
    converted: bool = False


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
    readiness: Literal[
        "ready", "needs_auth", "needs_setup", "needs_connect", "unavailable"
    ]
    auth_action: Literal[
        "none", "oauth", "credentials_setup", "no_auth_connect", "unknown"
    ]
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
    status: Literal[
        "disabled",
        "connected",
        "needs_auth",
        "needs_setup",
        "needs_connect",
        "unavailable",
    ]
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
    manage_url: str | None = None
    private_connectors_hidden: bool = False


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


class ConnectorCatalogConnectParams(ProtocolModel):
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
    url: str | None = None
    name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    transport: MCPAddTransport | Literal["stdio"] = "streamable-http"
    allow_insecure_http: bool = False
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    api_key_env: str | None = None
    api_key_header: str | None = None
    api_key_format: str | None = None
    startup_timeout_sec: float | None = Field(default=None, allow_inf_nan=False)
    tool_timeout_sec: float | None = Field(default=None, allow_inf_nan=False)


class MCPAddResponse(ProtocolModel):
    name: str
    url: str | None = None
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
    # The same repository-relative position without requiring it to exist in
    # the main checkout. Used to associate retained sessions with projects;
    # unlike repository_cwd, it is not necessarily a valid move destination.
    repository_mapped_cwd: str | None = None
    # The repository the listing was taken from. Pair with
    # repository_mapped_cwd so a nested repository is not treated as part of
    # a parent project just because its path sits underneath it.
    repository_root: str | None = None


class WorkspaceWorktreePruneParams(ProtocolModel):
    pass


class WorkspaceWorktreePruneResponse(ProtocolModel):
    removed: int


class WorkspaceWorktreeLimitUpdateParams(ProtocolModel):
    limit: int = Field(ge=0, le=100)


class WorkspaceWorktreeLimitUpdateResponse(ProtocolModel):
    limit: int
    failures: list[str] = Field(default_factory=list)


# No session_id on the wire: the caller is deleting a session that has already
# closed and dropped its holder, and accepting one would let a client name an
# arbitrary holder file to unlink.
class WorkspaceWorktreeRemoveParams(ProtocolModel):
    cwd: str = Field(min_length=1)


# The remove-only extension: `force`/`inspect` are confirm-flow params that
# exist so a client can ask before it discards (ADR 0014).
class WorkspaceWorktreeRemoveConfirmParams(WorkspaceWorktreeRemoveParams):
    # Explicit confirmation to discard uncommitted changes, untracked files,
    # and new commits without saving a snapshot. Absent (the default) keeps the
    # snapshot path, so old clients are unaffected.
    force: bool = False
    # None keeps the server default: delete the branch when Vibe created it.
    delete_branch: bool | None = None
    # Report the outcome a removal would have, without removing anything or
    # releasing holders: the exit flow's "Removing worktree" line asks before
    # it commits, like the CLI's own inspect before its remove. `outcome` is
    # then the would-be outcome ("removed" means a removal now would succeed).
    # Absent (the default) removes, so old clients are unaffected.
    inspect: bool = False


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
    # Whether Vibe created the branch, so a caller can prompt before deleting
    # one that existed before the session. Absent when no record was read.
    branch_created: bool | None = None
    # How many other sessions hold the worktree (the asking session's own
    # holder discounted), so a caller can say "in use by N other session(s)".
    # None when there was no claim to count.
    holders: int | None = None


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


class SetupProviderView(ProtocolModel):
    """The onboarding surface of a resolved provider.

    The six fields are everything the wizard reads and mutates. The server
    merges them back onto the full resolved provider on
    ``setup/submit-choices``, so the rest of the provider's settings survive
    the client round trip.
    """

    name: str
    api_base: str
    api_key_env_var: str = ""
    browser_auth_base_url: str | None = None
    browser_auth_api_base_url: str | None = None
    browser_auth_allow_origin_rewrite: bool = False


class SetupStatusParams(ProtocolModel):
    # The provider a failed session's handshake named, so the wizard seeds
    # for the provider that actually lacks a key; absent means the server's
    # own active provider.
    provider: str | None = None


class SetupStatusResponse(ProtocolModel):
    """The wizard seed: the server's fully-resolved view (ADR 0009 setup)."""

    provider: SetupProviderView
    console_base_url: str
    vibe_base_url: str
    active_model: str
    theme: str
    supports_browser_sign_in: bool
    # The server's view of the key: process env (including the loaded dotenv)
    # first, then the keyring — the ``resolve_api_key`` order.
    has_api_key: bool
    # The server-resolved TLS trust flag (ADR 0015): the fully-resolved
    # effective config, not just the env var and user config.toml.
    enable_system_trust_store: bool = False
    # The administrator-pinned console URL (`VIBE_CUSTOM_CONSOLE_URL`), when
    # set: the wizard locks its sign-in domain to it.
    pinned_console_url: str | None = None


class SetupStoreCredentialParams(ProtocolModel):
    # The provider name; the server resolves it against its own config so the
    # client never carries credential-adjacent state it does not own.
    provider: str
    api_key: str
    custom_domain: bool = False


class SetupStoreCredentialResponse(ProtocolModel):
    """The Python key-persist outcome contract — never silent.

    ``completed`` — the key is stored. ``env_var_error`` — the derived env
    var name is empty or invalid; nothing was saved. ``save_error`` — neither the
    keyring nor the ``.env`` fallback could store it, so the key lives only in
    the server process env for this run. ``detail`` carries the env var name
    or the write error.
    """

    outcome: Literal["completed", "env_var_error", "save_error"]
    detail: str | None = None


class SetupSubmitChoicesParams(ProtocolModel):
    """Fields the wizard may have drifted; absent means "keep the config"."""

    provider: SetupProviderView | None = None
    console_base_url: str | None = None
    vibe_base_url: str | None = None
    # Present only when a theme was selected; presence is the write signal
    # (Python persists /theme on every completed onboarding that selected one).
    theme: str | None = None


class SetupSubmitChoicesResponse(ProtocolModel):
    outcome: Literal["completed", "provider_config_error"]
    failures: list[str] = Field(default_factory=list)


class ProjectLinksListParams(ProtocolModel):
    pass


class ProjectLinksLocalLink(ProtocolModel):
    directory_path: str
    has_commits: bool


class ProjectLinksLinkedProject(ProtocolModel):
    project_id: str
    local_links: list[ProjectLinksLocalLink]


class ProjectLinksListResponse(ProtocolModel):
    projects: list[ProjectLinksLinkedProject]


type ProjectLinksResolveRootRejectReason = Literal[
    "not_git", "unsupported_remote", "nested_unresolvable", "no_commits"
]


class ProjectLinksDirectoryGit(ProtocolModel):
    current_branch: str | None
    default_branch: str | None
    github_repo_url: str | None
    has_commits: bool


class ProjectLinksInspectedDirectory(ProtocolModel):
    directory_path: str
    directory_name: str
    git: ProjectLinksDirectoryGit | None


class ProjectLinksResolveRootParams(ProtocolModel):
    root_path: str = Field(min_length=1)


class ProjectLinksResolveRootResponse(ProtocolModel):
    eligible: bool
    reject_reason: ProjectLinksResolveRootRejectReason | None = None
    root: ProjectLinksInspectedDirectory | None = None


class ProjectLinksInspectRootParams(ProtocolModel):
    root_path: str = Field(min_length=1)


class ProjectLinksSavedLink(ProtocolModel):
    project_id: str
    project_name: str


class ProjectLinksInspectRootResponse(ProtocolModel):
    eligible: bool
    reject_reason: ProjectLinksResolveRootRejectReason | None = None
    root: ProjectLinksInspectedDirectory | None = None
    saved_link: ProjectLinksSavedLink | None = None
    stale_link_cleared: bool
    stale_link_clear_failed: bool = False


class ProjectLinksPickerCandidate(ProtocolModel):
    project_id: str
    name: str
    recommended: bool


class ProjectLinksPickerCandidates(ProtocolModel):
    items: list[ProjectLinksPickerCandidate]
    next_cursor: str | None


class ProjectLinksPickerLoadParams(ProtocolModel):
    root_path: str = Field(min_length=1)


class ProjectLinksPickerLoadResponse(ProtocolModel):
    root: ProjectLinksInspectedDirectory
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
    expected_github_repo_url: str | None


class ProjectLink(ProtocolModel):
    project_id: str
    project_name: str
    directory_path: str


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
    snooze_duration_seconds: int | None = None


class FeedbackRecordParams(ProtocolModel):
    session_id: str
    action: Literal["asked", "given", "snoozed"]


class _TurnQueueInputParams(ProtocolModel):
    idempotency_key: str | None = None
    session_id: str
    entries: list[TurnInputEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        validate_turn_input_entries(self.entries)
        return self

    @property
    def input(self) -> list[SessionContentBlock]:
        return [block for entry in self.entries for block in entry.content]

    @property
    def user_entry(self) -> TurnUserInputEntry | None:
        return next(
            (entry for entry in reversed(self.entries) if entry.role == "user"), None
        )

    @property
    def message_entry_id(self) -> str | None:
        user_entry = self.user_entry
        return user_entry.entry_id if user_entry is not None else None


class TurnEnqueueParams(_TurnQueueInputParams):
    pass


class TurnEnqueueResponse(ProtocolModel):
    queue_item_id: str


class TurnQueueReplaceParams(_TurnQueueInputParams):
    queue_item_id: str


class TurnQueueReplaceResponse(ProtocolModel):
    queue_item_id: str


class _TurnQueueEditParams(ProtocolModel):
    session_id: str
    queue_item_id: str
    edit_id: UUID


class TurnQueueEditStartParams(_TurnQueueEditParams):
    pass


class TurnQueueEditStartResponse(ProtocolModel):
    queue_item_id: str


class TurnQueueEditSaveParams(_TurnQueueEditParams):
    idempotency_key: str
    entries: list[TurnInputEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        validate_turn_input_entries(self.entries)
        return self


class TurnQueueEditSaveResponse(ProtocolModel):
    queue_item_id: str


class TurnQueueEditCancelParams(_TurnQueueEditParams):
    pass


class TurnQueueEditCancelResponse(ProtocolModel):
    pass


class TurnQueueEditAbandonParams(_TurnQueueEditParams):
    pass


class TurnQueueEditAbandonResponse(ProtocolModel):
    pass


class TurnQueueSteerParams(ProtocolModel):
    session_id: str
    queue_item_id: str
    expected_turn_id: str


class TurnQueueSteerResponse(EventWatermarkResponse):
    queue_item_id: str
    turn_id: str


class TurnQueueReadParams(ProtocolModel):
    session_id: str


class TurnQueueReadResponse(ProtocolModel):
    queue: PublicTurnQueue


class TurnQueueRemoveParams(ProtocolModel):
    session_id: str
    queue_item_id: str


class TurnQueueRemoveResponse(ProtocolModel):
    pass


class TurnQueueResumeParams(ProtocolModel):
    session_id: str


class TurnQueueResumeResponse(ProtocolModel):
    pass


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
    user_display_content: UserDisplayContent | None = None
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


SESSION_SPAWN_METHOD = "session/spawn"
"""The server request asking a client that advertised ``sessionSpawn`` to start
a new session of its own and run ``prompt`` in it."""


class SessionSpawnNewWorktree(ProtocolModel):
    kind: Literal["new_worktree"] = "new_worktree"


class SessionSpawnManagedWorktree(ProtocolModel):
    kind: Literal["managed_worktree"] = "managed_worktree"
    path: str = Field(min_length=1)


class SessionSpawnDirectory(ProtocolModel):
    """The parent's directory, which is not a worktree Vibe manages."""

    kind: Literal["directory"] = "directory"
    path: str = Field(min_length=1)


type SessionSpawnCheckout = Annotated[
    SessionSpawnNewWorktree | SessionSpawnManagedWorktree | SessionSpawnDirectory,
    Field(discriminator="kind"),
]


class SessionSpawnParams(ProtocolModel):
    parent_session_id: str
    prompt: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    checkout: SessionSpawnCheckout
    # Stable across replays of one tool call.
    idempotency_key: str = Field(min_length=1)
    # The parent's unless the user asked for others; no level means the
    # model's default.
    model: str
    reasoning_effort: str | None = None
    approval_policy: str


class SessionSpawnResponse(ProtocolModel):
    session_id: str
    title: str | None = None
    # The client's own id and address for the session, opaque to the server.
    client_session_id: str | None = None
    url: str | None = None


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
    pass


class SessionUpdatedParams(EventNotificationParams):
    patch: list[JsonPatchOperation]


class TurnQueueUpdatedParams(EventNotificationParams):
    queue: PublicTurnQueue


class TurnStartedParams(EventNotificationParams):
    turn: PublicTurn


class TurnCompletedParams(EventNotificationParams):
    turn: PublicTurn


class StatsUpdatedParams(EventNotificationParams):
    stats: AgentStatsSnapshot
    context_window: int


class ChildSessionUpdatedParams(EventNotificationParams):
    child_session: PublicChildSession


class ArtifactCreatedParams(EventNotificationParams):
    artifact: PublicSessionArtifact


class ArtifactUpdatedParams(EventNotificationParams):
    artifact: PublicSessionArtifact


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


def format_invalid_params_issues(data: JsonValue) -> str | None:
    """Render the field-level detail carried by an INVALID_PARAMS error.

    The useful part of a validation rejection — which field failed and why —
    travels in ``ProtocolError.data`` as an :class:`InvalidParamsData` payload.
    Returns a single-line summary like ``field.path: reason; ...`` or ``None``
    when the data does not carry any issues.
    """
    if not isinstance(data, dict):
        return None
    issues = data.get("issues")
    if not isinstance(issues, list) or not issues:
        return None
    parts: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        path = issue.get("path")
        location = (
            ".".join(str(segment) for segment in path)
            if isinstance(path, list) and path
            else "<root>"
        )
        message = issue.get("message")
        parts.append(f"{location}: {message}" if message else location)
    if not parts:
        return None
    return "; ".join(parts)


def _render_protocol_error(error: ProtocolError) -> str:
    detail = format_invalid_params_issues(error.data)
    if detail is None:
        return error.message
    return f"{error.message} ({detail})"


class AppServerResponseError(RuntimeError):
    def __init__(self, error: ProtocolError) -> None:
        self.error = error
        super().__init__(_render_protocol_error(error))


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
