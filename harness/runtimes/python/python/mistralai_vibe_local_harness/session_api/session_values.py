"""Transport-neutral Session values shared by commands and public projections."""

from typing import Annotated, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    Tag,
    model_serializer,
    model_validator,
)
from pydantic.alias_generators import to_camel

EMBEDDED_RESOURCE_URI_PREFIX = "vibe-agents://embedded-resources/"


class ForwardCompatibleModel(BaseModel):
    """Base for every model a client reads: a field it does not know is ignored.

    A newer server may add fields, and a client built on this base keeps reading the
    response or event. Subclasses must not set ``extra="forbid"``; a test checks every
    Session model. A server checks requests strictly at its boundary instead, through
    ``validate_request``, so one model serves both sides.
    """

    model_config = ConfigDict(extra="ignore")


class SessionModel(ForwardCompatibleModel):
    """Camel-case Session wire model with forward-compatible client parsing."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class ProtocolError(SessionModel):
    """Serializable error exposed through the Session boundary."""

    message: str
    code: str | None = None
    # Whether sending the same request again may succeed, for example after a model stream
    # timeout. A server that predates the flag omits it, which reads as not retryable.
    retryable: bool = False
    details: JsonValue = None


type CompletedStopReason = Literal["iteration_limit", "skipped", "rejected"]


class CompletedTermination(SessionModel):
    outcome: Literal["completed"] = "completed"
    output: JsonValue = None
    stop_reason: CompletedStopReason | None = None


# Open: a newer server may name a cause this client does not know yet.
type CancelledCause = Literal["lifetime_limit", "idle_limit"] | str


class CancelledTermination(SessionModel):
    outcome: Literal["cancelled"] = "cancelled"
    reason: str | None = None
    # Why the work ended, when more than an interrupt ended it: `cancelled` when the Session was
    # cancelled, `lifetime_limit` when it reached its maximum lifetime, `idle_limit` when it stayed
    # idle too long.
    cause: CancelledCause | None = None
    output: JsonValue = Field(default=None, exclude_if=lambda value: value is None)


class FailedTermination(SessionModel):
    outcome: Literal["failed"] = "failed"
    error: ProtocolError


class UnknownTermination(SessionModel):
    """A termination whose outcome this reader does not know.

    A newer Runtime can add outcomes. Such a termination is kept, so that it does not fail
    the Turn, Session or event that holds it. Its outcome is saved in ``original_outcome``
    and the termination as it came in ``raw``. Its own ``outcome`` is ``unknown``, so a
    ``match`` on ``outcome`` still works for the known ones. Serialized, it is the
    termination as it came: its own ``outcome``, no ``original_outcome``. A writer never
    sends one.
    """

    outcome: Literal["unknown"] = "unknown"
    original_outcome: str
    raw: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def wrap_wire_termination(cls, value: object) -> object:
        """Read a termination off the wire, where it carries its own outcome and fields.

        A termination that already has this model's shape, with an original outcome and the
        outcome ``unknown``, is read as it is.
        """
        if not isinstance(value, dict):
            return value
        has_original = "original_outcome" in value or "originalOutcome" in value
        if has_original and value.get("outcome", "unknown") == "unknown":
            return value
        return {"original_outcome": value.get("outcome"), "raw": value}

    @model_serializer
    def write_wire_termination(self) -> dict[str, JsonValue]:
        """The termination as it came on the wire, with its real outcome."""
        return {
            "outcome": self.original_outcome,
            **{key: item for key, item in self.raw.items() if key != "outcome"},
        }


def _termination_variant(value: object) -> str | None:
    outcome = value.get("outcome") if isinstance(value, dict) else getattr(value, "outcome", None)
    # A missing or non-string outcome is malformed, not unknown: no variant accepts it.
    if not isinstance(outcome, str):
        return None
    return outcome if outcome in _TERMINATION_OUTCOMES else "unknown"


type Termination = Annotated[
    Annotated[CompletedTermination, Tag("completed")]
    | Annotated[CancelledTermination, Tag("cancelled")]
    | Annotated[FailedTermination, Tag("failed")]
    | Annotated[UnknownTermination, Tag("unknown")],
    Discriminator(_termination_variant),
]

# The tags of the union's variants, so a new outcome is known once it joins the union.
_TERMINATION_OUTCOMES: frozenset[str] = frozenset(
    metadata.tag
    for variant in get_args(get_args(Termination.__value__)[0])
    for metadata in variant.__metadata__
    if isinstance(metadata, Tag) and metadata.tag != "unknown"
)


# The largest Turn count a forked child may ask for, the u32 limit of Core's schema.
MAX_FORKED_TURNS = 4_294_967_295


class ForkedContext(SessionModel):
    """A child that starts from a copy of its parent's conversation, then its task."""

    mode: Literal["forked"]
    turns: Annotated[int, Field(strict=True, ge=1, le=MAX_FORKED_TURNS)] | Literal["all"] = Field(
        default="all",
        description=(
            "How many of the parent's latest whole Turns to copy, the current one included: "
            "a positive integer, or `all` (the default), which also lets the child reuse "
            "the parent's cached prompt prefix."
        ),
    )


type SubagentContext = Literal["fresh"] | ForkedContext
"""What a spawned child starts from: `fresh`, a new conversation with only its task, or a
copy of its parent's conversation."""
