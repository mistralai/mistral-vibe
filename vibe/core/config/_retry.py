from __future__ import annotations

from collections.abc import Awaitable, Callable

from vibe.core.config.types import ConcurrencyConflictError
from vibe.observability.logging import logger

# Each conflict means another writer saved, so simultaneous writers all finish.
CONFLICT_ATTEMPTS = 64


async def retry_on_conflict(
    write: Callable[[], Awaitable[list[BaseException]]],
    *,
    description: str,
    attempts: int = CONFLICT_ATTEMPTS,
) -> list[BaseException]:
    """Rerun *write* while every failure it returns is a ConcurrencyConflictError."""
    failures: list[BaseException] = []
    for _ in range(attempts):
        failures = await write()
        if not failures or not all(
            isinstance(failure, ConcurrencyConflictError) for failure in failures
        ):
            return failures
    logger.warning(
        "Gave up on %s after %d conflicting writes from other sessions",
        description,
        attempts,
    )
    return failures


__all__ = ["CONFLICT_ATTEMPTS", "retry_on_conflict"]
