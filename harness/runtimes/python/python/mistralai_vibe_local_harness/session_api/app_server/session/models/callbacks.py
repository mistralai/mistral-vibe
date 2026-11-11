"""Durable callback requests and results for the Session API."""

from typing import Annotated, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    Tag,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from .base import JsonSchema, SessionModel
from .common import (
    CallbackId,
    ProtocolError,
    SessionId,
    ToolCallId,
    ToolKind,
    TurnId,
    UnknownValue,
)
from .configuration import AgentConfig, ToolAnnotations
from .content import UNSUPPORTED_CONTENT_ERROR, ContentBlock, refuse_unsupported_blocks

type CallbackKind = Literal[
    "approval",
    "user_input",
    "auth",
    "client_tool",
    "client_hook",
    "execute_code",
    "start_subagent",
]


class BaseSessionCallback(SessionModel):
    id: CallbackId
    turn_id: TurnId | None = None
    # Owner of the callback, set only on the copy a callback provider hands to its handler.
    # Never serialized: the wire carries the owner next to the callback (``TreeCallback``).
    session_id: SkipJsonSchema[SessionId | None] = Field(default=None, exclude=True)
    """Session that opened the callback. Set on the callback a provider handler receives."""
    parent_session_id: SkipJsonSchema[SessionId | None] = Field(default=None, exclude=True)
    """Parent of the owner Session, None for a root. Set on the callback a provider handler
    receives."""
    agent_path: SkipJsonSchema[str | None] = Field(default=None, exclude=True)
    """Path of the owner in its agent tree, ``/root`` for the root. Set on the callback a provider
    handler receives, except for a child whose transport cannot watch the callback tree (such as a
    control plane without the tree routes): its path is then unknown and stays None."""


class ApprovalCallback(BaseSessionCallback):
    kind: Literal["approval"] = "approval"
    tool_call_id: ToolCallId
    tool_id: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Stable permission tool identity. Absent on legacy Hook approvals.",
    )
    name: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Model-visible tool name. Absent on legacy Hook approvals and on Hook approvals "
            "of builtin tools."
        ),
    )
    tool_kind: ToolKind | UnknownValue | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Where the tool comes from. Absent when the Harness does not know.",
    )
    tool_description: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="The tool description the model reads. Absent for built-in tools.",
    )
    input_schema: JsonSchema | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="JSON Schema of the tool input. Absent for built-in tools.",
    )
    output_schema: JsonSchema | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="JSON Schema of the tool output, when the tool declares one.",
    )
    tool_annotations: ToolAnnotations | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "The hints the tool declares, such as readOnlyHint. Absent when it declares none; "
            "an absent hint takes the MCP default."
        ),
    )
    tool_definition: dict[str, JsonValue] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "The whole tool as its MCP server or connector listed it, by MCP's field names, "
            "such as title, annotations, outputSchema and _meta. Absent for other tools."
        ),
    )
    tool_source: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="The MCP server or connector that lists the tool. Absent for other tools.",
    )
    hook: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Name of the pre-tool-call Hook that asked for this approval. "
            "Absent when the agent's permission policy asked."
        ),
    )
    title: str
    description: str = ""
    input: JsonValue = None


class UserInputCallback(BaseSessionCallback):
    kind: Literal["user_input"] = "user_input"
    message: list[ContentBlock] = Field(default_factory=list)
    response_schema: JsonSchema | None = None

    # For the rolling deploy only: a reader one release older cannot read such a callback, and
    # an Agents SDK of that release fails the whole callbacks list on it. Refused here, as
    # before UnknownContentBlock, the callback stays out of the Agent Controller's callbacks
    # list, and its event reads as unknown.
    # TODO: remove in the release after the Agent Controller with UnknownContentBlock is in
    # production.
    @field_validator("message")
    @classmethod
    def _refuse_unsupported_blocks(cls, message: list[ContentBlock]) -> list[ContentBlock]:
        try:
            # An older reader reads audio, so this guard keeps it.
            return refuse_unsupported_blocks(message, refuse_audio=False)
        except ValueError as error:
            raise PydanticCustomError(
                UNSUPPORTED_CONTENT_ERROR, "{reason}", {"reason": str(error)}
            ) from error


type AuthSource = Literal["connector", "mcp"]
type AuthMode = Literal["connect", "reconnect"]


class AuthRequest(SessionModel):
    """One source the user is asked to sign in to."""

    source: AuthSource | UnknownValue
    id: str = Field(description="Connector ID, or MCP source name.")
    name: str = Field(description="Display name. The ID when the opener does not know a name.")
    mode: AuthMode | UnknownValue = Field(
        default="connect",
        description="`reconnect` asks the user to sign in again to a source already connected.",
    )


class AuthCallback(BaseSessionCallback):
    """Ask the user to sign in to one or more sources.

    Answer it with an ``AuthResult`` as the callback result's ``output``.
    """

    kind: Literal["auth"] = "auth"
    tool_call_id: ToolCallId | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "The model's tool call that asked for the sign-in. Absent when the host opened "
            "the callback itself."
        ),
    )
    requests: Annotated[list[AuthRequest], Field(min_length=1)]
    reason: str = Field(
        default="",
        exclude_if=lambda value: not value,
        description="Why the agent needs the sign-in, when it says.",
    )


class AuthResult(SessionModel):
    """The answer to an ``AuthCallback``: the request IDs the user connected or denied.

    Name each request in one list. A Harness Session refuses an answer that leaves one out,
    and the callback stays open.
    """

    connected: list[str] = Field(default_factory=list)
    denied: list[str] = Field(default_factory=list)
    next_steps: str = Field(
        default="",
        exclude_if=lambda value: not value,
        description="Optional instruction for the model on how to continue.",
    )


class ClientToolCallback(BaseSessionCallback):
    kind: Literal["client_tool"] = "client_tool"
    tool_call_id: ToolCallId = Field(
        description="The tool call this callback executes, whose tool result holds the answer.",
    )
    name: str
    input: JsonValue = None
    injected_session_inputs: Annotated[dict[str, JsonValue], Field(default_factory=dict)] = Field(
        default_factory=dict,
        description=(
            "Session inputs selected for this invocation by the Harness, "
            "kept apart from the model-written input."
        ),
    )


class ClientHookCallback(BaseSessionCallback):
    kind: Literal["client_hook"] = "client_hook"
    name: str
    input: JsonValue = None
    injected_session_inputs: Annotated[dict[str, JsonValue], Field(default_factory=dict)] = Field(
        default_factory=dict,
        description=(
            "Session inputs selected for this invocation by the Harness, "
            "kept apart from the Harness-generated hook data in input."
        ),
    )


class ExecuteCodeCallback(BaseSessionCallback):
    kind: Literal["execute_code"] = "execute_code"
    code: str


class StartSubagentCallback(BaseSessionCallback):
    kind: Literal["start_subagent"] = "start_subagent"
    agent_config: AgentConfig


# TODO: a new callback kind breaks an older client that cannot answer it. Gate each new kind
# behind a flag the client sets at session creation, so an older client never gets a kind it
# cannot handle (user, 3 Nov). Linear: https://linear.app/mistral-ai/issue/AGE-2316/session-v1-an-older-client-never-gets-a-callback-kind-it-cannot-answer
type SessionCallback = Annotated[
    ApprovalCallback
    | UserInputCallback
    | AuthCallback
    | ClientToolCallback
    | ClientHookCallback
    | ExecuteCodeCallback
    | StartSubagentCallback,
    Field(discriminator="kind"),
]

SessionCallbackAdapter: TypeAdapter[SessionCallback] = TypeAdapter(SessionCallback)


class TreeCallback(SessionModel):
    """One open callback of a Session tree, with the Session that opened it."""

    session_id: SessionId
    parent_session_id: SessionId | None = None
    agent_path: str
    callback: SessionCallback


class CallbackResult(SessionModel):
    """Answer a durable callback by its stable identity."""

    callback_id: CallbackId
    output: JsonValue = None
    annotations: dict[str, JsonValue] = Field(default_factory=dict)
    error: ProtocolError | None = None
    content: list[ContentBlock] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Client tool answers only: the content blocks the model sees. Without it the "
            "model sees `output` as text."
        ),
    )
    is_error: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
        description=(
            "Client tool answers only: a tool execution error (MCP `isError`). The model sees "
            "the content and can retry; `error` instead reports that the call did not run."
        ),
    )
    meta: dict[str, JsonValue] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Client tool answers only: host metadata, MCP `_meta`. Never shown to the model."
        ),
    )

    @field_validator("content")
    @classmethod
    def _refuse_unsupported_blocks(
        cls, content: list[ContentBlock] | None
    ) -> list[ContentBlock] | None:
        if content is None:
            return None
        # Audio fails the call when the answer reaches the Turn, as it did before, so an
        # accepted answer in a Workflow history still reads on replay.
        return refuse_unsupported_blocks(content, refuse_audio=False)


type ApprovalScope = Literal["once", "session"]


# Reject unknown fields so a misspelled scope cannot silently become "once".
class _ApprovalAnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AllowApproval(_ApprovalAnswerModel):
    """Allow this call once, or matching calls for the rest of the session.

    A matching call uses the same tool with the same arguments.
    """

    decision: Literal["allow"] = "allow"
    """Always ``"allow"``."""
    scope: ApprovalScope = "once"
    """``"once"`` for this call. ``"session"`` also allows the same call with the same input for
    the rest of the session.
    """


class DenyApproval(_ApprovalAnswerModel):
    """Deny this call."""

    decision: Literal["deny"] = "deny"
    """Always ``"deny"``."""


type ApprovalAnswer = AllowApproval | DenyApproval


# Frozen because producers hold these as shared constants, so one instance reaches many
# published events.
class AnsweredCallbackOutcome(SessionModel):
    model_config = ConfigDict(frozen=True)

    outcome: Literal["answered"] = "answered"
    output: JsonValue = None


class CancelledCallbackOutcome(SessionModel):
    model_config = ConfigDict(frozen=True)

    outcome: Literal["cancelled"] = "cancelled"
    reason: str | None = None


class FailedCallbackOutcome(SessionModel):
    model_config = ConfigDict(frozen=True)

    outcome: Literal["failed"] = "failed"
    error: ProtocolError


class UnknownCallbackOutcome(SessionModel):
    """A callback outcome added after your SDK version.

    Such an outcome is kept, so that it does not fail the event that holds it. Its outcome
    is saved in ``original_outcome`` and the outcome as it came in ``raw``. Its own
    ``outcome`` is ``unknown``, so a ``match`` on ``outcome`` still works for the known
    ones. Serialized, it is the outcome as it came: its own ``outcome``, no
    ``original_outcome``. A writer never sends one.
    """

    model_config = ConfigDict(frozen=True)

    outcome: Literal["unknown"] = "unknown"
    original_outcome: str
    raw: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def wrap_wire_outcome(cls, value: object) -> object:
        """Read an outcome off the wire, where it carries its own outcome and fields.

        An outcome that already has this model's shape, with an original outcome and the
        outcome ``unknown``, is read as it is.
        """
        if not isinstance(value, dict):
            return value
        has_original = "original_outcome" in value or "originalOutcome" in value
        if has_original and value.get("outcome", "unknown") == "unknown":
            return value
        return {"original_outcome": value.get("outcome"), "raw": value}

    @model_serializer
    def write_wire_outcome(self) -> dict[str, JsonValue]:
        """The outcome as it came on the wire, with its real outcome."""
        return {
            "outcome": self.original_outcome,
            **{key: item for key, item in self.raw.items() if key != "outcome"},
        }


def _callback_outcome_variant(value: object) -> str | None:
    outcome = value.get("outcome") if isinstance(value, dict) else getattr(value, "outcome", None)
    # A missing or non-string outcome is malformed, not unknown: no variant accepts it.
    if not isinstance(outcome, str):
        return None
    return outcome if outcome in _CALLBACK_OUTCOMES else "unknown"


type CallbackOutcome = Annotated[
    Annotated[AnsweredCallbackOutcome, Tag("answered")]
    | Annotated[CancelledCallbackOutcome, Tag("cancelled")]
    | Annotated[FailedCallbackOutcome, Tag("failed")]
    | Annotated[UnknownCallbackOutcome, Tag("unknown")],
    Discriminator(_callback_outcome_variant),
]

# The tags of the union's variants, so a new outcome is known once it joins the union.
_CALLBACK_OUTCOMES: frozenset[str] = frozenset(
    metadata.tag
    for variant in get_args(get_args(CallbackOutcome.__value__)[0])
    for metadata in variant.__metadata__
    if isinstance(metadata, Tag) and metadata.tag != "unknown"
)
