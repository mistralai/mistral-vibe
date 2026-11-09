from __future__ import annotations

from contextvars import ContextVar

import pytest

from vibe.core.utils.concurrency import run_sync

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
