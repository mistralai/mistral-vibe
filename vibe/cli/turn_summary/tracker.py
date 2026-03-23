from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from vibe.cli.turn_summary.port import (
    TurnSummaryData,
    TurnSummaryPort,
    TurnSummaryResult,
)
from vibe.core.config import ModelConfig
from vibe.core.llm.types import BackendLike
from vibe.core.logger import logger
from vibe.core.prompts import UtilityPrompt
from vibe.core.types import AssistantEvent, BaseEvent, LLMMessage, Role


class TurnSummaryTracker(TurnSummaryPort):
    def __init__(
        self,
        backend: BackendLike,
        model: ModelConfig,
        on_summary: Callable[[TurnSummaryResult], None],
        max_tokens: int = 512,
    ) -> None:
        self._backend = backend
        self._model = model
        self._on_summary = on_summary
        self._max_tokens = max_tokens
        self._tasks: set[asyncio.Task[Any]] = set()
        self._data: TurnSummaryData | None = None
        self._generation: int = 0

    @property
    def generation(self) -> int:
        return self._generation

    def start_turn(self, user_message: str) -> None:
        self._generation += 1
        self._data = TurnSummaryData(user_message=user_message)

    def track(self, event: BaseEvent) -> None:
        if self._data is None:
            return
        match event:
            case AssistantEvent(content=c) if c:
                self._data.assistant_fragments.append(c)

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
            prompt_text = UtilityPrompt.TURN_SUMMARY.read()

            sections: list[str] = []
            sections.append(f"## User Request\n{data.user_message}")

            full_text = "".join(data.assistant_fragments)
            if full_text:
                sections.append(f"## Assistant Response\n{full_text}")

            if data.error:
                sections.append(f"## Error\n{data.error}")

            extraction_text = "\n\n".join(sections)

            summary_messages = [
                LLMMessage(role=Role.system, content=prompt_text),
                LLMMessage(role=Role.user, content=extraction_text),
            ]

            result = await self._backend.complete(
                model=self._model,
                messages=summary_messages,
                temperature=0.0,
                tools=None,
                tool_choice=None,
                max_tokens=self._max_tokens,
                extra_headers={},
                metadata={},
            )

            summary = result.message.content or ""
            self._on_summary(TurnSummaryResult(generation=gen, summary=summary))
        except Exception:
            logger.warning("Turn summary generation failed", exc_info=True)
            self._on_summary(TurnSummaryResult(generation=gen, summary=None))
