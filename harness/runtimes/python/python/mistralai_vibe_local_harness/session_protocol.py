"""Python models used by the local Harness Session Protocol adapter.

The Session Protocol is distinct from the Core-facing Step Protocol in
``protocol.py``. History entries remain raw JSON objects because the local
Runtime only needs to preserve and project their wire representation.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

type JsonObject = dict[str, JsonValue]
type SessionId = str
type TurnId = str
type EntryId = str
type EventId = int
type QueueItemId = str
type UnixTimeMilliseconds = int
type PublicCallbackId = str
type PublicCallbackKind = Literal["approval", "user_input"]
type PublicRetryCategory = Literal[
    "rate_limited", "server_error", "timed_out", "connection", "unknown"
]
type TitleSource = Literal["auto", "manual"]
type HarnessHookPoint = Literal[
    "pre_agent_turn",
    "post_agent_turn",
    "pre_llm_call",
    "post_llm_call",
    "pre_tool_call",
    "post_tool_call",
]

PROTOCOL_VERSION = 1

PUBLIC_SESSION_STATE_FORMAT = "harness.public-session-state/v1"


class SessionProtocolModel(BaseModel):
    """Base model for the camelCase Session Protocol wire format.

    The Step Protocol models in `protocol.py` are snake_case on the wire and
    alias individual fields; the Session Protocol is camelCase throughout, so
    the alias is generated instead.
    """

    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class UserDisplayContentAnnotation(SessionProtocolModel):
    version: str = Field(min_length=1)
    host: str = Field(min_length=1)
    content: list[dict[str, JsonValue]]

    @field_validator("version", "host")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class MessageAnnotations(SessionProtocolModel):
    vibe_user_display_content: UserDisplayContentAnnotation | None = Field(
        default=None,
        alias="vibe.userDisplayContent",
        exclude_if=lambda value: value is None,
    )


class TextContentBlock(SessionProtocolModel):
    type: Literal["text"] = "text"
    text: str = ""


class ImageContentBlock(SessionProtocolModel):
    type: Literal["image"] = "image"
    uri: str
    media_type: str | None = None
    alt_text: str | None = None


class ResourceLinkContentBlock(SessionProtocolModel):
    type: Literal["resource_link"] = "resource_link"
    uri: str
    name: str | None = None
    title: str | None = None
    description: str | None = None
    media_type: str | None = None
    size: int | None = Field(default=None, ge=0)


class EmbeddedResourceContentBlock(SessionProtocolModel):
    type: Literal["embedded_resource"] = "embedded_resource"
    uri: str
    media_type: str | None = None
    text: str | None = None
    blob: str | None = None

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if (self.text is None) == (self.blob is None):
            raise ValueError("Embedded resources require exactly one of text or blob")
        return self


ContentBlock = Annotated[
    TextContentBlock
    | ImageContentBlock
    | ResourceLinkContentBlock
    | EmbeddedResourceContentBlock,
    Field(discriminator="type"),
]


class TurnContextInputEntry(SessionProtocolModel):
    role: Literal["context"] = "context"
    entry_id: EntryId | None = None
    content: list[ContentBlock] = Field(min_length=1)
    annotations: MessageAnnotations = Field(default_factory=MessageAnnotations)


class TurnUserInputEntry(SessionProtocolModel):
    role: Literal["user"] = "user"
    entry_id: EntryId | None = None
    content: list[ContentBlock] = Field(min_length=1)
    annotations: MessageAnnotations = Field(default_factory=MessageAnnotations)


TurnInputEntry = Annotated[
    TurnContextInputEntry | TurnUserInputEntry, Field(discriminator="role")
]


def _validate_turn_input_entries(entries: list[TurnInputEntry]) -> None:
    user_positions = [
        index for index, entry in enumerate(entries) if entry.role == "user"
    ]
    if len(user_positions) > 1:
        raise ValueError("Turn input accepts at most one user entry")
    if user_positions and user_positions[0] != len(entries) - 1:
        raise ValueError("The user entry must be the final turn input entry")


class QueuedTurn(SessionProtocolModel):
    id: QueueItemId
    created_at: UnixTimeMilliseconds
    entries: list[TurnInputEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        _validate_turn_input_entries(self.entries)
        return self


TURN_QUEUE_MAX_ITEMS = 32


class TurnQueue(SessionProtocolModel):
    items: list[QueuedTurn]
    paused: bool
    max_items: Annotated[int, Field(ge=1)]


class ResolvedPluginDefinition(SessionProtocolModel):
    name: str
    namespace: str
    version: str | None = None
    source_format: str
    manifest_digest: str
    content_digest: str


type PluginComponentKind = Literal[
    "skill",
    "knowledge",
    "library",
    "mcp_server",
    "connector",
    "hook",
    "agent",
    "subagent",
    "tool",
    "unknown",
]


class PluginComponent(SessionProtocolModel):
    kind: PluginComponentKind
    name: str
    source_path: str | None = None
    config: dict[str, JsonValue] = Field(default_factory=dict)


class PluginInfo(SessionProtocolModel):
    workdir: str | None = None
    components: list[PluginComponent] = Field(default_factory=list)
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class TurnQueueUpdatedEvent(SessionProtocolModel):
    type: Literal["turn_queue_updated"] = "turn_queue_updated"
    queue: TurnQueue


class Event(SessionProtocolModel):
    event_id: str
    emitted_at: UnixTimeMilliseconds
    session_id: SessionId
    root_session_id: SessionId | None = None
    payload: TurnQueueUpdatedEvent


type Procedure = Literal[
    "app_server/session/start",
    "app_server/session/fork",
    "app_server/session/history/clear",
    "app_server/session/read",
    "app_server/session/history/list",
    "app_server/session/turns/list",
    "app_server/session/turn/start",
    "app_server/session/turn/enqueue",
    "app_server/session/turn/queue/read",
    "app_server/session/turn/queue/remove",
    "app_server/session/turn/queue/replace",
    "app_server/session/turn/queue/steer",
    "app_server/session/turn/queue/resume",
    "app_server/session/turn/steer",
    "app_server/session/turn/interrupt",
    "app_server/session/config/read",
    "app_server/session/config/write",
    "app_server/session/plugin/info",
    "app_server/session/plugin/reload",
    "app_server/session/callback/result",
    "app_server/session/events/read",
    "app_server/session/shellCommand",
    "app_server/session/shellCommand/interrupt",
]

APP_SERVER_SESSION_IMPLEMENTED_PROCEDURES: tuple[Procedure, ...] = (
    "app_server/session/start",
    "app_server/session/fork",
    "app_server/session/history/clear",
    "app_server/session/read",
    "app_server/session/history/list",
    "app_server/session/turns/list",
    "app_server/session/turn/start",
    "app_server/session/turn/enqueue",
    "app_server/session/turn/queue/read",
    "app_server/session/turn/queue/remove",
    "app_server/session/turn/queue/replace",
    "app_server/session/turn/queue/steer",
    "app_server/session/turn/queue/resume",
    "app_server/session/turn/steer",
    "app_server/session/turn/interrupt",
    "app_server/session/config/read",
    "app_server/session/config/write",
    "app_server/session/callback/result",
    "app_server/session/events/read",
    "app_server/session/shellCommand",
    "app_server/session/shellCommand/interrupt",
)

APP_SERVER_SESSION_PLUGIN_PROCEDURES: tuple[Procedure, ...] = (
    "app_server/session/plugin/info",
    "app_server/session/plugin/reload",
)


class ProtocolError(SessionProtocolModel):
    """A serializable failure. `retryable` describes executor behavior."""

    code: str
    message: str
    retryable: bool = False
    details: JsonValue = None


class PublicError(SessionProtocolModel):
    """Client-facing error details; retry policy remains runtime-private."""

    message: str
    code: str | None = None
    details: JsonValue = None


class TokenUsage(SessionProtocolModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)


class IdleSessionStatus(SessionProtocolModel):
    type: Literal["idle"] = "idle"


class RunningSessionStatus(SessionProtocolModel):
    type: Literal["running"] = "running"
    active_turn_id: TurnId


class BlockedSessionStatus(SessionProtocolModel):
    """Public blocking is reserved for approval and user input.

    Waiting on a private client tool or hook keeps the public session running.
    """

    type: Literal["blocked"] = "blocked"
    active_turn_id: TurnId
    callback_id: PublicCallbackId
    callback_kind: PublicCallbackKind


class FailedSessionStatus(SessionProtocolModel):
    type: Literal["failed"] = "failed"
    message: str


class ArchivedSessionStatus(SessionProtocolModel):
    type: Literal["archived"] = "archived"


PublicSessionStatus = Annotated[
    IdleSessionStatus
    | RunningSessionStatus
    | BlockedSessionStatus
    | FailedSessionStatus
    | ArchivedSessionStatus,
    Field(discriminator="type"),
]


class HistoryCursor(SessionProtocolModel):
    """Neighbours of a history page, or null at the oldest/newest boundary."""

    before: EntryId | None = None
    after: EntryId | None = None


class PublicHistoryPageBase(SessionProtocolModel):
    """A window of public history, always ordered from oldest to newest.

    `entries` remains raw JSON because this adapter preserves protocol-shaped
    history without owning the complete public history union.
    """

    entries: list[JsonObject] = Field(default_factory=list)
    cursor: HistoryCursor = Field(default_factory=HistoryCursor)


class LatestPublicHistoryPage(PublicHistoryPageBase):
    range: Literal["latest"] = "latest"


class PagedPublicHistoryPage(PublicHistoryPageBase):
    range: Literal["page"] = "page"


PublicHistoryPage = Annotated[
    LatestPublicHistoryPage | PagedPublicHistoryPage, Field(discriminator="range")
]


class InProgressPublicTurn(SessionProtocolModel):
    id: TurnId
    session_id: SessionId
    status: Literal["in_progress"] = "in_progress"
    queue_item_id: str | None = None
    started_at: UnixTimeMilliseconds


class CompletedPublicTurn(SessionProtocolModel):
    id: TurnId
    session_id: SessionId
    status: Literal["completed"] = "completed"
    queue_item_id: str | None = None
    started_at: UnixTimeMilliseconds
    completed_at: UnixTimeMilliseconds
    stop_reason: Literal["limit"] | None = None


class FailedPublicTurn(SessionProtocolModel):
    id: TurnId
    session_id: SessionId
    status: Literal["failed"] = "failed"
    queue_item_id: str | None = None
    started_at: UnixTimeMilliseconds
    completed_at: UnixTimeMilliseconds
    error: PublicError


class InterruptedPublicTurn(SessionProtocolModel):
    id: TurnId
    session_id: SessionId
    status: Literal["interrupted"] = "interrupted"
    queue_item_id: str | None = None
    started_at: UnixTimeMilliseconds
    completed_at: UnixTimeMilliseconds
    reason: str | None = None


PublicTurn = Annotated[
    InProgressPublicTurn
    | CompletedPublicTurn
    | FailedPublicTurn
    | InterruptedPublicTurn,
    Field(discriminator="status"),
]


class PublicSession(SessionProtocolModel):
    id: SessionId
    root_session_id: SessionId | None = None
    parent_session_id: SessionId | None = None
    title: str | None = None
    # Whether ``title`` was set by a user rename ("manual") or auto-generated
    # ("auto"). On resume this lets the runtime keep refreshing an auto title
    # while leaving a manual one alone.
    title_source: TitleSource = "auto"
    preview: str = ""
    status: PublicSessionStatus
    created_at: UnixTimeMilliseconds
    updated_at: UnixTimeMilliseconds
    token_usage: TokenUsage | None = None
    context_usage: TokenUsage | None = None


class PublicRetryState(SessionProtocolModel):
    turn_id: TurnId
    category: PublicRetryCategory
    detail: str


class PublicSessionState(SessionProtocolModel):
    format: Literal["harness.public-session-state/v1"] = (
        "harness.public-session-state/v1"
    )
    session: PublicSession
    history: LatestPublicHistoryPage = Field(default_factory=LatestPublicHistoryPage)
    active_callbacks: list[JsonObject] = Field(default_factory=list)
    latest_turn: PublicTurn | None = None
    turn_queue: TurnQueue
    retrying: PublicRetryState | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class SessionSnapshot(SessionProtocolModel):
    """Public state plus the watermark of the last event it includes.

    The watermark is separate from mutable public state and is never a private
    storage offset.
    """

    state: PublicSessionState
    history_limit: int = Field(ge=0)
    watermark: EventId = Field(ge=0)


class ClientSessionExtensions(SessionProtocolModel):
    """Client-provided session capabilities.

    Model routing, credentials, workspace trust, plugins, and product
    configuration are Runtime/Host configuration and never arrive here.

    `client_tool_groups` is raw JSON at this boundary. The Step Protocol's
    `RustToolGroupDefinition` models the same concept but serializes snake_case
    for the Core binding, so it cannot be reused on this camelCase wire.
    """

    client_tool_groups: list[JsonObject] = Field(default_factory=list)
    client_hooks: list[HarnessHookPoint] = Field(default_factory=list)


class SessionStartParams(SessionProtocolModel):
    client_extensions: ClientSessionExtensions = Field(
        default_factory=ClientSessionExtensions
    )
    history_limit: int = Field(ge=0)


class SessionStartResult(SessionProtocolModel):
    snapshot: SessionSnapshot


class SessionReadParams(SessionProtocolModel):
    session_id: SessionId
    history_limit: int = Field(ge=0)


class SessionReadResult(SessionProtocolModel):
    snapshot: SessionSnapshot


class SessionCloseParams(SessionProtocolModel):
    session_id: SessionId


class SessionCloseResult(SessionProtocolModel):
    """`closed` is true when this call archived the session.

    `session/close` is idle-only and idempotently archives the session. It
    never implicitly interrupts a turn or cancels a callback.
    """

    closed: bool


class _TurnQueueInputParams(SessionProtocolModel):
    idempotency_key: str | None = None
    session_id: SessionId
    entries: list[TurnInputEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        _validate_turn_input_entries(self.entries)
        return self


class TurnEnqueueParams(_TurnQueueInputParams):
    pass


class TurnEnqueueResponse(SessionProtocolModel):
    queue_item_id: QueueItemId


class TurnQueueReadParams(SessionProtocolModel):
    session_id: SessionId


class TurnQueueReadResponse(SessionProtocolModel):
    queue: TurnQueue


class TurnQueueRemoveParams(SessionProtocolModel):
    session_id: SessionId
    queue_item_id: QueueItemId


class TurnQueueRemoveResponse(SessionProtocolModel):
    pass


class TurnQueueReplaceParams(_TurnQueueInputParams):
    queue_item_id: QueueItemId


class TurnQueueReplaceResponse(SessionProtocolModel):
    queue_item_id: QueueItemId


class TurnQueueSteerParams(SessionProtocolModel):
    session_id: SessionId
    queue_item_id: QueueItemId
    expected_turn_id: TurnId


class TurnQueueSteerResponse(SessionProtocolModel):
    queue_item_id: QueueItemId
    turn_id: TurnId


class TurnQueueResumeParams(SessionProtocolModel):
    session_id: SessionId


class TurnQueueResumeResponse(SessionProtocolModel):
    pass


__all__ = [
    "APP_SERVER_SESSION_IMPLEMENTED_PROCEDURES",
    "APP_SERVER_SESSION_PLUGIN_PROCEDURES",
    "PROTOCOL_VERSION",
    "PUBLIC_SESSION_STATE_FORMAT",
    "TURN_QUEUE_MAX_ITEMS",
    "ArchivedSessionStatus",
    "BlockedSessionStatus",
    "ClientSessionExtensions",
    "CompletedPublicTurn",
    "ContentBlock",
    "EmbeddedResourceContentBlock",
    "EntryId",
    "Event",
    "EventId",
    "FailedPublicTurn",
    "FailedSessionStatus",
    "HarnessHookPoint",
    "HistoryCursor",
    "IdleSessionStatus",
    "ImageContentBlock",
    "InProgressPublicTurn",
    "InterruptedPublicTurn",
    "JsonObject",
    "LatestPublicHistoryPage",
    "MessageAnnotations",
    "PagedPublicHistoryPage",
    "PluginComponent",
    "PluginComponentKind",
    "PluginInfo",
    "Procedure",
    "ProtocolError",
    "PublicCallbackId",
    "PublicCallbackKind",
    "PublicError",
    "PublicHistoryPage",
    "PublicHistoryPageBase",
    "PublicRetryCategory",
    "PublicRetryState",
    "PublicSession",
    "PublicSessionState",
    "PublicSessionStatus",
    "PublicTurn",
    "QueueItemId",
    "QueuedTurn",
    "ResolvedPluginDefinition",
    "ResourceLinkContentBlock",
    "RunningSessionStatus",
    "SessionCloseParams",
    "SessionCloseResult",
    "SessionId",
    "SessionProtocolModel",
    "SessionReadParams",
    "SessionReadResult",
    "SessionSnapshot",
    "SessionStartParams",
    "SessionStartResult",
    "TextContentBlock",
    "TokenUsage",
    "TurnContextInputEntry",
    "TurnEnqueueParams",
    "TurnEnqueueResponse",
    "TurnId",
    "TurnInputEntry",
    "TurnQueue",
    "TurnQueueReadParams",
    "TurnQueueReadResponse",
    "TurnQueueRemoveParams",
    "TurnQueueRemoveResponse",
    "TurnQueueReplaceParams",
    "TurnQueueReplaceResponse",
    "TurnQueueResumeParams",
    "TurnQueueResumeResponse",
    "TurnQueueSteerParams",
    "TurnQueueSteerResponse",
    "TurnQueueUpdatedEvent",
    "TurnUserInputEntry",
    "UnixTimeMilliseconds",
    "UserDisplayContentAnnotation",
]
