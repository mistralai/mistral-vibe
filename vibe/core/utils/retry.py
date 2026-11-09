from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import email.utils
import functools
import logging

import httpx2

logger = logging.getLogger("vibe")

_DEFAULT_MAX_DELAY_SECONDS = 60.0

_RETRYABLE_REQUEST_ERRORS: tuple[type[httpx2.RequestError], ...] = (
    httpx2.TimeoutException,
    httpx2.ConnectError,
    httpx2.ReadError,
    httpx2.WriteError,
    httpx2.RemoteProtocolError,
)

_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def _is_retryable_http_error(error: Exception) -> bool:
    if isinstance(error, httpx2.HTTPStatusError):
        return error.response.status_code in _RETRYABLE_HTTP_STATUS_CODES
    return isinstance(error, _RETRYABLE_REQUEST_ERRORS)


def _parse_retry_after(error: Exception) -> float | None:
    """Server-directed backoff from a `Retry-After` header, in seconds.

    Handles both forms defined by RFC 9110: delta-seconds (a non-negative
    integer) and an HTTP-date. A past date yields 0. Returns None when the
    error carries no parseable header, so callers fall back to exponential
    backoff.
    """
    if not isinstance(error, httpx2.HTTPStatusError):
        return None
    value = error.response.headers.get("retry-after", "").strip()
    if not value:
        return None
    # `str.isdigit()` is True for non-ASCII decimals (e.g. superscripts) that
    # `float()` rejects, so require ASCII digits to keep the fast path total.
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        retry_at = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max((retry_at - datetime.now(UTC)).total_seconds(), 0.0)


def _next_delay(
    error: Exception,
    attempt: int,
    delay_seconds: float,
    backoff_factor: float,
    max_delay_seconds: float,
) -> float:
    """Honor a server `Retry-After` when present, else exponential backoff with
    a little jitter. Both are capped at `max_delay_seconds`.
    """
    retry_after = _parse_retry_after(error)
    if retry_after is not None:
        return min(retry_after, max_delay_seconds)
    if delay_seconds > 0:
        exponential = delay_seconds * (backoff_factor**attempt)
    else:
        exponential = 0.0
    return min(exponential + (0.05 * attempt), max_delay_seconds)


def async_retry[T, **P](
    tries: int = 3,
    delay_seconds: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay_seconds: float = _DEFAULT_MAX_DELAY_SECONDS,
    is_retryable: Callable[[Exception], bool] = _is_retryable_http_error,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Args:
        tries: Number of retry attempts
        delay_seconds: Initial delay between retries in seconds
        backoff_factor: Multiplier for delay on each retry
        max_delay_seconds: Upper bound on any single backoff, including a
                     server-directed `Retry-After`
        is_retryable: Function to determine if an exception should trigger a retry
                     (defaults to checking for retryable HTTP errors from both urllib and httpx2)

    Returns:
        Decorated function with retry logic
    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc = None
            for attempt in range(tries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt >= tries - 1 or not is_retryable(e):
                        raise e
                    current_delay = _next_delay(
                        e, attempt, delay_seconds, backoff_factor, max_delay_seconds
                    )
                    logger.warning(
                        "Retrying %s after error attempt=%d delay=%.2fs error=%r",
                        func.__qualname__,
                        attempt + 1,
                        current_delay,
                        e,
                    )
                    await asyncio.sleep(current_delay)
            raise RuntimeError(
                f"Retries exhausted. Last error: {last_exc}"
            ) from last_exc

        return wrapper

    return decorator
