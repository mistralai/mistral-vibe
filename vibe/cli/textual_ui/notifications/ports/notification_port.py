from __future__ import annotations

from enum import StrEnum, auto
from typing import Protocol


class NotificationContext(StrEnum):
    ACTION_REQUIRED = auto()
    COMPLETE = auto()


class NotificationPort(Protocol):
    def notify(self, context: NotificationContext) -> None: ...
    def on_focus(self) -> None: ...
    def on_blur(self) -> None: ...
    def restore(self) -> None: ...
