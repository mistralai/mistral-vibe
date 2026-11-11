"""Stateless procedures for the App Server Session Protocol."""

from collections.abc import Sequence
from typing import Annotated, Literal, Self, cast

from pydantic import Field, JsonValue, field_validator, model_validator

from ....session_values import REQUEST_CONTEXT
from ..models.base import SessionModel
from ..models.callbacks import CallbackResult, TreeCallback
from ..models.common import (
    DEFAULT_INTERRUPT_REASON,
    AbsolutePath,
    EventId,
    HistoryEntryId,
    OutputType,
    ProtocolError,
    SessionId,
    TurnId,
)
from ..models.configuration import AgentConfig
from ..models.content import ContentBlock, TextContentBlock, refuse_unsupported_blocks
from ..models.events import EventBatch
from ..models.history import HistoryEntry, HistoryImportEntry
from ..models.state import ListPageRequest, Page, PageRequest, Session, SessionState, TurnState

type Capability = dict[str, JsonValue]
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
type Procedure = Literal[
    "app_server/info",
    "app_server/subagents/list",
    "app_server/session/start",
    "app_server/session/fork",
    "app_server/session/compact",
    "app_server/session/history/clear",
    "app_server/session/read",
    "app_server/session/history/list",
    "app_server/session/turns/list",
    "app_server/session/message/send",
    "app_server/session/turn/start",
    "app_server/session/turn/steer",
    "app_server/session/turn/interrupt",
    "app_server/session/cancel",
    "app_server/session/config/read",
    "app_server/session/config/write",
    "app_server/session/plugin/info",
    "app_server/session/plugin/reload",
    "app_server/session/callback/result",
    "app_server/session/callbacks/tree",
    "app_server/session/events/read",
    "app_server/catalog/list",
    "app_server/catalog/rename",
    "app_server/catalog/archive",
    "app_server/catalog/delete",
    "app_server/branching/rewind",
    "app_server/branching/filesystem/rewind/read",
    "app_server/branching/filesystem/rewind/restoreFiles",
    "app_server/scheduling/loops/list",
    "app_server/scheduling/loops/create",
    "app_server/scheduling/loops/delete",
    "app_server/scheduling/loops/clear",
    "app_server/worker/read",
    "app_server/worker/wait",
    "app_server/worker/context/add",
    "app_server/worker/shellCommand",
    "app_server/worker/shellCommand/interrupt",
    "app_server/workspacePolicy/trust/read",
    "app_server/workspacePolicy/trust/decide",
    "app_server/workspacePolicy/trust/untrustedConfig",
    "app_server/workspacePolicy/prompt/prepare",
    "app_server/configuration/write",
    "app_server/configuration/schema/read",
    "app_server/configuration/fields/read",
    "app_server/configuration/proxy/read",
    "app_server/configuration/proxy/write",
    "app_server/configuration/reload",
    "app_server/configuration/thinking/write",
    "app_server/diagnostics/logs/read",
    "app_server/agentCatalog/switch",
    "app_server/agentCatalog/install",
    "app_server/connectorCatalog/auth/read",
    "app_server/connectorCatalog/refresh",
    "app_server/mcpManagement/read",
    "app_server/mcpManagement/refresh",
    "app_server/mcpManagement/toggle",
    "app_server/mcpManagement/add",
    "app_server/mcpManagement/logout",
    "app_server/mcpManagement/login",
    "app_server/codeReview/state",
    "app_server/codeReview/baseline",
    "app_server/codeReview/turnDiff",
    "app_server/codeReview/hunks",
    "app_server/codeReview/approve",
    "app_server/codeReview/revert",
    "app_server/vibeCodeWeb/projects/open",
    "app_server/vibeCodeWeb/projects/loadMore",
    "app_server/vibeCodeWeb/projects/create",
    "app_server/vibeCodeWeb/projects/select",
    "app_server/vibeCodeWeb/projects/unlink",
    "app_server/vibeCodeWeb/projects/cancel",
    "app_server/vibeCodeWeb/projects/recover",
    "app_server/account/read",
    "app_server/account/identity/read",
    "app_server/telemetry/record",
    "app_server/feedback/shouldShow",
    "app_server/feedback/record",
    "app_server/narration/summarize",
]

APP_SERVER_SESSION_IMPLEMENTED_PROCEDURES: tuple[Procedure, ...] = (
    "app_server/session/start",
    "app_server/session/fork",
    "app_server/session/history/clear",
    "app_server/session/read",
    "app_server/session/history/list",
    "app_server/session/turns/list",
    "app_server/session/message/send",
    "app_server/session/turn/start",
    "app_server/session/turn/steer",
    "app_server/session/turn/interrupt",
    "app_server/session/config/read",
    "app_server/session/config/write",
    "app_server/session/callback/result",
    "app_server/session/callbacks/tree",
    "app_server/session/events/read",
)

APP_SERVER_SESSION_PROTOCOL_PROCEDURES: tuple[Procedure, ...] = (
    *APP_SERVER_SESSION_IMPLEMENTED_PROCEDURES,
    "app_server/session/compact",
    "app_server/session/plugin/info",
    "app_server/session/plugin/reload",
    "app_server/session/cancel",
)

APP_SERVER_CAPABILITY_PROCEDURES: dict[str, tuple[Procedure, ...]] = {
    "app_server/subagents": ("app_server/subagents/list",),
    "app_server/catalog": (
        "app_server/catalog/list",
        "app_server/catalog/rename",
        "app_server/catalog/archive",
        "app_server/catalog/delete",
    ),
    "app_server/branching": ("app_server/branching/rewind",),
    "app_server/branching/filesystem": (
        "app_server/branching/filesystem/rewind/read",
        "app_server/branching/filesystem/rewind/restoreFiles",
    ),
    "app_server/scheduling": (
        "app_server/scheduling/loops/list",
        "app_server/scheduling/loops/create",
        "app_server/scheduling/loops/delete",
        "app_server/scheduling/loops/clear",
    ),
    "app_server/worker": (
        "app_server/worker/read",
        "app_server/worker/wait",
        "app_server/worker/context/add",
        "app_server/worker/shellCommand",
        "app_server/worker/shellCommand/interrupt",
    ),
    "app_server/workspacePolicy": (
        "app_server/workspacePolicy/trust/read",
        "app_server/workspacePolicy/trust/decide",
        "app_server/workspacePolicy/trust/untrustedConfig",
        "app_server/workspacePolicy/prompt/prepare",
    ),
    "app_server/configuration": (
        "app_server/configuration/write",
        "app_server/configuration/schema/read",
        "app_server/configuration/fields/read",
        "app_server/configuration/proxy/read",
        "app_server/configuration/proxy/write",
        "app_server/configuration/reload",
        "app_server/configuration/thinking/write",
    ),
    "app_server/diagnostics": ("app_server/diagnostics/logs/read",),
    "app_server/agentCatalog": (
        "app_server/agentCatalog/switch",
        "app_server/agentCatalog/install",
    ),
    "app_server/connectorCatalog": (
        "app_server/connectorCatalog/auth/read",
        "app_server/connectorCatalog/refresh",
    ),
    "app_server/mcpManagement": (
        "app_server/mcpManagement/read",
        "app_server/mcpManagement/refresh",
        "app_server/mcpManagement/toggle",
        "app_server/mcpManagement/add",
        "app_server/mcpManagement/logout",
        "app_server/mcpManagement/login",
    ),
    "app_server/codeReview": (
        "app_server/codeReview/state",
        "app_server/codeReview/baseline",
        "app_server/codeReview/turnDiff",
        "app_server/codeReview/hunks",
        "app_server/codeReview/approve",
        "app_server/codeReview/revert",
    ),
    "app_server/vibeCodeWeb": (
        "app_server/vibeCodeWeb/projects/open",
        "app_server/vibeCodeWeb/projects/loadMore",
        "app_server/vibeCodeWeb/projects/create",
        "app_server/vibeCodeWeb/projects/select",
        "app_server/vibeCodeWeb/projects/unlink",
        "app_server/vibeCodeWeb/projects/cancel",
        "app_server/vibeCodeWeb/projects/recover",
    ),
    "app_server/account": (
        "app_server/account/read",
        "app_server/account/identity/read",
    ),
    "app_server/telemetry": ("app_server/telemetry/record",),
    "app_server/feedback": (
        "app_server/feedback/shouldShow",
        "app_server/feedback/record",
    ),
    "app_server/narration": ("app_server/narration/summarize",),
}


def app_server_capability(procedures: Sequence[Procedure]) -> Capability:
    return {"version": 1, "procedures": cast(JsonValue, list(procedures))}


def app_server_capabilities() -> dict[str, Capability]:
    return {
        namespace: app_server_capability(procedures)
        for namespace, procedures in APP_SERVER_CAPABILITY_PROCEDURES.items()
    }


class RequestEnvelope[ParamsT: SessionModel](SessionModel):
    """Transport request whose ID only correlates its response."""

    id: str
    method: Procedure
    params: ParamsT


class ResponseEnvelope[ResultT: SessionModel](SessionModel):
    id: str
    result: ResultT | None = None
    error: ProtocolError | None = None


def validate_request[ModelT: SessionModel](model: type[ModelT], value: object) -> ModelT:
    """Validate client wire data with strict server-side extra-field handling."""

    return model.model_validate(
        value,
        by_alias=True,
        by_name=False,
        extra="forbid",
        context=REQUEST_CONTEXT,
    )


class ClientInfo(SessionModel):
    name: str
    version: str
    title: str | None = None


class ServerInfo(SessionModel):
    name: str
    version: str


class PluginComponent(SessionModel):
    kind: PluginComponentKind
    name: str
    source_path: AbsolutePath | None = None
    config: dict[str, JsonValue] = Field(default_factory=dict)


class PluginInfo(SessionModel):
    workdir: AbsolutePath | None = None
    components: list[PluginComponent] = Field(default_factory=list)
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class InfoParams(SessionModel):
    client_info: ClientInfo | None = None
    capabilities: dict[str, Capability] = Field(default_factory=dict)


class InfoResponse(SessionModel):
    server_info: ServerInfo
    capabilities: dict[str, Capability] = Field(default_factory=dict)


class ContextMessage(SessionModel):
    """Context for the model sent with a user message.

    The model gets it in a `<Context>` block at the start of the user message. Public
    history records it after the user message, as a `context` message of kind `input`,
    when the Session accepts the message.
    """

    content: Annotated[list[ContentBlock], Field(min_length=1)]

    @field_validator("content")
    @classmethod
    def _refuse_blank_text(cls, content: list[ContentBlock]) -> list[ContentBlock]:
        return _require_non_blank_text(content, "A context message")

    @field_validator("content")
    @classmethod
    def _refuse_unsupported_blocks(cls, content: list[ContentBlock]) -> list[ContentBlock]:
        return refuse_unsupported_blocks(content)


class UserMessageInput(SessionModel):
    """A user message sent to a Session."""

    message: Annotated[list[ContentBlock], Field(min_length=1)]
    context_messages: list[ContextMessage] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def _refuse_blank_text(cls, message: list[ContentBlock]) -> list[ContentBlock]:
        return _require_non_blank_text(message, "A user message")

    @field_validator("message")
    @classmethod
    def _refuse_unsupported_blocks(cls, message: list[ContentBlock]) -> list[ContentBlock]:
        return refuse_unsupported_blocks(message)


def _require_non_blank_text(content: list[ContentBlock], subject: str) -> list[ContentBlock]:
    if all(isinstance(block, TextContentBlock) and not block.text.strip() for block in content):
        raise ValueError(f"{subject} needs a content block that is not blank text")
    return content


class InitialTurn(UserMessageInput):
    """The message that starts the first Turn of a Session in `app_server/session/start`."""


class SessionStartParams(SessionModel):
    idempotency_key: str | None = None
    agent_config: AgentConfig
    history: list[HistoryImportEntry] = Field(default_factory=list)
    session_inputs: Annotated[dict[str, JsonValue], Field(default_factory=dict)] = Field(
        default_factory=dict,
        description=(
            "Caller-supplied Session inputs keyed by input ID, fixed for the "
            "Session lifetime and delivered only to declared consumers."
        ),
    )
    initial_turn: InitialTurn | None = Field(
        default=None,
        description=(
            "Start the first Turn with the Session. A start retried with the same "
            "`idempotency_key` returns the same Turn. Send the same message on a retry: "
            "a host may refuse a different one."
        ),
    )


class SessionStartResponse(SessionModel):
    state: SessionState
    last_event_id: EventId
    initial_turn: TurnState | None = Field(
        default=None,
        description="The first Turn, if the Session was started with `initial_turn`.",
    )


class SessionForkParams(SessionModel):
    idempotency_key: str | None = None
    source_session_id: SessionId
    agent_config: AgentConfig | None = None
    after_turn_id: TurnId | None = None


class SessionForkResponse(SessionModel):
    state: SessionState
    last_event_id: EventId


class SessionCompactParams(SessionModel):
    session_id: SessionId
    instructions: str | None = None


class SessionCompactResponse(SessionModel):
    state: SessionState
    last_event_id: EventId
    summary: str | None = None


class SessionHistoryClearParams(SessionModel):
    idempotency_key: str | None = None
    session_id: SessionId


class SessionHistoryClearResponse(SessionModel):
    source_session_id: SessionId
    state: SessionState
    last_event_id: EventId


class SessionReadParams(SessionModel):
    session_id: SessionId
    history: PageRequest | None = None
    turns: PageRequest | None = None


class SubagentsListParams(SessionModel):
    parent_session_id: SessionId
    page: ListPageRequest = Field(default_factory=ListPageRequest)


class SubagentSession(SessionModel):
    """A direct child link with an ordinary Session summary, listed newest first."""

    session: Session
    name: str
    config_name: str | None = None
    agent_path: str


class SubagentsListResponse(Page[SubagentSession]):
    pass


class RootsScope(SessionModel):
    type: Literal["roots"] = "roots"


class DirectChildrenScope(SessionModel):
    type: Literal["direct_children"] = "direct_children"
    parent_session_id: SessionId


class TreeScope(SessionModel):
    type: Literal["tree"] = "tree"
    root_session_id: SessionId


type SessionCatalogScope = Annotated[
    RootsScope | DirectChildrenScope | TreeScope, Field(discriminator="type")
]


class CatalogListParams(SessionModel):
    scope: SessionCatalogScope = Field(default_factory=RootsScope)
    include_archived: bool = False
    page: ListPageRequest = Field(default_factory=ListPageRequest)


class CatalogListResponse(Page[Session]):
    pass


class CatalogRenameParams(SessionModel):
    session_id: SessionId
    title: str | None = Field(max_length=255)


class CatalogRenameResponse(SessionModel):
    session: Session


class CatalogArchiveParams(SessionModel):
    session_id: SessionId
    archived: bool = True


class CatalogArchiveResponse(SessionModel):
    session: Session


class SessionReadResponse(SessionModel):
    state: SessionState
    last_event_id: EventId


class SessionHistoryListParams(SessionModel):
    session_id: SessionId
    turn_id: TurnId | None = None
    page: PageRequest = Field(default_factory=PageRequest)


class SessionHistoryListResponse(SessionModel):
    page: Page[HistoryEntry]


class SessionTurnsListParams(SessionModel):
    session_id: SessionId
    page: PageRequest = Field(default_factory=PageRequest)


class SessionTurnsListResponse(SessionModel):
    page: Page[TurnState]


class MessageSendInput(SessionModel):
    """What `message/send` sends: a user message, model-only context, or both.

    Context sent without a message starts no Turn and extends none: the running
    Turn's model reads it at its next call, or the next Turn's first call reads
    it when no Turn runs.
    """

    message: Annotated[list[ContentBlock], Field(min_length=1)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    context_messages: list[ContextMessage] = Field(default_factory=list)
    # Names the message, so only a send with one sets it.
    message_entry_id: HistoryEntryId | None = None

    @field_validator("message")
    @classmethod
    def _refuse_blank_text(cls, message: list[ContentBlock] | None) -> list[ContentBlock] | None:
        return None if message is None else _require_non_blank_text(message, "A user message")

    @field_validator("message")
    @classmethod
    def _refuse_unsupported_blocks(
        cls, message: list[ContentBlock] | None
    ) -> list[ContentBlock] | None:
        return None if message is None else refuse_unsupported_blocks(message)

    @model_validator(mode="after")
    def _require_input(self) -> Self:
        if self.message is None and not self.context_messages:
            raise ValueError("A message/send needs a message or a context message")
        if self.message is None and self.message_entry_id is not None:
            raise ValueError("A message/send without a message has no message entry ID")
        return self


class MessageSendParams(MessageSendInput):
    """Send one user message; the Session starts a Turn or steers the active one.

    It needs a `message`, `context_messages`, or both. Context messages alone
    start no Turn, and take no `message_entry_id`.

    Reusing `idempotency_key` with the same message, entry ID, and context
    messages returns the original receipt; reusing it with any of them changed
    is a conflict.
    """

    idempotency_key: str | None = None
    session_id: SessionId
    # The schema the answer of the Turn this message starts must match. A message that steers
    # the active Turn carries none or that Turn's own.
    output_type: OutputType | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _require_message_for_output_type(self) -> Self:
        if self.message is None and self.output_type is not None:
            raise ValueError("A message/send without a message has no output type")
        return self


class MessageSendResponse(SessionModel):
    """Receipt for an accepted send and the Turn its message joined.

    `steered` is true when the message joined the running Turn and false when it
    started that Turn. A host that does not report it leaves it out. A send of
    context only names no entry and no Turn, so it leaves all three out.
    """

    message_entry_id: HistoryEntryId | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    turn_id: TurnId | None = Field(default=None, exclude_if=lambda value: value is None)
    steered: bool | None = Field(default=None, exclude_if=lambda value: value is None)


class TurnStartParams(UserMessageInput):
    idempotency_key: str | None = None
    session_id: SessionId
    message_entry_id: HistoryEntryId | None = None
    # Every agent model call of this Turn must answer with JSON that follows this schema.
    output_type: OutputType | None = Field(default=None, exclude_if=lambda value: value is None)


class TurnStartResponse(SessionModel):
    turn: TurnState
    last_event_id: EventId


class TurnSteerParams(UserMessageInput):
    idempotency_key: str | None = None
    session_id: SessionId
    expected_turn_id: TurnId
    message_entry_id: HistoryEntryId | None = None


class TurnSteerResponse(SessionModel):
    accepted: Literal[True] = True
    last_event_id: EventId


class TurnInterruptParams(SessionModel):
    session_id: SessionId
    expected_turn_id: TurnId
    reason: str = DEFAULT_INTERRUPT_REASON


class TurnInterruptResponse(SessionModel):
    accepted: Literal[True] = True
    last_event_id: EventId


class SessionCancelParams(SessionModel):
    idempotency_key: str | None = None
    session_id: SessionId
    reason: str = "cancel requested"


class SessionCancelResponse(SessionModel):
    accepted: Literal[True] = True


class ConfigReadParams(SessionModel):
    session_id: SessionId


class ConfigReadResponse(SessionModel):
    config: AgentConfig


class ConfigWriteParams(SessionModel):
    session_id: SessionId
    config: AgentConfig


class ConfigWriteResponse(SessionModel):
    config: AgentConfig
    last_event_id: EventId


class PluginInfoParams(SessionModel):
    session_id: SessionId


class PluginInfoResponse(SessionModel):
    info: PluginInfo


class PluginReloadParams(SessionModel):
    session_id: SessionId


class PluginReloadResponse(SessionModel):
    info: PluginInfo
    last_event_id: EventId


class CallbackResultParams(SessionModel):
    session_id: SessionId
    result: CallbackResult


class CallbackResultResponse(SessionModel):
    accepted: Literal[True] = True
    last_event_id: EventId


class CallbacksTreeParams(SessionModel):
    """List the open callbacks of one Session and of all its descendants."""

    session_id: SessionId


class CallbacksTreeResponse(SessionModel):
    """Open callbacks of a Session tree.

    Ordered by owner (the addressed Session first, then descendants by agent path), then by
    the order each callback opened. A watch delivers the full list again on every change.
    """

    session_id: SessionId
    callbacks: list[TreeCallback]


class EventsReadParams(SessionModel):
    """Read one Session strictly after an opaque event ID, then follow its live tail."""

    session_id: SessionId
    after_event_id: EventId | None = None
    batch_size: Annotated[int, Field(ge=1)] = 100


class EventsReadResponse(SessionModel):
    batch: EventBatch
