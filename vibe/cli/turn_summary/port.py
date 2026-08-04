from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, Field


class TurnSummaryData(BaseModel):
    user_message: str
    message_id: str | None = None
    assistant_fragments: list[str] = Field(default_factory=list)
    error: str | None = None


class TurnSummaryResult(BaseModel):
    generation: int
    summary: str | None


class TurnSummaryGenerator(Protocol):
    async def summarize(
        self,
        *,
        user_message: str,
        assistant_text: str,
        error: str | None,
        message_id: str | None,
    ) -> str | None: ...


class TurnSummaryPort(ABC):
    @property
    @abstractmethod
    def generation(self) -> int: ...

    @property
    @abstractmethod
    def on_summary(self) -> Callable[[TurnSummaryResult], None] | None: ...

    @on_summary.setter
    @abstractmethod
    def on_summary(self, value: Callable[[TurnSummaryResult], None] | None) -> None: ...

    @abstractmethod
    def start_turn(self, user_message: str) -> None: ...

    @abstractmethod
    def track_user_message(self, message_id: str) -> None: ...

    @abstractmethod
    def track_assistant_text(self, content: str) -> None: ...

    @abstractmethod
    def set_error(self, message: str) -> None: ...

    @abstractmethod
    def cancel_turn(self) -> None: ...

    @abstractmethod
    def end_turn(self) -> Callable[[], bool] | None: ...

    @abstractmethod
    async def close(self) -> None: ...
