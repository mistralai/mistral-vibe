from __future__ import annotations

import asyncio
from contextvars import ContextVar

import pytest

from vibe.core.utils.concurrency import cancel_pending, run_sync

_MARK: ContextVar[str] = ContextVar("mark", default="unset")


async def _read_mark() -> str:
    return _MARK.get()


@pytest.mark.asyncio
async def test_run_sync_inside_a_running_loop_keeps_the_callers_context() -> None:
    """*Prepare*: A context variable set in a running event loop.
    *Do*: Run a coroutine that reads it through run_sync.
    *Assert*: The coroutine sees the caller's value, though it runs on another
    thread's loop.
    """
    # Prepare
    token = _MARK.set("caller")

    # Do
    try:
        seen = run_sync(_read_mark())
    finally:
        _MARK.reset(token)

    # Assert
    assert seen == "caller"


@pytest.mark.asyncio
async def test_cancel_pending_cancels_a_running_task_and_hands_it_back() -> None:
    running = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)

    returned = cancel_pending(running)

    assert returned is running
    with pytest.raises(asyncio.CancelledError):
        await running


@pytest.mark.asyncio
async def test_cancel_pending_leaves_a_finished_task_and_no_task_alone() -> None:
    finished = asyncio.create_task(asyncio.sleep(0, result="done"))
    await finished

    returned = (cancel_pending(finished), cancel_pending(None))

    assert returned == (None, None)
    assert finished.result() == "done"
