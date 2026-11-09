"""Transport-neutral Session values shared by commands and public projections."""

import builtins
import re
from dataclasses import dataclass
from typing import Annotated, Literal, Self, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    JsonValue,
    Tag,
    ValidationInfo,
    model_serializer,
    model_validator,
)
from pydantic.alias_generators import to_camel
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, PydanticOmit, core_schema

EMBEDDED_RESOURCE_URI_PREFIX = "vibe-agents://embedded-resources/"


REQUEST_CONTEXT = {"request": True}
"""Validation context of a request a server reads: an `UnknownValue` is refused there."""


@dataclass(frozen=True, slots=True)
class UnknownValue:
    """A value of a closed set, such as a status, added after your SDK version.

    A field a reader gets from a Runtime, such as a status or a kind, is typed
    ``Literal[...] | UnknownValue``. A value from the ``Literal`` reads as the plain string. A
    string added after your SDK version reads as an instance with that ``text``, so the model
    that holds it is still read. It is not a ``str``, so a type checker still flags a typo in a
    known value. Serialized, it is its text. A request refuses it, and it is left out of the
    JSON schema, so the published contract keeps the closed set.

    Compare with ``str(value) == "paused"``, never with an instance holding ``"paused"``: a later
    reader that knows ``paused`` reads it as the plain string.
    """

    text: str

    def __str__(self) -> str:
        return self.text

    @classmethod
    def _read(cls, value: object, info: ValidationInfo) -> Self:
        if isinstance(value, UnknownValue):
            text = value.text
        elif isinstance(value, str):
            text = value
        else:
            raise PydanticCustomError("unknown_value", "An unknown value must be a string")
        if isinstance(info.context, dict) and info.context.get("request"):
            raise PydanticCustomError(
                "unknown_value", "'{text}' is not a value this Runtime knows", {"text": text}
            )
        return cls(text)

    @staticmethod
    def _write(value: "UnknownValue") -> str:
        return value.text

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.with_info_plain_validator_function(
            cls._read,
            serialization=core_schema.plain_serializer_function_ser_schema(cls._write),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        raise PydanticOmit


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


type CompletedStopReason = Literal["iteration_limit", "skipped", "rejected", "output_limit"]


class CompletedTermination(SessionModel):
    outcome: Literal["completed"] = "completed"
    output: JsonValue = None
    stop_reason: CompletedStopReason | UnknownValue | None = None


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
    """A termination whose outcome was added after your SDK version.

    Such a termination is kept, so that it does not fail the Turn, Session or event that
    holds it. Its outcome is saved in ``original_outcome`` and the termination as it came in
    ``raw``. Its own ``outcome`` is ``unknown``, so a ``match`` on ``outcome`` still works
    for the known ones. Serialized, it is the termination as it came: its own ``outcome``,
    no ``original_outcome``. A writer never sends one.
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


class OutputType(SessionModel):
    """The JSON Schema one Turn's answer must match: an ask on that Turn, not a model setting.

    It names no provider: the Runtime asks its completion provider for an answer in this
    schema, in strict mode. Strict mode makes every object of the schema require all its
    properties and reject properties it does not list.
    """

    name: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    # Named `schema` on the wire. Set as validation and serialization aliases, not `alias`, so
    # Python code builds the model with the attribute name.
    json_schema: dict[str, JsonValue] = Field(
        validation_alias="schema", serialization_alias="schema"
    )

    @classmethod
    def from_model(cls, model: builtins.type[BaseModel]) -> Self:
        """Build the output type of a Pydantic model: its class name and its JSON Schema.

        Each character of the class name that `name` does not allow becomes `_`, and only the
        first 64 characters are kept, so a generic model such as `Page[City]` is named
        `Page_City_`.
        """

        name = _OUTPUT_TYPE_NAME_DISALLOWED.sub("_", model.__name__)[:64]
        return cls(name=name or "output", json_schema=model.model_json_schema())


_OUTPUT_TYPE_NAME_DISALLOWED = re.compile(r"[^A-Za-z0-9_-]")
