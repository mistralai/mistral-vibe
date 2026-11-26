from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from vibe.core.utils.fetch_wait import FetchWait


def _wait(task: asyncio.Task[Any], cap: float) -> FetchWait:
    return FetchWait(task, name="Test fetch", cap_seconds=cap)


@pytest.mark.asyncio
async def test_the_wait_ends_with_the_fetch() -> None:
    released = asyncio.Event()
    wait = _wait(asyncio.create_task(released.wait()), 30)
    waiter = asyncio.create_task(wait.wait())
    await asyncio.sleep(0)
    assert not waiter.done()
    assert wait.done is False

    released.set()
    await waiter

    assert wait.done is True


@pytest.mark.asyncio
async def test_the_wait_ends_at_the_cap_and_leaves_the_fetch_running() -> None:
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = _wait(task, 0.01)

    await wait.wait()

    assert not task.done()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_later_wait_on_the_same_slow_fetch_gets_its_own_cap() -> None:
    # A fetch still running at one session's cap is joined by the next
    # session, which waits for it within its own cap.
    released = asyncio.Event()
    task = asyncio.create_task(released.wait())
    first = _wait(task, 0.01)
    await first.wait()

    second = _wait(task, 30)
    await asyncio.sleep(0.05)

    assert second.done is False
    released.set()
    await second.wait()


@pytest.mark.asyncio
async def test_a_cancelled_fetch_ends_the_wait() -> None:
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = _wait(task, 30)
    task.cancel()

    await wait.wait()

    assert task.cancelled()
