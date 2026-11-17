from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence

from vibe.observability.logging import logger

SHUTDOWN_TIMEOUT_SECONDS = 5.0


def raise_if_cancelled(errors: Sequence[BaseException]) -> None:
    """Re-raise the first cancellation among collected errors, logging the rest."""
    cancelled: asyncio.CancelledError | None = None
    dropped: list[BaseException] = []
    for exc in errors:
        found = _find_cancellation(exc) if cancelled is None else None
        if found is None:
            dropped.append(exc)
            continue
        cancelled = found
        if isinstance(exc, BaseExceptionGroup):
            _, rest = exc.split(asyncio.CancelledError)
            if rest is not None:
                dropped.append(rest)
    if cancelled is None:
        return
    for exc in dropped:
        logger.warning("Shutdown error dropped for a cancellation", exc_info=exc)
    raise cancelled


def _find_cancellation(exc: BaseException) -> asyncio.CancelledError | None:
    if isinstance(exc, asyncio.CancelledError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            if (found := _find_cancellation(inner)) is not None:
                return found
    return None


async def cancel_tasks[ResultT](
    tasks: Iterable[asyncio.Task[ResultT]],
    *,
    label: str,
    timeout: float = SHUTDOWN_TIMEOUT_SECONDS,
) -> list[BaseException]:
    pending = set(tasks)
    for task in pending:
        task.cancel()
    if not pending:
        return []
    done, pending = await asyncio.wait(pending, timeout=timeout)
    errors: list[BaseException] = []
    for task in done:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except BaseExceptionGroup as group:
            # Shutdowns that collect their errors wrap the cancellation sent here.
            _, rest = group.split(asyncio.CancelledError)
            if rest is not None:
                errors.append(rest)
        except BaseException as exc:
            errors.append(exc)
    if pending:
        errors.append(
            TimeoutError(f"Timed out cancelling {len(pending)} {label} task(s)")
        )
    return errors
