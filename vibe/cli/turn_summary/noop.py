from __future__ import annotations

from collections.abc import Callable

from vibe.cli.turn_summary.port import TurnSummaryPort, TurnSummaryResult
from vibe.core.types import BaseEvent


class NoopTurnSummary(TurnSummaryPort):
    @property
    def generation(self) -> int:
        return 0

    @property
    def on_summary(self) -> Callable[[TurnSummaryResult], None] | None:
        return None

    @on_summary.setter
    def on_summary(self, value: Callable[[TurnSummaryResult], None] | None) -> None:
        pass

    def start_turn(self, user_message: str) -> None:
        pass

    def track(self, event: BaseEvent) -> None:
        pass

    def set_error(self, message: str) -> None:
        pass

    def cancel_turn(self) -> None:
        pass

    def end_turn(self) -> Callable[[], bool] | None:
        return None

    async def close(self) -> None:
        pass
