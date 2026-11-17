from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from vibe.app_server._execution import cancel_tasks, raise_if_cancelled


def test_a_cancellation_nested_among_shutdown_errors_is_raised_and_the_rest_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cancelled = asyncio.CancelledError()
    errors = [
        TimeoutError("timed out"),
        BaseExceptionGroup("close", [ValueError("broken"), cancelled]),
    ]

    with caplog.at_level("WARNING", logger="vibe"):
        with pytest.raises(asyncio.CancelledError) as raised:
            raise_if_cancelled(errors)

    assert raised.value is cancelled
    logged = [str(record.exc_info[1]) for record in caplog.records if record.exc_info]
    assert logged == ["timed out", "close (1 sub-exception)"]


def test_shutdown_errors_without_a_cancellation_are_left_to_the_caller() -> None:
    raise_if_cancelled([ValueError("broken")])


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


async def _shutdown_collecting(*extra: BaseException) -> None:
    errors: list[BaseException] = list(extra)
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError as exc:
        errors.append(exc)
    raise BaseExceptionGroup("Failed to shut down session adapters", errors)


@pytest.mark.asyncio
async def test_cancel_tasks_ignores_cancellation_wrapped_in_exception_group() -> None:
    task = asyncio.create_task(_shutdown_collecting())
    await asyncio.sleep(0)

    errors = await cancel_tasks([task], label="test")

    assert errors == []


@pytest.mark.asyncio
async def test_cancel_tasks_keeps_real_errors_next_to_wrapped_cancellation() -> None:
    failure = RuntimeError("adapter failed")
    task = asyncio.create_task(_shutdown_collecting(failure))
    await asyncio.sleep(0)

    errors = await cancel_tasks([task], label="test")

    assert len(errors) == 1
    assert isinstance(errors[0], BaseExceptionGroup)
    assert errors[0].exceptions == (failure,)
