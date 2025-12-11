"""ChefChat Async Helpers
========================

Async utilities for ChefChat providing:
- Spinner-wrapped async operations
- Batch execution with concurrency limits
- Progress tracking for long-running tasks

Usage:
    from vibe.utils.async_helpers import run_with_spinner, batch_execute

    result = await run_with_spinner(api_call(), "Calling API...")
    results = await batch_execute(tasks, max_concurrent=5)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

T = TypeVar("T")

# Shared console instance
_console = Console()


async def run_with_spinner(
    coro: Awaitable[T], message: str = "Processing...", console: Console | None = None
) -> T:
    """Run an async task with a Rich spinner.

    The spinner is displayed while the coroutine executes, providing
    visual feedback that work is being done.

    Args:
        coro: The awaitable/coroutine to execute
        message: Message to display next to the spinner
        console: Optional custom console (uses default if not provided)

    Returns:
        The result of the coroutine

    Example:
        >>> result = await run_with_spinner(
        ...     fetch_data(),
        ...     "🍳 Fetching ingredients..."
        ... )
    """
    con = console or _console

    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[progress.description]{task.description}"),
        console=con,
        transient=True,
    ) as progress:
        task = progress.add_task(message, total=None)
        try:
            result = await coro
        finally:
            progress.remove_task(task)
        return result


async def batch_execute(
    tasks: list[Callable[[], Awaitable[T]]], max_concurrent: int = 5
) -> list[T | Exception]:
    """Execute multiple async tasks with concurrency limit.

    Useful for parallel tool execution while preventing resource exhaustion.

    Args:
        tasks: List of async callables (zero-argument functions returning awaitables)
        max_concurrent: Maximum number of tasks to run simultaneously

    Returns:
        List of results (or exceptions for failed tasks) in original order

    Example:
        >>> async def fetch(url): ...
        >>> tasks = [lambda u=url: fetch(u) for url in urls]
        >>> results = await batch_execute(tasks, max_concurrent=3)
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    results: list[T | Exception] = []

    async def limited_task(task: Callable[[], Awaitable[T]], index: int) -> None:
        async with semaphore:
            try:
                result = await task()
                results.append((index, result))  # type: ignore
            except Exception as e:
                results.append((index, e))  # type: ignore

    # Run all tasks concurrently (limited by semaphore)
    await asyncio.gather(*[limited_task(t, i) for i, t in enumerate(tasks)])

    # Sort by original index and extract results
    results.sort(key=lambda x: x[0])  # type: ignore
    return [r[1] for r in results]  # type: ignore


async def run_with_progress(
    coro: Awaitable[T],
    total: int,
    description: str = "Working...",
    console: Console | None = None,
) -> T:
    """Run an async task with a progress bar.

    For tasks where progress can be tracked (e.g., file operations).

    Args:
        coro: The awaitable to execute
        total: Total number of steps
        description: Description text
        console: Optional custom console

    Returns:
        The result of the coroutine
    """
    con = console or _console

    with Progress(console=con, transient=True) as progress:
        task = progress.add_task(description, total=total)
        try:
            result = await coro
            progress.update(task, completed=total)
        finally:
            pass
        return result


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = ["batch_execute", "run_with_progress", "run_with_spinner"]
