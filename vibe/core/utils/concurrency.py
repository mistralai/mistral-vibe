from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import concurrent.futures
import functools
from typing import Any


class ConversationLimitException(Exception):
    pass


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine synchronously, handling nested event loops.

    If called from within an async context (running event loop), runs the
    coroutine in a thread pool executor. Otherwise, uses asyncio.run().

    This mirrors the pattern used by ToolManager for MCP integration.
    """
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


class AsyncExecutor:
    """Run sync functions in a thread pool with timeout. Supports async context manager."""

    def __init__(
        self, max_workers: int = 4, timeout: float = 60.0, name: str = "async-executor"
    ) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=name
        )
        self._timeout = timeout

    async def __aenter__(self) -> AsyncExecutor:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.shutdown(wait=False)

    async def run[T](self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor, functools.partial(fn, *args, **kwargs)
        )
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError as e:
            raise TimeoutError(f"Operation timed out after {self._timeout}s") from e

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
