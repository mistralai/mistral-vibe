from __future__ import annotations

from enum import StrEnum, auto
from typing import Protocol


class NarratorState(StrEnum):
    IDLE = auto()
    SUMMARIZING = auto()
    SPEAKING = auto()


class NarratorManagerListener:
    def on_narrator_state_change(self, state: NarratorState) -> None:
        pass


class NarratorManagerPort(Protocol):
    @property
    def state(self) -> NarratorState: ...

    @property
    def is_playing(self) -> bool: ...

    def on_turn_start(self, user_message: str) -> None: ...

    def on_user_message(self, message_id: str) -> None: ...

    def on_assistant_text(self, content: str) -> None: ...

    def on_turn_error(self, message: str) -> None: ...

    def on_turn_cancel(self) -> None: ...

    def on_turn_end(self) -> None: ...

    def cancel(self) -> None: ...

    def sync(self) -> None: ...

    def add_listener(self, listener: NarratorManagerListener) -> None: ...

    def remove_listener(self, listener: NarratorManagerListener) -> None: ...

    async def close(self) -> None: ...
