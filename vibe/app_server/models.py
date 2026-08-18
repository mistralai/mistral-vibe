from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto
from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter

from vibe.agents import AgentSafety, AgentType
from vibe.app_server._effect_models import (
    EffectDetail as EffectDetail,
    FileEditEffectDetail as FileEditEffectDetail,
    FileEditEffectInput as FileEditEffectInput,
    FileEditEffectOccurrence as FileEditEffectOccurrence,
    FileEditEffectOutput as FileEditEffectOutput,
    FileReadEffectDetail as FileReadEffectDetail,
    FileReadEffectInput as FileReadEffectInput,
    FileReadEffectOutput as FileReadEffectOutput,
    FileSearchEffectDetail as FileSearchEffectDetail,
    FileSearchEffectInput as FileSearchEffectInput,
    FileSearchEffectMatch as FileSearchEffectMatch,
    FileSearchEffectOutput as FileSearchEffectOutput,
    FileWriteEffectDetail as FileWriteEffectDetail,
    FileWriteEffectInput as FileWriteEffectInput,
    FileWriteEffectOutput as FileWriteEffectOutput,
    GenericEffectDetail as GenericEffectDetail,
    ShellEffectDetail as ShellEffectDetail,
    ShellEffectInput as ShellEffectInput,
    ShellEffectOutput as ShellEffectOutput,
    SkillEffectDetail as SkillEffectDetail,
    SkillEffectInput as SkillEffectInput,
    SkillEffectOutput as SkillEffectOutput,
    SubagentEffectDetail as SubagentEffectDetail,
    SubagentEffectInput as SubagentEffectInput,
    SubagentEffectOutput as SubagentEffectOutput,
    TodoEffectDetail as TodoEffectDetail,
    TodoEffectInput as TodoEffectInput,
    TodoEffectItem as TodoEffectItem,
    TodoEffectOutput as TodoEffectOutput,
    TodoEffectPriority as TodoEffectPriority,
    TodoEffectStatus as TodoEffectStatus,
    UserQuestionEffectDetail as UserQuestionEffectDetail,
    WebFetchEffectDetail as WebFetchEffectDetail,
    WebFetchEffectInput as WebFetchEffectInput,
    WebFetchEffectOutput as WebFetchEffectOutput,
    WebSearchEffectDetail as WebSearchEffectDetail,
    WebSearchEffectInput as WebSearchEffectInput,
    WebSearchEffectOutput as WebSearchEffectOutput,
    WebSearchEffectSource as WebSearchEffectSource,
    WorktreeEffectDetail as WorktreeEffectDetail,
    WorktreeEffectInput as WorktreeEffectInput,
    effect_input_json as effect_input_json,
)
from vibe.app_server._model import ProtocolModel
from vibe.permissions import RequiredPermission
from vibe.questions import (
    QuestionChoice as QuestionChoice,
    UserAnswer as UserAnswer,
    UserQuestion as UserQuestion,
    UserQuestionRequest as UserQuestionRequest,
    UserQuestionResult as UserQuestionResult,
)
from vibe.user_content import UserDisplayContent as UserDisplayContent, UserResource
from vibe.utils.pricing import session_token_cost
from vibe.utils.tool_presentation import (
    EffectCallDisplay as EffectCallDisplay,
    EffectResultDisplay as EffectResultDisplay,
)


class AccountStatus(StrEnum):
    READY = auto()
    MISSING_KEY = auto()
    UNAUTHORIZED = auto()
    UNAVAILABLE = auto()


class AccountPlanKind(StrEnum):
    API = auto()
    CHAT = auto()
    MISTRAL_CODE = auto()


class AccountActionKind(StrEnum):
    SWITCH_API_KEY = auto()
    UPGRADE_TO_PRO = auto()


class AccountPlanView(ProtocolModel):
    kind: AccountPlanKind
    name: str
    title: str | None = None


class AccountAction(ProtocolModel):
    kind: AccountActionKind
    url: str


class AccountView(ProtocolModel):
    status: AccountStatus
    plan: AccountPlanView | None = None
    plan_offer: AccountAction | None = None
    rate_limit_action: AccountAction | None = None
    teleport_eligible: bool = False
    teleport_action: AccountAction | None = None


class IdentityEntityView(ProtocolModel):
    id: str
    name: str


class IdentityView(ProtocolModel):
    id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    workspace: IdentityEntityView | None = None
    organization: IdentityEntityView | None = None

    @property
    def name(self) -> str | None:
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        if self.first_name:
            return self.first_name
        return self.email


class FileImageSource(ProtocolModel):
    kind: Literal["file"] = "file"
    path: str


class InlineImageSource(ProtocolModel):
    kind: Literal["inline"] = "inline"
    data: str


ImageSource = Annotated[
    FileImageSource | InlineImageSource, Field(discriminator="kind")
]


class ImageAttachment(ProtocolModel):
    source: ImageSource
    alias: str
    mime_type: str


class MentionStats(ProtocolModel):
    count: int = 0
    context_types: dict[str, int] = Field(default_factory=dict)
    file_extensions: dict[str, int] = Field(default_factory=dict)


class PreparedPrompt(ProtocolModel):
    display_text: str
    prompt_text: str
    images: list[ImageAttachment] = Field(default_factory=list)
    auto_title: str | None = None
    mentions: MentionStats = Field(default_factory=MentionStats)


type WorkspaceTrustDecision = Literal["trust_repo", "trust_cwd", "decline"]
type WorkspaceTrustStatus = Literal["trusted", "session", "untrusted"]


class WorkspaceTrustDetails(ProtocolModel):
    cwd: str
    repo_root: str | None = None
    detected_files: list[str] = Field(default_factory=list)
    repo_detected_files: list[str] = Field(default_factory=list)
    repo_explicitly_untrusted: bool = False
    settings_path: str
    available_decisions: list[WorkspaceTrustDecision] = Field(default_factory=list)


class TextContentBlock(ProtocolModel):
    type: Literal["text"] = "text"
    text: str


class ImageContentBlock(ProtocolModel):
    type: Literal["image"] = "image"
    attachment: ImageAttachment


class ResourceContentBlock(ProtocolModel):
    type: Literal["resource"] = "resource"
    resource: UserResource


ContentBlock = Annotated[
    TextContentBlock | ImageContentBlock | ResourceContentBlock,
    Field(discriminator="type"),
]


class ApprovalDecisionType(StrEnum):
    APPROVE = auto()
    APPROVE_FOR_SESSION = auto()
    APPROVE_PERMANENTLY = auto()
    DENY = auto()
    CANCEL_TURN = auto()


class ApprovalDecision(ProtocolModel):
    type: ApprovalDecisionType


class ApprovalCallbackDetail(ProtocolModel):
    kind: Literal["approval"] = "approval"
    effect: EffectDetail
    required_permissions: list[RequiredPermission] = Field(default_factory=list)
    choices: list[ApprovalDecisionType] = Field(
        default_factory=lambda: list(ApprovalDecisionType)
    )
    related_entry_id: str | None = None


class UserInputCallbackDetail(ProtocolModel):
    kind: Literal["user_input"] = "user_input"
    request: UserQuestionRequest
    related_entry_id: str | None = None


CallbackDetail = Annotated[
    ApprovalCallbackDetail | UserInputCallbackDetail, Field(discriminator="kind")
]


class ApprovalCallbackOutput(ProtocolModel):
    type: Literal["approval"] = "approval"
    decision: ApprovalDecision
    feedback: str | None = None


class UserInputCallbackOutput(ProtocolModel):
    type: Literal["user_input"] = "user_input"
    result: UserQuestionResult


CallbackOutput = Annotated[
    ApprovalCallbackOutput | UserInputCallbackOutput, Field(discriminator="type")
]


class PublicEntryGenerationStatus(StrEnum):
    IN_PROGRESS = auto()
    COMPLETED = auto()


class PublicTurnStatus(StrEnum):
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()
    INTERRUPTED = auto()


class PublicTurnStopReason(StrEnum):
    LIMIT = auto()


class PublicRetryCategory(StrEnum):
    RATE_LIMITED = auto()
    SERVER_ERROR = auto()
    TIMED_OUT = auto()
    CONNECTION = auto()
    UNKNOWN = auto()


class TurnErrorCode(StrEnum):
    RATE_LIMIT = auto()
    CONTEXT_TOO_LONG = auto()
    RESPONSE_TOO_LONG = auto()
    REFUSAL = auto()
    INVALID_IMAGE_ATTACHMENT = auto()
    IMAGES_NOT_SUPPORTED = auto()
    COMPACTION_FAILED = auto()
    INCOMPLETE_STREAM = auto()
    BACKEND_ERROR = auto()
    INVALID_MODEL = auto()
    INTERNAL_ERROR = auto()


class PublicError(ProtocolModel):
    message: str
    code: str | None = None
    details: JsonValue = None


class TokenUsage(ProtocolModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class AgentStatsSnapshot(ProtocolModel):
    steps: int = 0
    session_prompt_tokens: int = 0
    session_completion_tokens: int = 0
    session_cached_tokens: int = 0
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    cached_input_price_per_million: float | None = None
    tool_calls_agreed: int = 0
    tool_calls_rejected: int = 0
    tool_calls_failed: int = 0
    tool_calls_succeeded: int = 0
    context_tokens: int = 0
    last_turn_prompt_tokens: int = 0
    last_turn_completion_tokens: int = 0
    last_turn_cached_tokens: int = 0
    last_turn_duration: float = 0.0
    tokens_per_second: float = 0.0

    @property
    def session_total_llm_tokens(self) -> int:
        return self.session_prompt_tokens + self.session_completion_tokens

    @property
    def last_turn_total_tokens(self) -> int:
        return self.last_turn_prompt_tokens + self.last_turn_completion_tokens

    @property
    def session_cost(self) -> float:
        return session_token_cost(
            prompt_tokens=self.session_prompt_tokens,
            completion_tokens=self.session_completion_tokens,
            cached_tokens=self.session_cached_tokens,
            input_price_per_million=self.input_price_per_million,
            output_price_per_million=self.output_price_per_million,
            cached_input_price_per_million=self.cached_input_price_per_million,
        )

    @property
    def token_usage(self) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.session_prompt_tokens,
            output_tokens=self.session_completion_tokens,
            total_tokens=self.session_total_llm_tokens,
        )


class ConfigIssue(ProtocolModel):
    file: str
    message: str


class DebugLogEntry(ProtocolModel):
    id: str
    timestamp: datetime
    ppid: int
    pid: int
    level: str
    message: str
    raw_line: str


class DebugLogPage(ProtocolModel):
    entries: list[DebugLogEntry]
    has_more: bool
    cursor: int | None = None


class VibeCodeRepository(ProtocolModel):
    repo_url: str
    default_branch: str | None = None


class VibeCodeProject(ProtocolModel):
    project_id: str
    name: str
    repositories: list[VibeCodeRepository] = Field(default_factory=list)
    is_read_only: bool = False


class VibeCodeProjectLink(ProtocolModel):
    repo_root: str
    repo_url: str
    project_id: str
    project_name: str


class VibeCodePickerContext(ProtocolModel):
    repo_root: str
    repo_url: str
    repo_name: str
    saved_link: VibeCodeProjectLink | None = None


class VibeCodeGitInfo(ProtocolModel):
    remote_name: str
    remote_url: str
    repo: str
    branch: str | None = None
    default_branch: str | None = None


class VibeCodePickerState(ProtocolModel):
    projects: list[VibeCodeProject]
    next_cursor: str | None = None
    repo_url: str = ""

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


class VibeCodePickerView(ProtocolModel):
    context: VibeCodePickerContext
    state: VibeCodePickerState
    git: VibeCodeGitInfo
    saved_project_link_cleared: bool = False
    project_repo_remote_changed: bool = False


type VibeCodePickerPurpose = Literal["configure", "teleport"]


class TeleportSummarizingContext(ProtocolModel):
    kind: Literal["summarizing_context"] = "summarizing_context"
    operation_id: str


class TeleportCheckingGit(ProtocolModel):
    kind: Literal["checking_git"] = "checking_git"
    operation_id: str


class TeleportPushRequired(ProtocolModel):
    kind: Literal["push_required"] = "push_required"
    operation_id: str
    unpushed_count: int
    branch_not_pushed: bool = False


class TeleportPushing(ProtocolModel):
    kind: Literal["pushing"] = "pushing"
    operation_id: str


class TeleportStartingWorkflow(ProtocolModel):
    kind: Literal["starting_workflow"] = "starting_workflow"
    operation_id: str


class TeleportComplete(ProtocolModel):
    kind: Literal["complete"] = "complete"
    operation_id: str
    url: str


class TeleportFailed(ProtocolModel):
    kind: Literal["failed"] = "failed"
    operation_id: str
    error: PublicError


type TeleportEvent = Annotated[
    TeleportSummarizingContext
    | TeleportCheckingGit
    | TeleportPushRequired
    | TeleportPushing
    | TeleportStartingWorkflow
    | TeleportComplete
    | TeleportFailed,
    Field(discriminator="kind"),
]


class AgentSummary(ProtocolModel):
    name: str
    display_name: str
    description: str
    safety: AgentSafety
    agent_type: AgentType


class SkillSummary(ProtocolModel):
    name: str
    description: str
    prompt: str
    user_invocable: bool = True
    source: Literal["builtin", "local", "registry"] = "local"


class ToolSummary(ProtocolModel):
    name: str
    is_custom: bool = False


class ConnectorCounts(ProtocolModel):
    connected: int = 0
    total: int | None = None


class MCPSourceKind(StrEnum):
    SERVER = auto()
    CONNECTOR = auto()


class MCPSourceStatus(StrEnum):
    DISABLED = auto()
    CONNECTED = auto()
    ENABLED = auto()
    NEEDS_AUTH = auto()
    NEEDS_SETUP = auto()
    UNAVAILABLE = auto()


class MCPToolSummary(ProtocolModel):
    name: str
    description: str = ""
    enabled: bool = True


class MCPSourceSummary(ProtocolModel):
    name: str
    kind: MCPSourceKind
    transport: str
    status: MCPSourceStatus
    tools: list[MCPToolSummary] = Field(default_factory=list)


class MCPState(ProtocolModel):
    sources: list[MCPSourceSummary] = Field(default_factory=list)
    discovery_errors: dict[str, str] = Field(default_factory=dict)

    @property
    def needs_auth(self) -> list[str]:
        return sorted(
            source.name
            for source in self.sources
            if source.kind is MCPSourceKind.SERVER
            and source.status is MCPSourceStatus.NEEDS_AUTH
        )

    @property
    def statuses(self) -> dict[str, str]:
        return {
            source.name: source.status.value
            for source in self.sources
            if source.kind is MCPSourceKind.SERVER
        }


class SessionLogSummary(ProtocolModel):
    enabled: bool
    session_id: str | None = None
    persisted: bool = False
    path: str | None = None
    title: str | None = None
    needs_initial_auto_title: bool = False


class SavedSessionSummary(ProtocolModel):
    session_id: str
    cwd: str
    parent_session_id: str | None = None
    title: str | None = None
    end_time: str | None = None
    preview: str = ""

    @property
    def option_id(self) -> str:
        return self.session_id

    @property
    def short_id(self) -> str:
        return self.session_id[:8]


class PendingEffectState(ProtocolModel):
    status: Literal["pending"] = "pending"


class RunningEffectState(ProtocolModel):
    status: Literal["running"] = "running"
    output_text: str = ""


class BlockedEffectState(ProtocolModel):
    status: Literal["blocked"] = "blocked"
    callback_id: str
    output_text: str = ""


class CompletedEffectState(ProtocolModel):
    status: Literal["completed"] = "completed"
    output: JsonValue = None
    output_text: str = ""
    duration_ms: float = 0.0
    display: EffectResultDisplay


class FailedEffectState(ProtocolModel):
    status: Literal["failed"] = "failed"
    error: PublicError
    output_text: str = ""
    duration_ms: float = 0.0
    display: EffectResultDisplay


class CancelledEffectState(ProtocolModel):
    status: Literal["cancelled"] = "cancelled"
    reason: str
    output_text: str = ""
    duration_ms: float = 0.0
    display: EffectResultDisplay | None = None


class SkippedEffectState(ProtocolModel):
    status: Literal["skipped"] = "skipped"
    reason: str
    display: EffectResultDisplay


EffectState = Annotated[
    PendingEffectState
    | RunningEffectState
    | BlockedEffectState
    | CompletedEffectState
    | FailedEffectState
    | CancelledEffectState
    | SkippedEffectState,
    Field(discriminator="status"),
]


class OpenCallbackState(ProtocolModel):
    status: Literal["open"] = "open"


class AnsweredCallbackState(ProtocolModel):
    status: Literal["answered"] = "answered"
    output: CallbackOutput


class CancelledCallbackState(ProtocolModel):
    status: Literal["cancelled"] = "cancelled"
    reason: str


class ExpiredCallbackState(ProtocolModel):
    status: Literal["expired"] = "expired"
    reason: str


CallbackState = Annotated[
    OpenCallbackState
    | AnsweredCallbackState
    | CancelledCallbackState
    | ExpiredCallbackState,
    Field(discriminator="status"),
]


class _PublicHistoryEntryBase(ProtocolModel):
    id: str
    session_id: str
    turn_id: str | None = None
    created_at: int
    updated_at: int
    generation_status: PublicEntryGenerationStatus
    related_entry_id: str | None = None


type PublicMessageSource = Literal["turn_start", "turn_steer", "harness"]


class PublicMessageEntry(_PublicHistoryEntryBase):
    type: Literal["message"] = "message"
    role: Literal["system", "user", "assistant"]
    content: list[ContentBlock]
    source: PublicMessageSource | None = None
    user_display_content: UserDisplayContent | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(
            block.text for block in self.content if isinstance(block, TextContentBlock)
        )

    @property
    def images(self) -> list[ImageAttachment]:
        return [
            block.attachment
            for block in self.content
            if isinstance(block, ImageContentBlock)
        ]


class PublicReasoningEntry(_PublicHistoryEntryBase):
    type: Literal["reasoning"] = "reasoning"
    text: str
    summary: list[str] = Field(default_factory=list)


class PublicEffectEntry(_PublicHistoryEntryBase):
    type: Literal["effect"] = "effect"
    title: str
    detail: EffectDetail
    state: EffectState


class PublicCallbackEntry(_PublicHistoryEntryBase):
    type: Literal["callback"] = "callback"
    callback_id: str
    title: str
    detail: CallbackDetail
    state: CallbackState


class HookScope(StrEnum):
    POST_AGENT = auto()
    PRE_TOOL = auto()
    POST_TOOL = auto()


class HookSeverity(StrEnum):
    OK = auto()
    WARNING = auto()
    ERROR = auto()


class HookNoticeDetail(ProtocolModel):
    kind: Literal[
        "hook_run_started", "hook_run_completed", "hook_started", "hook_completed"
    ]
    scope: HookScope = HookScope.POST_AGENT
    tool_name: str | None = None
    tool_call_id: str | None = None
    hook_name: str | None = None
    status: HookSeverity | None = None
    content: str | None = None


class AgentChangedNoticeDetail(ProtocolModel):
    kind: Literal["agent_changed"] = "agent_changed"
    agent_name: str


class ContextClearedNoticeDetail(ProtocolModel):
    kind: Literal["context_cleared"] = "context_cleared"
    plan_file_path: str | None = None


class SessionTitleUpdatedNoticeDetail(ProtocolModel):
    kind: Literal["session_title_updated"] = "session_title_updated"
    title: str


class PlanReviewStartedNoticeDetail(ProtocolModel):
    kind: Literal["plan_review_started"] = "plan_review_started"
    file_path: str


class PlanReviewEndedNoticeDetail(ProtocolModel):
    kind: Literal["plan_review_ended"] = "plan_review_ended"


class WaitingForInputNoticeDetail(ProtocolModel):
    kind: Literal["waiting_for_input"] = "waiting_for_input"
    task_id: str
    label: str | None = None
    predefined_answers: list[str] | None = None


class ScheduledLoopFiredNoticeDetail(ProtocolModel):
    kind: Literal["scheduled_loop_fired"] = "scheduled_loop_fired"
    loop_id: str


NoticeDetail = Annotated[
    HookNoticeDetail
    | AgentChangedNoticeDetail
    | ContextClearedNoticeDetail
    | SessionTitleUpdatedNoticeDetail
    | PlanReviewStartedNoticeDetail
    | PlanReviewEndedNoticeDetail
    | WaitingForInputNoticeDetail
    | ScheduledLoopFiredNoticeDetail,
    Field(discriminator="kind"),
]


class PublicCheckpointEntry(_PublicHistoryEntryBase):
    type: Literal["checkpoint"] = "checkpoint"
    kind: str
    message: str | None = None
    details: JsonValue = None


class PublicNoticeEntry(_PublicHistoryEntryBase):
    type: Literal["notice"] = "notice"
    level: Literal["info", "warning", "error"]
    message: str
    detail: NoticeDetail


PublicHistoryEntry = Annotated[
    PublicMessageEntry
    | PublicReasoningEntry
    | PublicEffectEntry
    | PublicCallbackEntry
    | PublicCheckpointEntry
    | PublicNoticeEntry,
    Field(discriminator="type"),
]

_PUBLIC_HISTORY_ENTRY_ADAPTER = TypeAdapter(PublicHistoryEntry)


def validate_history_entry(value: object) -> PublicHistoryEntry:
    return _PUBLIC_HISTORY_ENTRY_ADAPTER.validate_python(
        value, by_alias=True, by_name=False
    )


class HistoryCursor(ProtocolModel):
    before: str | None = None
    after: str | None = None


class PublicHistoryPage(ProtocolModel):
    entries: list[PublicHistoryEntry] = Field(default_factory=list)
    cursor: HistoryCursor = Field(default_factory=HistoryCursor)
    range: Literal["latest", "page"] = "latest"


class IdleSessionStatus(ProtocolModel):
    type: Literal["idle"] = "idle"


class RunningSessionStatus(ProtocolModel):
    type: Literal["running"] = "running"
    active_turn_id: str


class BlockedSessionStatus(ProtocolModel):
    type: Literal["blocked"] = "blocked"
    active_turn_id: str
    callback_id: str
    reason: str


class FailedSessionStatus(ProtocolModel):
    type: Literal["failed"] = "failed"
    message: str


PublicSessionStatus = Annotated[
    IdleSessionStatus
    | RunningSessionStatus
    | BlockedSessionStatus
    | FailedSessionStatus,
    Field(discriminator="type"),
]


class PublicSession(ProtocolModel):
    id: str
    root_session_id: str | None = None
    parent_session_id: str | None = None
    title: str | None = None
    preview: str = ""
    status: PublicSessionStatus
    created_at: int
    updated_at: int
    cwd: str | None = None
    workspace_roots: list[str] = Field(default_factory=list)
    model: str | None = None
    agent: AgentSummary | None = None
    token_usage: TokenUsage | None = None


class PublicTurn(ProtocolModel):
    id: str
    session_id: str
    status: PublicTurnStatus
    started_at: int
    completed_at: int | None = None
    error: PublicError | None = None
    stop_reason: PublicTurnStopReason | None = None


class PublicSessionState(ProtocolModel):
    format: Literal["vibe.public-session-state/v1"] = "vibe.public-session-state/v1"
    event_id: int = Field(ge=0, strict=True)
    session: PublicSession
    history: list[PublicHistoryEntry] | None = None
    history_before_cursor: str | None = None
    turns: list[PublicTurn] | None = None
    active_callbacks: list[PublicCallbackEntry] = Field(default_factory=list)

    @property
    def latest_turn(self) -> PublicTurn | None:
        if not self.turns:
            return None
        return self.turns[-1]


class JsonPatchOperation(ProtocolModel):
    op: Literal["add", "append", "replace", "remove", "test"]
    path: str
    value: JsonValue = None


class ScheduledLoop(ProtocolModel):
    id: str
    prompt: str
    interval_seconds: int
    next_fire_at: float


class CompactionDetails(ProtocolModel):
    current_context_tokens: int | None = None
    threshold: int | None = None
    summary_length: int | None = None
    old_session_id: str | None = None
    new_session_id: str | None = None
