"""Holding a session's start for a background fetch, within a cap.

A wait ends when its fetch's task ends or when its cap expires, counted from
when the wait is created. It keeps no memory of past fetches. After a
failure, a session that opens within the ``RemoteCache`` retry delay finds its
fetch already over and does not wait; one that opens later waits for the
retry. A fetch still running at one session's cap is waited for again by the
next session, within its own cap.
"""

from __future__ import annotations

import asyncio
from typing import Any

from vibe.observability.logging import logger


class FetchWait:
    """A session waiting on a fetch: done once the fetch's task (or any future
    standing for it) ends or the cap expires. The cap counts from creation;
    create it in the running loop.
    """

    def __init__(
        self, task: asyncio.Future[Any], *, name: str, cap_seconds: float
    ) -> None:
        self._task = task
        self._name = name
        self._cap_seconds = cap_seconds
        self._done = asyncio.Event()
        self._cap = asyncio.get_running_loop().call_later(cap_seconds, self._expire)
        task.add_done_callback(self._settle)

    @property
    def done(self) -> bool:
        return self._done.is_set()

    async def wait(self) -> None:
        await self._done.wait()

    def _settle(self, _task: asyncio.Future[Any]) -> None:
        self._cap.cancel()
        self._done.set()

    def _expire(self) -> None:
        if self._task.done():
            return
        logger.info(
            "%s not ready within %ss; continuing without it",
            self._name,
            self._cap_seconds,
        )
        self._done.set()
