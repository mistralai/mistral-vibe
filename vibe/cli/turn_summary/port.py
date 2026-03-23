from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from pydantic import BaseModel, Field

from vibe.core.types import BaseEvent


class TurnSummaryData(BaseModel):
    user_message: str
    assistant_fragments: list[str] = Field(default_factory=list)
    error: str | None = None


class TurnSummaryResult(BaseModel):
    generation: int
    summary: str | None


class TurnSummaryPort(ABC):
    @property
    @abstractmethod
    def generation(self) -> int: ...

    @abstractmethod
    def start_turn(self, user_message: str) -> None: ...

    @abstractmethod
    def track(self, event: BaseEvent) -> None: ...

    @abstractmethod
    def set_error(self, message: str) -> None: ...

    @abstractmethod
    def cancel_turn(self) -> None: ...

    @abstractmethod
    def end_turn(self) -> Callable[[], bool] | None: ...

    @abstractmethod
    async def close(self) -> None: ...
