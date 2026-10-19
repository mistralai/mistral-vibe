from __future__ import annotations

import asyncio
from collections.abc import Iterable

SHUTDOWN_TIMEOUT_SECONDS = 5.0


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
        except BaseException as exc:
            errors.append(exc)
    if pending:
        errors.append(
            TimeoutError(f"Timed out cancelling {len(pending)} {label} task(s)")
        )
    return errors
