from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.telemetry.types import AgentEntrypoint
from vibe.core.types import BaseEvent

TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH = 8_000


class TeleportMessageContextSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["teleport"] = "teleport"
    entrypoint: AgentEntrypoint = "unknown"
    client_name: str | None = Field(default=None, serialization_alias="clientName")


class TeleportMessageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH)
    source: TeleportMessageContextSource | None = None


class TeleportStartingWorkflowEvent(BaseEvent):
    pass


class TeleportCheckingGitEvent(BaseEvent):
    pass


class TeleportSummarizingContextEvent(BaseEvent):
    pass


class TeleportPushRequiredEvent(BaseEvent):
    unpushed_count: int = 1
    branch_not_pushed: bool = False


class TeleportPushResponseEvent(BaseEvent):
    approved: bool


class TeleportPushingEvent(BaseEvent):
    pass


class TeleportCompleteEvent(BaseEvent):
    url: str


type TeleportYieldEvent = (
    TeleportCheckingGitEvent
    | TeleportSummarizingContextEvent
    | TeleportPushRequiredEvent
    | TeleportPushingEvent
    | TeleportStartingWorkflowEvent
    | TeleportCompleteEvent
)

type TeleportSendEvent = TeleportPushResponseEvent | None
