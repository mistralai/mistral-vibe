"""Ordered public events emitted for one Session."""

from typing import Annotated, Literal

from pydantic import (
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    Tag,
    TypeAdapter,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import ErrorDetails

from ....session_values import EXPERIMENTAL
from .base import SessionModel
from .callbacks import CallbackOutcome, SessionCallback
from .common import (
    CallbackId,
    EncryptedValue,
    EventId,
    HistoryEntryId,
    ProtocolError,
    SessionId,
    TurnId,
    UnixMs,
)
from .content import UNSUPPORTED_CONTENT_ERROR
from .history import HistoryEntry
from .state import BackgroundProcess, Session, TurnState

type JsonPointer = str


class _JsonPatchValueOperation(SessionModel):
    # These operations require a value, and null is a valid one. Dumping with exclude_none would
    # strip it and emit an invalid operation, so a stripped value is restored as null.
    # Left unannotated on purpose: Pydantic derives the serialization schema from a return
    # annotation, and a dict annotation would replace each operation's tagged schema with an open
    # object in the generated Session API types.
    @model_serializer(mode="wrap")
    def _keep_null_value(self, handler: SerializerFunctionWrapHandler):
        payload = dict(handler(self))
        payload.setdefault("value", None)
        return payload


class JsonPatchAddOperation(_JsonPatchValueOperation):
    op: Literal["add"] = "add"
    path: JsonPointer
    value: JsonValue


class JsonPatchReplaceOperation(_JsonPatchValueOperation):
    op: Literal["replace"] = "replace"
    path: JsonPointer
    value: JsonValue


class JsonPatchTestOperation(_JsonPatchValueOperation):
    op: Literal["test"] = "test"
    path: JsonPointer
    value: JsonValue


class JsonPatchRemoveOperation(SessionModel):
    op: Literal["remove"] = "remove"
    path: JsonPointer


class JsonPatchMoveOperation(SessionModel):
    op: Literal["move"] = "move"
    path: JsonPointer
    from_: JsonPointer = Field(alias="from")


class JsonPatchCopyOperation(SessionModel):
    op: Literal["copy"] = "copy"
    path: JsonPointer
    from_: JsonPointer = Field(alias="from")


class JsonPatchAppendOperation(SessionModel):
    op: Literal["append"] = "append"
    path: JsonPointer
    value: str


type JsonPatchOperation = Annotated[
    JsonPatchAddOperation
    | JsonPatchReplaceOperation
    | JsonPatchTestOperation
    | JsonPatchRemoveOperation
    | JsonPatchMoveOperation
    | JsonPatchCopyOperation
    | JsonPatchAppendOperation,
    Field(discriminator="op"),
]

JsonPatchOperationAdapter: TypeAdapter[JsonPatchOperation] = TypeAdapter(JsonPatchOperation)


class HistoryEntryAddedEvent(SessionModel):
    type: Literal["history_entry_added"] = "history_entry_added"
    entry: HistoryEntry


class HistoryEntryPatchedEvent(SessionModel):
    type: Literal["history_entry_patched"] = "history_entry_patched"
    entry_id: HistoryEntryId
    entry_index: Annotated[int, Field(ge=0)]
    patches: list[JsonPatchOperation] = Field(default_factory=list)


class HistoryEntryPatchedEncryptedEvent(SessionModel):
    """The ``history_entry_patched`` event of an entry of an encrypted Session.

    The worker writes it while the entry streams. ``patches`` holds, sealed, the patch list that a
    ``history_entry_patched`` event would carry, computed on the entry before it was sealed. A
    client decrypts the list and applies it to the entry it decrypted from the
    ``EncryptedHistoryEntry``.

    This model is experimental: a minor release can change it without a compatibility path.
    """

    model_config = ConfigDict(json_schema_extra=EXPERIMENTAL)

    type: Literal["history_entry_patched_encrypted"] = "history_entry_patched_encrypted"
    entry_id: HistoryEntryId
    entry_index: Annotated[int, Field(ge=0)]
    patches: EncryptedValue


class HistoryEntryCompletedEvent(SessionModel):
    type: Literal["history_entry_completed"] = "history_entry_completed"
    entry: HistoryEntry


class ToolCallArgumentsAppendedEvent(SessionModel):
    """Arguments the model just wrote for a tool call whose entry is still generating.

    Live only: it is never stored, so a reader that joins or reads again later can miss some or
    all of a call's fragments. The completed `tool_call` entry carries the final arguments.
    """

    type: Literal["tool_call_arguments_appended"] = "tool_call_arguments_appended"
    entry_id: HistoryEntryId
    entry_index: Annotated[int, Field(ge=0)]
    json_fragment: str = Field(
        alias="json",
        description=(
            "The raw JSON text the model just wrote, appended to the call's arguments so far. "
            "Joined in order, a call's fragments may not parse until its entry completes."
        ),
    )


class TurnStartedEvent(SessionModel):
    type: Literal["turn_started"] = "turn_started"
    turn: TurnState


class TurnUpdatedEvent(SessionModel):
    type: Literal["turn_updated"] = "turn_updated"
    turn: TurnState


class TurnTerminatedEvent(SessionModel):
    type: Literal["turn_terminated"] = "turn_terminated"
    turn: TurnState


class SessionUpdatedEvent(SessionModel):
    type: Literal["session_updated"] = "session_updated"
    session: Session


class SessionTerminatedEvent(SessionModel):
    type: Literal["session_terminated"] = "session_terminated"
    session: Session


class BackgroundProcessesUpdatedEvent(SessionModel):
    """The Session's background processes changed: one started, changed status, or left the list.

    `processes` is the full list, as `SessionState.backgroundProcesses` holds it, and replaces
    the reader's copy. The Runtime emits it after it saves the change, so a read never returns
    an older list than the last event. Only a Host that reports background processes to
    clients emits it.
    """

    type: Literal["background_processes_updated"] = "background_processes_updated"
    processes: list[BackgroundProcess]


class CallbackOpenedEvent(SessionModel):
    type: Literal["callback_opened"] = "callback_opened"
    callback: SessionCallback


class CallbackResolvedEvent(SessionModel):
    type: Literal["callback_resolved"] = "callback_resolved"
    callback_id: CallbackId
    outcome: CallbackOutcome | None = Field(
        default=None,
        description=(
            "How the callback ended. Null only on resolutions recorded before this field "
            "existed, which carry the ID alone; every producer sets it now."
        ),
    )
    turn_id: TurnId | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "The turn that opened the callback, as its callback_opened event names it. Absent "
            "when the callback names no turn, and on resolutions recorded before this field "
            "existed."
        ),
    )


class PromptSuggestion(SessionModel):
    """A suggested next prompt for the user, offered after a turn ends.

    ``replyTo`` is the id of the user message history entry whose turn the suggestion
    follows. A client shows the suggestion only while that message is the latest user
    message and the session is waiting.
    """

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    reply_to: HistoryEntryId

    # A validator rather than Field(min_length=1): constraining the shared HistoryEntryId
    # alias would give it a second JSON schema shape.
    @field_validator("reply_to")
    @classmethod
    def _reply_to_not_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("reply_to must name a user message entry")
        return value


class PromptSuggestedEvent(SessionModel):
    """A prompt suggestion for the session. It is not part of the history."""

    type: Literal["prompt_suggested"] = "prompt_suggested"
    suggestion: PromptSuggestion


class ArtifactCreatedEvent(SessionModel):
    """An artifact the session created, such as a pull request. It is not part of the history."""

    type: Literal["artifact_created"] = "artifact_created"
    artifact: dict[str, JsonValue] = Field(
        description=(
            "The artifact, passed through unchanged. Consumers validate it against code-server's "
            "`artifactCreationPayloadSchema`."
        ),
    )


class NoticeEvent(SessionModel):
    type: Literal["notice"] = "notice"
    message: str
    details: JsonValue = None


class ErrorEvent(SessionModel):
    type: Literal["error"] = "error"
    error: ProtocolError


type SessionClientEvent = (
    SessionUpdatedEvent
    | SessionTerminatedEvent
    | HistoryEntryAddedEvent
    | HistoryEntryPatchedEvent
    | HistoryEntryPatchedEncryptedEvent
    | HistoryEntryCompletedEvent
    | ToolCallArgumentsAppendedEvent
    | TurnStartedEvent
    | TurnUpdatedEvent
    | TurnTerminatedEvent
    | BackgroundProcessesUpdatedEvent
    | PromptSuggestedEvent
    | ArtifactCreatedEvent
    | NoticeEvent
    | ErrorEvent
)

type SessionCallbackEvent = CallbackOpenedEvent | CallbackResolvedEvent

type SessionEvent = Annotated[
    SessionUpdatedEvent
    | SessionTerminatedEvent
    | HistoryEntryAddedEvent
    | HistoryEntryPatchedEvent
    | HistoryEntryPatchedEncryptedEvent
    | HistoryEntryCompletedEvent
    | ToolCallArgumentsAppendedEvent
    | TurnStartedEvent
    | TurnUpdatedEvent
    | TurnTerminatedEvent
    | BackgroundProcessesUpdatedEvent
    | PromptSuggestedEvent
    | ArtifactCreatedEvent
    | NoticeEvent
    | ErrorEvent
    | CallbackOpenedEvent
    | CallbackResolvedEvent,
    Field(discriminator="type"),
]

type EventPayload = SessionEvent

SessionEventAdapter: TypeAdapter[SessionEvent] = TypeAdapter(SessionEvent)
EventPayloadAdapter = SessionEventAdapter

_KNOWN_EVENT_PAYLOAD_TYPES = frozenset(
    {
        "history_entry_added",
        "history_entry_patched",
        "history_entry_patched_encrypted",
        "history_entry_completed",
        "tool_call_arguments_appended",
        "turn_started",
        "turn_updated",
        "turn_terminated",
        "background_processes_updated",
        "session_updated",
        "session_terminated",
        "callback_opened",
        "callback_resolved",
        "prompt_suggested",
        "artifact_created",
        "notice",
        "error",
    }
)


class Event(SessionModel):
    """One event in a Session-scoped order."""

    event_id: EventId
    emitted_at: UnixMs
    session_id: SessionId
    root_session_id: SessionId | None = None
    payload: EventPayload


class EventBatch(SessionModel):
    type: Literal["events"] = "events"
    events: list[Event] = Field(default_factory=list)


class UnknownEventPayload(SessionModel):
    """A payload of a type added after your SDK version, kept raw so it can be skipped.

    Serialized, it is the payload as it came, with its real type and no ``original_type``.
    """

    type: Literal["unknown"] = "unknown"
    original_type: str
    raw: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def wrap_wire_payload(cls, value: object) -> object:
        """Read a payload off the wire, where it carries its own type and fields.

        A payload that already has this model's shape, with an original type and the type
        ``unknown``, is read as it is. A wire payload whose own type is ``unknown`` is wrapped.
        """
        if not isinstance(value, dict):
            return value
        has_original_type = "original_type" in value or "originalType" in value
        if has_original_type and value.get("type", "unknown") == "unknown":
            return value
        return {"original_type": value.get("type"), "raw": value}

    @model_serializer
    def write_wire_payload(self) -> dict[str, JsonValue]:
        """The payload as it came on the wire, with its real type.

        ``unknown`` is only this reader's name for it. A stored or relayed copy keeps the real
        type, so that a reader that knows the type reads it.
        """
        return {
            "type": self.original_type,
            **{key: item for key, item in self.raw.items() if key != "type"},
        }


def _classify_client_payload(value: object) -> str:
    payload_type = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    # A missing or non-string type is malformed, not unknown: the known union rejects it.
    if isinstance(payload_type, str) and payload_type not in _KNOWN_EVENT_PAYLOAD_TYPES:
        return "unknown"
    return "known"


type ClientEventPayload = Annotated[
    Annotated[SessionEvent, Tag("known")] | Annotated[UnknownEventPayload, Tag("unknown")],
    Discriminator(_classify_client_payload),
]


class ClientEvent(Event):
    """Client-side event parser that preserves unknown payload variants.

    A payload of a type added after your SDK version, or with a value or a union variant
    added after it (a status, a kind, an operation), reads as `UnknownEventPayload`, so the
    reader can skip it. History entries and content blocks of a type added after your SDK
    version are the exception: they read as `UnknownHistoryEntry` and
    `UnknownContentBlock`, and the event keeps its type. A payload that is malformed, a
    wrong JSON type, a missing field or a missing tag, still fails.
    """

    payload: ClientEventPayload  # pyright: ignore[reportIncompatibleVariableOverride]

    @field_validator("payload", mode="wrap")
    @classmethod
    def downgrade_newer_payload(
        cls, value: object, handler: ValidatorFunctionWrapHandler
    ) -> object:
        try:
            return handler(value)
        except ValidationError as error:
            if not isinstance(value, dict) or not all(
                cls._is_newer_value(detail) for detail in error.errors()
            ):
                raise
            return UnknownEventPayload.model_validate(value)

    @staticmethod
    def _is_newer_value(error: ErrorDetails) -> bool:
        """Whether one error is a string from a closed set this reader does not know, or a
        content block a reader model refuses for older readers."""
        if error["type"] == UNSUPPORTED_CONTENT_ERROR:
            return True
        if error["type"] == "literal_error":
            return isinstance(error["input"], str)
        if error["type"] == "union_tag_invalid":
            container = error["input"]
            discriminator = str(error.get("ctx", {}).get("discriminator", "")).strip("'")
            return isinstance(container, dict) and isinstance(container.get(discriminator), str)
        return False


class ClientEventBatch(EventBatch):
    events: list[ClientEvent] = Field(  # pyright: ignore[reportIncompatibleVariableOverride]
        default_factory=list
    )


type TransportEndReason = Literal["server_closed", "client_closed", "error"]


class TransportStreamStarted(SessionModel):
    type: Literal["stream_started"] = "stream_started"


class TransportHeartbeat(SessionModel):
    type: Literal["heartbeat"] = "heartbeat"
    latest_event_id: EventId | None = None


class TransportStreamEnded(SessionModel):
    type: Literal["stream_ended"] = "stream_ended"
    reason: TransportEndReason
    latest_event_id: EventId | None = None


type EventStreamItem = Event | EventBatch
type ClientEventStreamItem = ClientEvent | ClientEventBatch
type TransportStreamItem = (
    Event | EventBatch | TransportStreamStarted | TransportHeartbeat | TransportStreamEnded
)
type ClientTransportStreamItem = (
    Event
    | EventBatch
    | ClientEvent
    | ClientEventBatch
    | TransportStreamStarted
    | TransportHeartbeat
    | TransportStreamEnded
)
