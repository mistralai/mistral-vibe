from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time

from textual.widget import Widget

from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
from vibe.core.logger import logger
from vibe.core.loop import (
    USAGE_HINT,
    LoopErrorResult,
    LoopListResult,
    LoopManager,
    LoopOkResult,
    ScheduledLoop,
    format_duration,
)
from vibe.core.session.session_logger import SessionLogger


def _format_loop_list(loops: list[ScheduledLoop]) -> str:
    if not loops:
        return "No scheduled loops."
    now = time.time()
    rows = ["| Prompt | Next in | Every | ID |", "|--------|------|-------|----|"]
    for loop in loops:
        remaining = format_duration(max(0, int(loop.next_fire_at - now)), short=True)
        interval = format_duration(loop.interval_seconds)
        prompt = loop.prompt.replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {prompt} | {remaining} | {interval} | `{loop.id}` |")
    return "\n".join(rows)


class ScheduledLoopRunner:
    def __init__(
        self,
        session_logger: SessionLogger,
        *,
        can_fire: Callable[[], bool],
        fire: Callable[[str], Awaitable[None]],
        mount: Callable[[Widget], Awaitable[None]],
        tools_collapsed: Callable[[], bool],
    ) -> None:
        self._session_logger = session_logger
        self._manager = LoopManager(session_logger)
        self._can_fire = can_fire
        self._fire = fire
        self._mount = mount
        self._tools_collapsed = tools_collapsed
        self._task: asyncio.Task[None] | None = None

    def restore_from_session(self) -> None:
        metadata = self._session_logger.session_metadata
        self._manager.restore(list(metadata.loops) if metadata is not None else [])

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def handle_command(self, cmd_args: str) -> Widget:
        result = await self._manager.handle_command(cmd_args)
        match result:
            case LoopListResult(loops=loops):
                return UserCommandMessage(_format_loop_list(loops))
            case LoopErrorResult(message=message):
                return ErrorMessage(
                    f"{message}\n{USAGE_HINT}", collapsed=self._tools_collapsed()
                )
            case LoopOkResult(message=message):
                return UserCommandMessage(message)

    async def _poll(self) -> None:
        while True:
            try:
                sleep_for = min(self._manager.next_due_in(), 1.0)
                await asyncio.sleep(max(0.05, sleep_for))
                if not self._can_fire():
                    continue
                due = await self._manager.pop_due()
                if due is None:
                    continue
                await self._fire(due.prompt)
                await self._mount(UserCommandMessage(f"Loop `{due.id}` fired"))
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Error polling scheduled loops", exc_info=e)
