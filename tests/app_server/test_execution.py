from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from vibe.app_server._execution import cancel_tasks


@pytest.mark.asyncio
async def test_cancel_tasks_returns_after_bounded_timeout() -> None:
    blocked = asyncio.Event()

    async def ignore_first_cancellation() -> None:
        try:
            await blocked.wait()
        except asyncio.CancelledError:
            await blocked.wait()

    task = asyncio.create_task(ignore_first_cancellation())
    await asyncio.sleep(0)
    try:
        errors = await cancel_tasks([task], label="test", timeout=0.01)

        assert len(errors) == 1
        assert isinstance(errors[0], TimeoutError)
        assert "1 test task" in str(errors[0])
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
