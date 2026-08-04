from __future__ import annotations

import asyncio
from collections.abc import Callable

from vibe.cli.turn_summary.port import (
    TurnSummaryData,
    TurnSummaryGenerator,
    TurnSummaryPort,
    TurnSummaryResult,
)
from vibe.observability.logging import logger


class TurnSummaryTracker(TurnSummaryPort):
    def __init__(
        self,
        generator: TurnSummaryGenerator,
        on_summary: Callable[[TurnSummaryResult], None] | None = None,
    ) -> None:
        self._generator = generator
        self._on_summary = on_summary
        self._tasks: set[asyncio.Task[None]] = set()
        self._data: TurnSummaryData | None = None
        self._generation: int = 0

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def on_summary(self) -> Callable[[TurnSummaryResult], None] | None:
        return self._on_summary

    @on_summary.setter
    def on_summary(self, value: Callable[[TurnSummaryResult], None] | None) -> None:
        self._on_summary = value

    def start_turn(self, user_message: str) -> None:
        self._generation += 1
        self._data = TurnSummaryData(user_message=user_message)

    def track_user_message(self, message_id: str) -> None:
        if self._data is None:
            return
        self._data.message_id = message_id

    def track_assistant_text(self, content: str) -> None:
        if self._data is not None and content:
            self._data.assistant_fragments.append(content)

    def set_error(self, message: str) -> None:
        if self._data is not None:
            self._data.error = message

    def cancel_turn(self) -> None:
        self._data = None

    def end_turn(self) -> Callable[[], bool] | None:
        if self._data is None:
            return None
        gen = self._generation
        task = asyncio.create_task(self._generate_summary(self._data, gen))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._data = None
        return task.cancel

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _generate_summary(self, data: TurnSummaryData, gen: int) -> None:
        try:
            summary = await self._generator.summarize(
                user_message=data.user_message,
                assistant_text="".join(data.assistant_fragments),
                error=data.error,
                message_id=data.message_id,
            )
            if self._on_summary is not None:
                self._on_summary(TurnSummaryResult(generation=gen, summary=summary))
        except Exception as exc:
            logger.warning("Turn summary request failed", exc_info=exc)
            if self._on_summary is not None:
                self._on_summary(TurnSummaryResult(generation=gen, summary=None))
