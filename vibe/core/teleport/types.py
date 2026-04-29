from __future__ import annotations

from vibe.core.types import BaseEvent


class TeleportAuthRequiredEvent(BaseEvent):
    oauth_url: str
    message: str | None = None


class TeleportAuthCompleteEvent(BaseEvent):
    pass


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


class TeleportWaitingForGitHubEvent(BaseEvent):
    pass


class TeleportFetchingUrlEvent(BaseEvent):
    pass


class TeleportCompleteEvent(BaseEvent):
    url: str


type TeleportYieldEvent = (
    TeleportAuthRequiredEvent
    | TeleportAuthCompleteEvent
    | TeleportCheckingGitEvent
    | TeleportPushRequiredEvent
    | TeleportPushingEvent
    | TeleportStartingWorkflowEvent
    | TeleportWaitingForGitHubEvent
    | TeleportFetchingUrlEvent
    | TeleportCompleteEvent
)

type TeleportSendEvent = TeleportPushResponseEvent | None
