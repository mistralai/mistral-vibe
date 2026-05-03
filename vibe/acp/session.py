from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from vibe.acp.commands import AcpCommandRegistry
from vibe.core.agent_loop import AgentLoop


class AcpSessionLoop:
    """Holds the state for a single ACP session.

    All session-scoped async work (background updates, the prompt task)
    is tracked internally.  ``close`` cancels everything;
    ``cancel_prompt`` cancels only the active prompt.
    """

    def __init__(
        self, *, id: str, agent_loop: AgentLoop, command_registry: AcpCommandRegistry
    ) -> None:
        self.id = id
        self.agent_loop = agent_loop
        self.command_registry = command_registry
        self._closed = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._prompt_task: asyncio.Task[None] | None = None

    # -- public API ------------------------------------------------------------

    @property
    def prompt_task(self) -> asyncio.Task[None] | None:
        return self._prompt_task

    def spawn(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None] | None:
        """Launch a background coroutine tied to this session."""
        if self._closed:
            coro.close()
            return None
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def set_prompt_task(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Create the prompt task. Only one may be active at a time."""
        task = asyncio.create_task(coro)
        self._prompt_task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda _: self._clear_prompt_task(task))
        return task

    async def cancel_prompt(self) -> None:
        """Cancel the active prompt task, if any."""
        task = self._prompt_task
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        """Cancel all tasks (prompt + background) and mark session closed."""
        self._closed = True
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._prompt_task = None

    # -- private ---------------------------------------------------------------

    def _clear_prompt_task(self, task: asyncio.Task[None]) -> None:
        if self._prompt_task is task:
            self._prompt_task = None
