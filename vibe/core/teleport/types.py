from __future__ import annotations

from vibe.core.types import BaseEvent


class TeleportStartingWorkflowEvent(BaseEvent):
    pass


class TeleportCheckingGitEvent(BaseEvent):
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
    | TeleportPushRequiredEvent
    | TeleportPushingEvent
    | TeleportStartingWorkflowEvent
    | TeleportCompleteEvent
)

type TeleportSendEvent = TeleportPushResponseEvent | None
