"""Portable agent input shared by Runtime commands and public history.

Core receives only sender path, kind, and text. Public history adds its own
position and ContentBlocks; those projection types do not enter recovery state.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel

from .session_values import (
    EXPERIMENTAL,
    EncryptedValue,
    ForwardCompatibleModel,
    ProtocolError,
    Termination,
)


class AgentMessageValue(ForwardCompatibleModel):
    """Readers ignore fields added by newer Runtimes; servers reject them per request."""

    model_config = ConfigDict(
        frozen=True,
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


class AgentMessageSource(AgentMessageValue):
    type: Literal["agent"] = "agent"
    session_id: UUID
    agent_path: str = Field(min_length=1)


class AgentMessageRecipient(AgentMessageValue):
    session_id: UUID
    agent_path: str = Field(min_length=1)


class AgentInputIdentity(AgentMessageValue):
    message_id: UUID
    source: AgentMessageSource
    recipient: AgentMessageRecipient


class AgentMessageMetadata(AgentInputIdentity):
    kind: Literal["task", "message"]


class AgentMessageInput(AgentMessageMetadata):
    text: str = Field(min_length=1)


class AgentResultInput(AgentInputIdentity):
    kind: Literal["result"] = "result"
    source_turn_id: str = Field(min_length=1)
    termination: Termination


class AgentSessionEndedInput(AgentInputIdentity):
    """The source child Session ended, sent by the host in the child's place.

    It names no Turn. The recipient fails whatever it records as running for that child,
    and stops sending it work. Public history shows the failed Turn as an ordinary result,
    so this input never enters it.
    """

    kind: Literal["session_ended"] = "session_ended"
    error: ProtocolError


class EncryptedAgentInput(AgentInputIdentity):
    """The input that an encrypted agent request gives the recipient's Runtime.

    The control plane writes it from an encrypted agent request. It copies the request's sealed
    value into ``encrypted`` unchanged, and names the kind of request in ``input_kind``. It resolves
    ``message_id``, ``source`` and ``recipient`` itself, so they stay in plain text. The recipient's
    worker decrypts the request, and builds the plain input from it and these ids before its Runtime
    admits it.

    This model is experimental: a minor release can change it without a compatibility path.
    """

    model_config = ConfigDict(json_schema_extra=EXPERIMENTAL)

    kind: Literal["encrypted"] = "encrypted"
    input_kind: Literal["task", "message", "result"]
    encrypted: EncryptedValue


type AgentInput = Annotated[AgentMessageInput | AgentResultInput, Field(discriminator="kind")]

type AgentCommandInput = Annotated[
    AgentMessageInput | AgentResultInput | AgentSessionEndedInput | EncryptedAgentInput,
    Field(discriminator="kind"),
]
"""Input a Runtime admits from another Session: public agent input, or a child's end."""
