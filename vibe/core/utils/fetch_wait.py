"""Holding a session's start for a background fetch, within a cap.

A wait ends when its fetch's task ends or when its cap expires, counted from
when the wait is created. Once a fetch fails, or is still running when a cap
expires, the fetch's ``FetchFailures`` remembers it for the process, so no
later session waits for that fetch. A fetch cancelled with its session is not a
failure: the next session still waits for its own fetch.
"""

from __future__ import annotations

import asyncio
from typing import Any

from vibe.observability.logging import logger


class FetchFailures:
    def __init__(self) -> None:
        self._failed = False

    @property
    def failed(self) -> bool:
        return self._failed

    def mark(self) -> None:
        self._failed = True

    def reset(self) -> None:
        self._failed = False


class FetchWait:
    """A session waiting on a fetch: done once the fetch's task ends or the
    cap expires. The cap counts from creation; create it in the running loop.
    """

    def __init__(
        self,
        task: asyncio.Task[Any],
        *,
        name: str,
        cap_seconds: float,
        failures: FetchFailures,
    ) -> None:
        self._task = task
        self._name = name
        self._cap_seconds = cap_seconds
        self._failures = failures
        self._done = asyncio.Event()
        # Armed whether or not anything waits, so a fetch still running at
        # the cap is recorded even when every client skipped the wait.
        self._cap = asyncio.get_running_loop().call_later(cap_seconds, self._expire)
        task.add_done_callback(self._settle)

    @property
    def done(self) -> bool:
        return self._done.is_set()

    async def wait(self) -> None:
        await self._done.wait()

    def _settle(self, _task: asyncio.Task[Any]) -> None:
        self._cap.cancel()
        self._done.set()

    def _expire(self) -> None:
        if self._task.done():
            return
        if not self._failures.failed:
            logger.info(
                "%s not applied within %ss; continuing on local config",
                self._name,
                self._cap_seconds,
            )
        if self._task.done():
            # The fetch landed between the first check and the mark; a
            # success must not become a process-wide failure.
            return
        self._failures.mark()
        self._done.set()
