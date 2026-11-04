"""Retained public history entries for one Session."""

from typing import Annotated, Any, Literal, Self, get_args

from pydantic import (
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    Tag,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)

from ....agent_messages import AgentMessageMetadata, AgentResultInput
from .base import JsonSchema, SessionModel, read_unknown_variant, write_unknown_variant
from .common import (
    AbsolutePath,
    GenerationStatus,
    HistoryEntryId,
    MessageRole,
    ProtocolError,
    ToolCallId,
    ToolKind,
    TurnId,
    UnixMs,
    Uri,
)
from .content import ContentBlock, TextContentBlock, refuse_unsupported_blocks

type FileChangeStatus = Literal["proposed", "approved", "denied", "applied", "failed"]
type NoticeLevel = Literal["info", "warning", "error"]
type NoticeKind = Literal["tool_discovery", "operation_progress"]
type CheckpointKind = Literal["compaction", "rollback", "resume", "custom"]


class HistoryImportMessage(SessionModel):
    type: Literal["message"] = "message"
    role: MessageRole
    content: list[ContentBlock] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def _refuse_unsupported_blocks(cls, content: list[ContentBlock]) -> list[ContentBlock]:
        # Imported history is replayed to the model, which can only take blocks a Turn can run.
        return refuse_unsupported_blocks(content)


class HistoryImportReasoning(SessionModel):
    type: Literal["reasoning"] = "reasoning"
    text: str


class HistoryImportToolCall(SessionModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: ToolCallId
    tool_kind: ToolKind = "local"
    name: str
    arguments: JsonValue = None


class HistoryImportToolResult(SessionModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: ToolCallId
    name: str | None = None
    result: JsonValue = None
    error: ProtocolError | None = None
    annotations: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.error is not None and self.result is not None:
            raise ValueError("Imported tool results cannot contain both result and error")
        return self


class HistoryImportSnapshot(SessionModel):
    type: Literal["snapshot"] = "snapshot"
    task_name: str
    snapshot_type: str
    value: JsonValue = None


type HistoryImportEntry = Annotated[
    HistoryImportMessage
    | HistoryImportReasoning
    | HistoryImportToolCall
    | HistoryImportToolResult
    | HistoryImportSnapshot,
    Field(discriminator="type"),
]


class HistoryEntryBase(SessionModel):
    """Fields shared by every retained public history entry."""

    id: HistoryEntryId
    index: Annotated[int, Field(ge=0)]
    turn_id: TurnId | None = None
    generation_status: GenerationStatus
    created_at: UnixMs
    updated_at: UnixMs
    annotations: dict[str, JsonValue] = Field(default_factory=dict)


class MessageHistoryEntry(HistoryEntryBase):
    type: Literal["message"] = "message"
    role: MessageRole
    content: list[ContentBlock] = Field(default_factory=list)


class AgentContextMessage(AgentMessageMetadata):
    """An admitted agent input, independent of its public history position."""

    content: Annotated[list[TextContentBlock], Field(min_length=1)]


class AgentContextMessageHistoryEntry(HistoryEntryBase, AgentContextMessage):
    type: Literal["message"] = "message"
    role: Literal["context"] = "context"


class AgentResultContextMessageHistoryEntry(HistoryEntryBase, AgentResultInput):
    type: Literal["message"] = "message"
    role: Literal["context"] = "context"


class UnknownMessageHistoryEntry(HistoryEntryBase):
    """A message entry whose role, or context kind, this reader does not know.

    A newer Runtime can add message roles and context kinds. A reader keeps the
    entry's position and role so that one new variant does not fail the whole
    history or event. It also keeps the rest of the entry's fields as they came, so
    later patches to the entry still apply. Known roles and kinds still validate strictly.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["message"] = "message"
    role: str
    kind: str | None = None


_MESSAGE_ROLES: frozenset[str] = frozenset(get_args(MessageRole.__value__))
_CONTEXT_KINDS: dict[str, str] = {
    "task": "context",
    "message": "context",
    "result": "context_result",
}


def _message_variant(value: object) -> str:
    if isinstance(value, dict):
        role, kind = value.get("role"), value.get("kind")
    else:
        role, kind = getattr(value, "role", None), getattr(value, "kind", None)
    if role in _MESSAGE_ROLES:
        return "role"
    # A missing or non-string role is malformed, not unknown: let the strict model reject it.
    if not isinstance(role, str):
        return "role"
    if role == "context" and isinstance(kind, str):
        return _CONTEXT_KINDS.get(kind, "unknown")
    return "unknown"


type MessageHistoryEntryVariant = Annotated[
    Annotated[MessageHistoryEntry, Tag("role")]
    | Annotated[AgentContextMessageHistoryEntry, Tag("context")]
    | Annotated[AgentResultContextMessageHistoryEntry, Tag("context_result")]
    | Annotated[UnknownMessageHistoryEntry, Tag("unknown")],
    Discriminator(_message_variant),
]


class ReasoningHistoryEntry(HistoryEntryBase):
    type: Literal["reasoning"] = "reasoning"
    summary: list[str] = Field(default_factory=list)
    text: str = ""


class PlanItem(SessionModel):
    id: str
    text: str
    status: Literal["pending", "generating", "complete"]


class PlanHistoryEntry(HistoryEntryBase):
    type: Literal["plan"] = "plan"
    items: list[PlanItem] = Field(default_factory=list)
    text: str | None = None


class ToolCallHistoryEntry(HistoryEntryBase):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: ToolCallId
    tool_kind: ToolKind
    name: str
    arguments: JsonValue = None
    input_schema: JsonSchema | None = None
    output_schema: JsonSchema | None = None


class ToolResultHistoryEntry(HistoryEntryBase):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: ToolCallId
    name: str | None = None
    result: JsonValue = None
    error: ProtocolError | None = None
    duration_ms: Annotated[int | None, Field(ge=0)] = None
    content: list[ContentBlock] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "The tool's content blocks when `result` does not hold them: the result has "
            "structured output, or the call failed."
        ),
    )
    meta: dict[str, JsonValue] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Result-level host metadata, MCP `_meta`. Never shown to the model.",
    )


class FileChange(SessionModel):
    path: AbsolutePath
    kind: Literal["create", "modify", "delete", "rename"]
    old_path: AbsolutePath | None = None
    diff: str | None = None


class FileChangeHistoryEntry(HistoryEntryBase):
    type: Literal["file_change"] = "file_change"
    changes: list[FileChange] = Field(default_factory=list)
    status: FileChangeStatus


class ResourceHistoryEntry(HistoryEntryBase):
    type: Literal["resource"] = "resource"
    uri: Uri
    title: str | None = None
    media_type: str | None = None


class CheckpointHistoryEntry(HistoryEntryBase):
    type: Literal["checkpoint"] = "checkpoint"
    checkpoint_kind: CheckpointKind
    message: str | None = None
    details: JsonValue = None


class ErrorHistoryEntry(HistoryEntryBase):
    type: Literal["error"] = "error"
    error: ProtocolError


class NoticeHistoryEntry(HistoryEntryBase):
    type: Literal["notice"] = "notice"
    level: NoticeLevel
    notice_kind: NoticeKind | None = None
    message: str
    details: JsonValue = None


class UnknownHistoryEntry(HistoryEntryBase):
    """A history entry of a type this reader does not know.

    A newer Runtime can add entry types. Such an entry is kept, so that it does not fail
    a whole history page or drop the event that carries it. Its type is saved in
    ``original_type`` and its other fields are kept as they came, so later patches to it
    still apply. Its own ``type`` is ``unknown``, so a ``match`` on ``type`` still works
    for the known entries. Serialized, it has its own ``type`` and no ``original_type``; its
    own fields are as they came, the fields every entry shares as this reader models them.
    Entries of a known type are validated as before.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["unknown"] = "unknown"
    original_type: str

    @model_validator(mode="before")
    @classmethod
    def wrap_wire_entry(cls, value: object) -> object:
        return read_unknown_variant(value)

    @model_serializer(mode="wrap")
    def write_wire_entry(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        return write_unknown_variant(handler(self), self.original_type, self.model_extra, info)


def _history_entry_variant(value: object) -> str | None:
    entry_type = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    # A missing or non-string type is malformed, not unknown: no variant accepts it.
    if not isinstance(entry_type, str):
        return None
    return entry_type if entry_type in _HISTORY_ENTRY_TYPES else "unknown"


type HistoryEntry = Annotated[
    Annotated[MessageHistoryEntryVariant, Tag("message")]
    | Annotated[ReasoningHistoryEntry, Tag("reasoning")]
    | Annotated[PlanHistoryEntry, Tag("plan")]
    | Annotated[ToolCallHistoryEntry, Tag("tool_call")]
    | Annotated[ToolResultHistoryEntry, Tag("tool_result")]
    | Annotated[FileChangeHistoryEntry, Tag("file_change")]
    | Annotated[ResourceHistoryEntry, Tag("resource")]
    | Annotated[CheckpointHistoryEntry, Tag("checkpoint")]
    | Annotated[ErrorHistoryEntry, Tag("error")]
    | Annotated[NoticeHistoryEntry, Tag("notice")]
    | Annotated[UnknownHistoryEntry, Tag("unknown")],
    Discriminator(_history_entry_variant),
]

# The tags of the union's variants, so a new entry type is known once it joins the union.
_HISTORY_ENTRY_TYPES: frozenset[str] = frozenset(
    metadata.tag
    for variant in get_args(get_args(HistoryEntry.__value__)[0])
    for metadata in variant.__metadata__
    if isinstance(metadata, Tag) and metadata.tag != "unknown"
)

HistoryEntryAdapter: TypeAdapter[HistoryEntry] = TypeAdapter(HistoryEntry)
