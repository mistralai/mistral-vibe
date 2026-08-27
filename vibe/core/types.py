from __future__ import annotations

from abc import ABC
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterator, Sequence
import copy
from enum import StrEnum, auto
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Annotated, Any, Literal, overload
from uuid import uuid4

if TYPE_CHECKING:
    from vibe.core.tools.base import BaseTool
else:
    BaseTool = Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PrivateAttr,
    computed_field,
    model_validator,
)

from vibe.core.experiments.models import EvalResponse
from vibe.core.tools.models import RequiredPermission
from vibe.user_content import UserDisplayContent, UserResource
from vibe.utils.pricing import session_token_cost
from vibe.utils.tool_presentation import ToolCallPresentation, ToolResultPresentation


class ScheduledLoop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    interval_seconds: int
    prompt: str
    next_fire_at: float
    created_at: float


class Backend(StrEnum):
    MISTRAL = auto()
    GENERIC = auto()


class AgentStats(BaseModel):
    steps: int = 0
    session_prompt_tokens: int = 0
    session_completion_tokens: int = 0
    session_cached_tokens: int = 0
    tool_calls_agreed: int = 0
    tool_calls_rejected: int = 0
    tool_calls_hook_denied: int = 0
    tool_calls_failed: int = 0
    tool_calls_succeeded: int = 0

    context_tokens: int = 0

    last_turn_prompt_tokens: int = 0
    last_turn_completion_tokens: int = 0
    last_turn_cached_tokens: int = 0
    last_turn_duration: float = 0.0
    tokens_per_second: float = 0.0

    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    cached_input_price_per_million: float | None = None

    _listeners: dict[str, Callable[[AgentStats], None]] = PrivateAttr(
        default_factory=dict
    )

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in self._listeners:
            self._listeners[name](self)

    def trigger_listeners(self) -> None:
        for listener in self._listeners.values():
            listener(self)

    def add_listener(
        self, attr_name: str, listener: Callable[[AgentStats], None]
    ) -> None:
        self._listeners[attr_name] = listener

    @staticmethod
    def create_fresh(previous: AgentStats) -> AgentStats:
        fresh = AgentStats()
        fresh._listeners = previous._listeners.copy()
        return fresh

    @computed_field
    @property
    def session_total_llm_tokens(self) -> int:
        return self.session_prompt_tokens + self.session_completion_tokens

    @computed_field
    @property
    def last_turn_total_tokens(self) -> int:
        return self.last_turn_prompt_tokens + self.last_turn_completion_tokens

    @computed_field
    @property
    def session_cost(self) -> float:
        """Total session cost in dollars from token usage and pricing.

        If the model changes mid-session, this uses current pricing for all tokens.
        """
        return session_token_cost(
            prompt_tokens=self.session_prompt_tokens,
            completion_tokens=self.session_completion_tokens,
            cached_tokens=self.session_cached_tokens,
            input_price_per_million=self.input_price_per_million,
            output_price_per_million=self.output_price_per_million,
            cached_input_price_per_million=self.cached_input_price_per_million,
        )

    def update_pricing(
        self,
        input_price: float,
        output_price: float,
        cached_input_price: float | None = None,
    ) -> None:
        """Update pricing info when model changes.

        NOTE: session_cost will be recalculated using new pricing for all
        accumulated tokens. This is a known approximation when models change.
        This should not be a big issue, pricing is only used for max_price which is in
        programmatic mode, so user should not update models there.
        """
        self.input_price_per_million = input_price
        self.output_price_per_million = output_price
        self.cached_input_price_per_million = cached_input_price

    def reset_context_state(self) -> None:
        """Reset context-related fields while preserving cumulative session stats.

        Used after config reload or similar operations where the context
        changes but we want to preserve session totals.
        """
        self.context_tokens = 0
        self.last_turn_prompt_tokens = 0
        self.last_turn_completion_tokens = 0
        self.last_turn_cached_tokens = 0
        self.last_turn_duration = 0.0
        self.tokens_per_second = 0.0


class SessionInfo(BaseModel):
    session_id: str
    start_time: str
    message_count: int
    stats: AgentStats
    save_dir: str


class ChildSessionLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    tool_call_id: str
    agent: str
    relative_path: str | None = None


# Session state rather than a message, because the worktree is created before
# the session has a first turn: there is no message it could belong to, and the
# transcript is rebuilt on every resume from what was persisted here.
class WorktreeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    name: str
    branch: str
    path: str
    created_at: int


class SessionMetadata(BaseModel):
    session_id: str
    parent_session_id: str | None = None
    start_time: str
    end_time: str | None
    git_commit: str | None
    git_branch: str | None
    environment: dict[str, str | None]
    # Where the session began. ``environment.working_directory`` follows it as
    # it moves, so between them the record names both the directory the user
    # started in and the one the session is working in. Neither on its own can
    # do that, which is the whole reason this field exists.
    origin_directory: str | None = None
    username: str
    child_sessions: list[ChildSessionLink] = Field(default_factory=list)
    loops: list[ScheduledLoop] = Field(default_factory=list)
    title: str | None = None
    title_source: Literal["auto", "manual"] = "auto"
    # The sticky GrowthBook variant assignment, persisted so a resumed session
    # keeps its buckets. NOTE: plan/org attributes and user_plan are user-scoped,
    # not session-scoped, so they are deliberately NOT persisted here — they are
    # re-resolved from the user-scoped whoami/identity cache on every session, so
    # resuming never reports a stale plan.
    experiments: EvalResponse | None = None
    import_provenance: dict[str, JsonValue] | None = None
    created_worktree: WorktreeContext | None = None


StrToolChoice = Literal["auto", "none", "any", "required"]


class AvailableFunction(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class AvailableTool(BaseModel):
    type: Literal["function"] = "function"
    function: AvailableFunction


class FunctionCall(BaseModel):
    name: str | None = None
    arguments: str | None = None


class ToolCall(BaseModel):
    id: str | None = None
    index: int | None = None
    function: FunctionCall = Field(default_factory=FunctionCall)
    type: Literal["function"] = "function"
    presentation: ToolCallPresentation | None = None


def _content_before(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        parts: list[str] = []
        for p in v:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            else:
                parts.append(str(p))
        return "\n".join(parts)
    return str(v)


Content = Annotated[str, BeforeValidator(_content_before)]


class Role(StrEnum):
    system = auto()
    user = auto()
    assistant = auto()
    tool = auto()


class ApprovalResponse(StrEnum):
    YES = "y"
    NO = "n"


class FileImageSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["file"] = "file"
    path: Path


class InlineImageSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["inline"] = "inline"
    # Raw base64-encoded bytes (no `data:` prefix). Used when the image has no
    # durable file on disk (session logging disabled): memory-only, never
    # persisted to a session transcript.
    data: str


class ImageAttachment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: Annotated[FileImageSource | InlineImageSource, Field(discriminator="kind")]
    alias: str
    mime_type: str

    @model_validator(mode="before")
    @classmethod
    def _migrate_flat_source(cls, value: Any) -> Any:
        # Accept and migrate the legacy flat shape `{path|data, ...}` from older
        # session transcripts.
        if not isinstance(value, dict) or "source" in value:
            return value
        if value.get("path") is not None:
            return {**value, "source": {"kind": "file", "path": value["path"]}}
        if value.get("data") is not None:
            return {**value, "source": {"kind": "inline", "data": value["data"]}}
        return value


class PersistedToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: dict[str, JsonValue]
    duration: float | None = Field(default=None, ge=0)
    cancelled: bool = False
    presentation: ToolResultPresentation | None = None


class ManualShellContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    command: str
    cwd: str
    stdout: str = ""
    stderr: str = ""
    output_text: str = ""
    exit_code: int
    timed_out: bool = False
    interrupted: bool = False
    duration_ms: float = Field(default=0.0, ge=0)
    created_at: int


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Role
    content: Content | None = None
    images: list[ImageAttachment] | None = None
    injected: bool = False
    reasoning_content: Content | None = None
    reasoning_payloads: list[dict[str, Any]] | None = None
    reasoning_message_id: str | None = None
    tool_calls: list[ToolCall] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_result: PersistedToolResult | None = None
    message_id: str | None = None
    user_display_content: UserDisplayContent | None = None
    input_text: str | None = None
    resources: list[UserResource] | None = None
    manual_shell: ManualShellContext | None = None
    context_boundary: Literal["compaction"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_any(cls, v: Any) -> dict[str, Any] | Any:
        if isinstance(v, dict):
            v.setdefault("content", "")
            v.setdefault("role", "assistant")
            if v.get("message_id") is None and v.get("role") != "tool":
                v["message_id"] = str(uuid4())
            if v.get("reasoning_message_id") is None and v.get("reasoning_content"):
                v["reasoning_message_id"] = str(uuid4())
            return v
        role = str(getattr(v, "role", "assistant"))
        reasoning_content = getattr(v, "reasoning_content", None)
        return {
            "role": role,
            "content": getattr(v, "content", ""),
            "reasoning_content": reasoning_content,
            "reasoning_payloads": getattr(v, "reasoning_payloads", None),
            "reasoning_message_id": getattr(v, "reasoning_message_id", None)
            or (str(uuid4()) if reasoning_content else None),
            "tool_calls": getattr(v, "tool_calls", None),
            "name": getattr(v, "name", None),
            "tool_call_id": getattr(v, "tool_call_id", None),
            "tool_result": getattr(v, "tool_result", None),
            "images": getattr(v, "images", None),
            "message_id": getattr(v, "message_id", None)
            or (str(uuid4()) if role != "tool" else None),
            "user_display_content": getattr(v, "user_display_content", None),
            "input_text": getattr(v, "input_text", None),
            "resources": getattr(v, "resources", None),
            "manual_shell": getattr(v, "manual_shell", None),
            "context_boundary": getattr(v, "context_boundary", None),
        }

    def __add__(self, other: LLMMessage) -> LLMMessage:
        """Careful: this is not commutative!"""
        if self.role != other.role:
            raise ValueError("Can't accumulate messages with different roles")

        if self.name != other.name:
            raise ValueError("Can't accumulate messages with different names")

        if self.tool_call_id != other.tool_call_id:
            raise ValueError("Can't accumulate messages with different tool_call_ids")

        content = (self.content or "") + (other.content or "")
        if not content:
            content = None

        reasoning_content = (self.reasoning_content or "") + (
            other.reasoning_content or ""
        )
        if not reasoning_content:
            reasoning_content = None

        reasoning_payloads = [
            *(self.reasoning_payloads or []),
            *(other.reasoning_payloads or []),
        ] or None

        tool_calls_map = OrderedDict[int, ToolCall]()
        for tool_calls in [self.tool_calls or [], other.tool_calls or []]:
            for tc in tool_calls:
                if tc.index is None:
                    raise ValueError("Tool call chunk missing index")
                if tc.index not in tool_calls_map:
                    tool_calls_map[tc.index] = copy.deepcopy(tc)
                else:
                    existing_name = tool_calls_map[tc.index].function.name
                    new_name = tc.function.name
                    if existing_name and new_name and existing_name != new_name:
                        raise ValueError(
                            "Can't accumulate messages with different tool call names"
                        )
                    if new_name and not existing_name:
                        tool_calls_map[tc.index].function.name = new_name
                    new_args = (tool_calls_map[tc.index].function.arguments or "") + (
                        tc.function.arguments or ""
                    )
                    tool_calls_map[tc.index].function.arguments = new_args

        return LLMMessage(
            role=self.role,
            content=content,
            images=self.images if self.images is not None else other.images,
            reasoning_content=reasoning_content,
            reasoning_payloads=reasoning_payloads,
            reasoning_message_id=self.reasoning_message_id
            or other.reasoning_message_id,
            tool_calls=list(tool_calls_map.values()) or None,
            name=self.name,
            tool_call_id=self.tool_call_id,
            tool_result=self.tool_result or other.tool_result,
            message_id=self.message_id,
            user_display_content=self.user_display_content
            if self.user_display_content is not None
            else other.user_display_content,
            input_text=(
                self.input_text if self.input_text is not None else other.input_text
            ),
            resources=self.resources if self.resources is not None else other.resources,
            context_boundary=self.context_boundary or other.context_boundary,
        )


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Prompt tokens served from the provider cache; a subset of prompt_tokens.
    cached_tokens: int = 0

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


class StopReason(StrEnum):
    REFUSAL = "refusal"


class StopInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    reason: str | None = None
    category: str | None = None
    explanation: str | None = None

    @property
    def is_refusal(self) -> bool:
        return self.reason == StopReason.REFUSAL


class LLMChunk(BaseModel):
    model_config = ConfigDict(frozen=True)
    message: LLMMessage
    usage: LLMUsage | None = None
    correlation_id: str | None = None
    stop: StopInfo | None = None

    def __add__(self, other: LLMChunk) -> LLMChunk:
        if self.usage is None and other.usage is None:
            new_usage = None
        else:
            new_usage = (self.usage or LLMUsage()) + (other.usage or LLMUsage())
        return LLMChunk(
            message=self.message + other.message,
            usage=new_usage,
            correlation_id=other.correlation_id or self.correlation_id,
            stop=other.stop or self.stop,
        )


class BaseEvent(BaseModel, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True)


class UserMessageEvent(BaseEvent):
    content: str
    message_id: str
    images: list[ImageAttachment] = Field(default_factory=list)
    user_display_content: UserDisplayContent | None = None
    resources: list[UserResource] = Field(default_factory=list)


class AssistantEvent(BaseEvent):
    content: str
    stopped_by_middleware: bool = False
    message_id: str | None = None

    def __add__(self, other: AssistantEvent) -> AssistantEvent:
        return AssistantEvent(
            content=self.content + other.content,
            stopped_by_middleware=self.stopped_by_middleware
            or other.stopped_by_middleware,
            message_id=self.message_id or other.message_id,
        )


class ReasoningEvent(BaseEvent):
    content: str
    message_id: str | None = None


class ToolCallEvent(BaseEvent):
    tool_call_id: str
    tool_name: str
    tool_class: type[BaseTool]
    tool_call_index: int | None = None
    args: BaseModel | None = None
    presentation: ToolCallPresentation | None = None


class ToolResultEvent(BaseEvent):
    tool_name: str
    tool_class: type[BaseTool] | None
    result: BaseModel | None = None
    error: str | None = None
    error_display: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    cancelled: bool = False
    duration: float | None = None
    tool_call_id: str
    presentation: ToolResultPresentation | None = None


class ToolStreamEvent(BaseEvent):
    tool_name: str
    message: str
    tool_call_id: str


class WaitingForInputEvent(BaseEvent):
    task_id: str
    label: str | None = None
    predefined_answers: list[str] | None = None


class RequestEvent(BaseEvent):
    request_id: str


class ApprovalRequestEvent(RequestEvent):
    tool_name: str
    tool_args: BaseModel
    tool_call_id: str
    required_permissions: list[RequiredPermission] | None = None


class UserInputRequestEvent(RequestEvent):
    args: BaseModel
    tool_call_id: str


class TokenUsageUpdatedEvent(BaseEvent):
    stats: AgentStats
    context_window: int


class CompactStartEvent(BaseEvent):
    current_context_tokens: int
    threshold: int
    # WORKAROUND: Using tool_call to communicate compact events to the client.
    # This should be revisited when the ACP protocol defines how compact events
    # should be represented.
    # [RFD](https://agentclientprotocol.com/rfds/session-usage)
    tool_call_id: str


class CompactEndEvent(BaseEvent):
    summary_length: int
    old_session_id: str | None = None
    new_session_id: str | None = None
    # WORKAROUND: Using tool_call to communicate compact events to the client.
    # This should be revisited when the ACP protocol defines how compact events
    # should be represented.
    # [RFD](https://agentclientprotocol.com/rfds/session-usage)
    tool_call_id: str


class PlanReviewRequestedEvent(BaseEvent):
    file_path: Path


class PlanReviewEndedEvent(BaseEvent):
    pass


class AgentProfileChangedEvent(BaseEvent):
    """Emitted when the active agent profile changes during a turn."""

    agent_name: str


class ContextClearedEvent(BaseEvent):
    """Emitted after the context is cleared on plan accept."""

    plan_file_path: Path | None = None


class SessionTitleUpdatedEvent(BaseEvent):
    title: str
    session_id: str


type SwitchAgentCallback = Callable[[str], Awaitable[None]]

type ClearContextCallback = Callable[[], Awaitable[None]]


class MessageList(Sequence[LLMMessage]):
    def __init__(self, initial: list[LLMMessage] | None = None) -> None:
        self._data: list[LLMMessage] = list(initial) if initial else []
        self._reset_hooks: list[Callable[[], None]] = []
        self._lock = threading.RLock()

    def append(self, msg: LLMMessage) -> None:
        self._data.append(msg)

    def insert(self, i: int, msg: LLMMessage) -> None:
        self._data.insert(i, msg)

    def extend(self, msgs: list[LLMMessage]) -> None:
        for msg in msgs:
            self.append(msg)

    def on_reset(self, hook: Callable[[], None]) -> None:
        """Register a callback that fires whenever the list is reset."""
        self._reset_hooks.append(hook)

    def reset(self, new: list[LLMMessage]) -> None:
        """Replace contents and fire reset hooks (e.g. RewindManager)."""
        with self._lock:
            self._data = list(new)
            for hook in self._reset_hooks:
                hook()

    def reset_preserving_system(self, tail: list[LLMMessage]) -> None:
        """Atomically keep existing system messages and replace the rest.

        Held under the same lock as ``update_system_prompt`` so a concurrent
        deferred-init thread cannot interleave its system-prompt insert with
        this read-then-replace and corrupt the list.
        """
        with self._lock:
            system = [m for m in self._data if m.role is Role.system]
            self._data = [*system, *tail]
            for hook in self._reset_hooks:
                hook()

    def update_system_prompt(self, new: str) -> None:
        """Replace the system prompt, or insert it if none exists yet.

        Under deferred init the prompt can land after messages were already
        appended, so insert at the front rather than clobber slot 0.
        """
        msg = LLMMessage(role=Role.system, content=new)
        with self._lock:
            if self._data and self._data[0].role == Role.system:
                self._data[0] = msg
            else:
                self._data.insert(0, msg)

    def __len__(self) -> int:
        return len(self._data)

    @overload
    def __getitem__(self, index: int) -> LLMMessage: ...
    @overload
    def __getitem__(self, index: slice) -> list[LLMMessage]: ...
    def __getitem__(self, index: int | slice) -> LLMMessage | list[LLMMessage]:
        return self._data[index]

    def __iter__(self) -> Iterator[LLMMessage]:
        # Snapshot under the lock: only __iter__ hands element-by-element
        # control back to Python, so the deferred-init thread's insert(0)
        # could shift indices mid-iteration. Other read dunders are single
        # C-level ops and stay atomic under the GIL without locking.
        with self._lock:
            return iter(list(self._data))

    def __contains__(self, item: object) -> bool:
        return item in self._data

    def __bool__(self) -> bool:
        return bool(self._data)


class RateLimitError(Exception):
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        super().__init__(
            "Rate limits exceeded. Please wait a moment before trying again."
        )


class ContextTooLongError(Exception):
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        super().__init__(
            "The conversation context exceeds the model's maximum limit. "
            "Use /rewind to undo recent actions, then /compact to summarize the conversation."
        )


class ResponseTooLongError(Exception):
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        super().__init__(
            "The model's response exceeded the maximum output token limit."
        )


class RefusalError(Exception):
    def __init__(
        self,
        provider: str,
        model: str,
        category: str | None = None,
        explanation: str | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.category = category
        self.explanation = explanation
        super().__init__(self._fmt())

    def _fmt(self) -> str:
        lead = "The model declined to respond to this request and stopped early."
        if self.category:
            lead += f" (category: {self.category})"
        detail = self.explanation or (
            "Try rephrasing your request or starting a new conversation."
        )
        return f"{lead} {detail}"
