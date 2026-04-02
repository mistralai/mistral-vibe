from __future__ import annotations

from vibe.core.types import BaseEvent


class TeleportAuthRequiredEvent(BaseEvent):
    user_code: str
    verification_uri: str


class TeleportAuthCompleteEvent(BaseEvent):
    pass


class TeleportStartingWorkflowEvent(BaseEvent):
    pass


class TeleportCheckingGitEvent(BaseEvent):
    pass


class TeleportPushRequiredEvent(BaseEvent):
    unpushed_count: int = 1


class TeleportPushResponseEvent(BaseEvent):
    approved: bool


class TeleportPushingEvent(BaseEvent):
    pass


class TeleportSendingGithubTokenEvent(BaseEvent):
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
    | TeleportSendingGithubTokenEvent
    | TeleportCompleteEvent
)

type TeleportSendEvent = TeleportPushResponseEvent | None
