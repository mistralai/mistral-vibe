from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from vibe.core.utils.fetch_wait import FetchFailures, FetchWait


def _wait(task: asyncio.Task[Any], failures: FetchFailures, cap: float) -> FetchWait:
    return FetchWait(task, name="Test fetch", cap_seconds=cap, failures=failures)


@pytest.mark.asyncio
async def test_the_wait_ends_with_the_fetch() -> None:
    failures = FetchFailures()
    released = asyncio.Event()
    wait = _wait(asyncio.create_task(released.wait()), failures, 30)
    waiter = asyncio.create_task(wait.wait())
    await asyncio.sleep(0)
    assert not waiter.done()
    assert wait.done is False

    released.set()
    await waiter

    assert wait.done is True
    assert failures.failed is False


@pytest.mark.asyncio
async def test_a_fetch_slower_than_the_cap_is_recorded_as_failed() -> None:
    failures = FetchFailures()
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = _wait(task, failures, 0.01)

    await wait.wait()

    assert not task.done()
    assert failures.failed is True
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_the_cap_is_recorded_even_with_nobody_waiting() -> None:
    failures = FetchFailures()
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = _wait(task, failures, 0.01)

    await asyncio.sleep(0.05)

    assert wait.done is True
    assert failures.failed is True
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_cancelled_fetch_is_not_a_failure() -> None:
    failures = FetchFailures()
    task = asyncio.create_task(asyncio.sleep(3600))
    wait = _wait(task, failures, 30)
    task.cancel()

    await wait.wait()

    assert task.cancelled()
    assert failures.failed is False


@pytest.mark.asyncio
async def test_each_held_fetch_gets_its_own_cap() -> None:
    # A session closed during its fetch leaves nothing behind: a later held
    # fetch still waits for its own result.
    failures = FetchFailures()
    first = asyncio.create_task(asyncio.sleep(3600))
    _wait(first, failures, 0.2)
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    await asyncio.sleep(0.3)
    released = asyncio.Event()
    wait = _wait(asyncio.create_task(released.wait()), failures, 0.2)

    await asyncio.sleep(0.05)

    assert wait.done is False
    assert failures.failed is False
    released.set()
    await wait.wait()
