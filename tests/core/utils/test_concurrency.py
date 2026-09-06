from __future__ import annotations

import asyncio

from vibe.core.utils.concurrency import run_sync


def test_run_sync_completes_without_running_loop() -> None:
    async def operation() -> str:
        return "completed"

    assert run_sync(operation()) == "completed"


def test_run_sync_completes_inside_running_loop() -> None:
    async def operation() -> str:
        return "completed"

    async def scenario() -> None:
        assert run_sync(operation()) == "completed"

    asyncio.run(scenario())


def test_run_sync_propagates_runtime_error_without_running_loop() -> None:
    original = RuntimeError("operation failed")

    async def operation() -> None:
        raise original

    try:
        run_sync(operation())
    except RuntimeError as caught:
        assert caught is original
    else:
        raise AssertionError("run_sync() did not propagate the operation error")


def test_run_sync_propagates_runtime_error_inside_running_loop() -> None:
    original = RuntimeError("operation failed")

    async def operation() -> None:
        raise original

    async def scenario() -> None:
        try:
            run_sync(operation())
        except RuntimeError as caught:
            assert caught is original
        else:
            raise AssertionError("run_sync() did not propagate the operation error")

    asyncio.run(scenario())


def test_run_sync_propagates_other_errors_inside_running_loop_once() -> None:
    original = ValueError("operation failed")
    executions = 0

    async def operation() -> None:
        nonlocal executions
        executions += 1
        raise original

    async def scenario() -> None:
        try:
            run_sync(operation())
        except ValueError as caught:
            assert caught is original
        else:
            raise AssertionError("run_sync() did not propagate the operation error")

    asyncio.run(scenario())
    assert executions == 1
