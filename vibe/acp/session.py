from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from vibe.acp.commands.registry import AcpCommandRegistry
from vibe.app_server.session import AppServerSession
from vibe.observability.logging import logger


class AcpSession:
    def __init__(
        self,
        *,
        session_id: str,
        app_server: AppServerSession,
        cwd: Path,
        commands: AcpCommandRegistry,
    ) -> None:
        self.id = session_id
        self.app_server = app_server
        self.cwd = cwd
        self.commands = commands
        self._accepting_tasks = True
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None] | None:
        if not self._accepting_tasks:
            coroutine.close()
            return None
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)
        return task

    async def cancel_prompt(self) -> None:
        if not self.app_server.turn_active:
            return
        await self.app_server.interrupt()

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._accepting_tasks = False
            current = asyncio.current_task()
            tasks = [task for task in self._tasks if task is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.clear()
            await self.app_server.close()
            self._closed = True

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        if error := task.exception():
            logger.error(
                "ACP background task failed session_id=%s error=%s",
                self.id,
                error,
                exc_info=error,
            )
