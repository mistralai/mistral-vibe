from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
import email.utils
from enum import StrEnum, auto
import functools
from itertools import count
import logging
import math
import time

import httpx

logger = logging.getLogger("vibe")

_DEFAULT_MAX_DELAY_SECONDS = 60.0

_RETRYABLE_REQUEST_ERRORS: tuple[type[httpx.RequestError], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)

_SERVER_ERROR_STATUS_FLOOR = 500


class StreamHTTPError(RuntimeError):
    def __init__(self, message: str, status: int | None) -> None:
        self.status = status
        super().__init__(message)


class RetryCategory(StrEnum):
    RATE_LIMITED = auto()
    SERVER_ERROR = auto()
    TIMED_OUT = auto()
    CONNECTION = auto()
    UNKNOWN = auto()

    @classmethod
    def for_http_status(cls, status_code: int) -> RetryCategory:
        match status_code:
            case 429:
                return cls.RATE_LIMITED
            case 408:
                return cls.TIMED_OUT
            case status if status >= _SERVER_ERROR_STATUS_FLOOR:
                return cls.SERVER_ERROR
            case _:
                return cls.UNKNOWN

    @classmethod
    def for_transport_error(cls, error: Exception) -> RetryCategory:
        if isinstance(error, httpx.TimeoutException):
            return cls.TIMED_OUT
        if isinstance(error, _RETRYABLE_REQUEST_ERRORS):
            return cls.CONNECTION
        return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class RetryReason:
    """Why a request is being retried: a category clients can render, plus the
    raw detail for logs and tooltips. Classified here, where the exception is,
    so no consumer has to parse `detail` back into a category.
    """

    category: RetryCategory
    detail: str

    @classmethod
    def from_http_status(cls, status_code: int) -> RetryReason:
        return cls(RetryCategory.for_http_status(status_code), f"HTTP {status_code}")

    @classmethod
    def from_error(cls, error: Exception) -> RetryReason:
        status = _http_error_status(error)
        if status is not None:
            return cls.from_http_status(status)
        return cls(RetryCategory.for_transport_error(error), type(error).__name__)


type RetryObserver = Callable[[RetryReason], Awaitable[None]]


_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def _http_error_status(error: Exception) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    if isinstance(error, StreamHTTPError):
        return error.status
    return None


def _is_retryable_http_error(error: Exception) -> bool:
    status = _http_error_status(error)
    if status is not None:
        return status in _RETRYABLE_HTTP_STATUS_CODES
    return isinstance(error, _RETRYABLE_REQUEST_ERRORS)


def _parse_retry_after(error: Exception) -> float | None:
    """Server-directed backoff from a `Retry-After` header, in seconds.

    Handles both forms defined by RFC 9110: delta-seconds (a non-negative
    integer) and an HTTP-date. A past date yields 0. Returns None when the
    error carries no parseable header, so callers fall back to exponential
    backoff.
    """
    if not isinstance(error, httpx.HTTPStatusError):
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
    # Once the exponential backoff would reach max_delay_seconds, further
    # attempts are saturated there. Returning the cap directly avoids computing
    # backoff_factor ** attempt, which overflows once retries run unbounded by
    # count (tries=None). Below the cap the exponent is small enough to be safe.
    if delay_seconds > 0 and backoff_factor > 1.0 and max_delay_seconds > 0:
        cap = math.floor(
            math.log(max_delay_seconds / delay_seconds) / math.log(backoff_factor)
        )
        if attempt > cap:
            return max_delay_seconds
    if delay_seconds > 0:
        exponential = delay_seconds * (backoff_factor**attempt)
    else:
        exponential = 0.0
    return min(exponential + (0.05 * attempt), max_delay_seconds)


def async_retry[T, **P](
    tries: int | None = 3,
    delay_seconds: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay_seconds: float = _DEFAULT_MAX_DELAY_SECONDS,
    max_elapsed_time: float | None = None,
    is_retryable: Callable[[Exception], bool] = _is_retryable_http_error,
    on_retry: RetryObserver | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Args:
        tries: Number of retry attempts, or None to retry for as long as the
                     time budget allows.
        delay_seconds: Initial delay between retries in seconds
        backoff_factor: Multiplier for delay on each retry
        max_delay_seconds: Upper bound on any single backoff, including a
                     server-directed `Retry-After`
        max_elapsed_time: Total wall-clock budget for retries in seconds. Once
                     exceeded, the last error is re-raised. None disables the
                     time bound; then `tries` must bound the count.
        is_retryable: Function to determine if an exception should trigger a retry
                     (defaults to checking for retryable HTTP errors from both urllib and httpx)
        on_retry: Notified before each backoff, so callers can surface the retry

    Returns:
        Decorated function with retry logic
    """
    if tries is None and max_elapsed_time is None:
        raise ValueError(
            "async_retry requires a bound: set `tries` or `max_elapsed_time`"
        )

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc = None
            start = time.monotonic() if max_elapsed_time is not None else 0.0
            attempts = count() if tries is None else range(tries)
            for attempt in attempts:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    is_last = tries is not None and attempt >= tries - 1
                    budget_spent = (
                        max_elapsed_time is not None
                        and time.monotonic() - start >= max_elapsed_time
                    )
                    if is_last or budget_spent or not is_retryable(e):
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
                    if on_retry is not None:
                        await on_retry(RetryReason.from_error(e))
                    await asyncio.sleep(current_delay)
            raise RuntimeError(
                f"Retries exhausted. Last error: {last_exc}"
            ) from last_exc

        return wrapper

    return decorator


def async_generator_retry[T, **P](
    tries: int | None = 3,
    delay_seconds: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay_seconds: float = _DEFAULT_MAX_DELAY_SECONDS,
    max_elapsed_time: float | None = None,
    is_retryable: Callable[[Exception], bool] = _is_retryable_http_error,
    on_retry: RetryObserver | None = None,
) -> Callable[[Callable[P, AsyncGenerator[T]]], Callable[P, AsyncGenerator[T]]]:
    """Retry decorator for async generators.

    Only the first item is retried: once an item has been yielded the caller has
    seen output, and restarting would duplicate it.

    Args:
        tries: Number of retry attempts, or None to retry for as long as the
                     time budget allows.
        delay_seconds: Initial delay between retries in seconds
        backoff_factor: Multiplier for delay on each retry
        max_delay_seconds: Upper bound on any single backoff, including a
                     server-directed `Retry-After`
        max_elapsed_time: Total wall-clock budget for retries in seconds. Once
                     exceeded, the last error is re-raised. None disables the
                     time bound; then `tries` must bound the count.
        is_retryable: Function to determine if an exception should trigger a retry
                     (defaults to checking for retryable HTTP errors from both urllib and httpx)
        on_retry: Notified before each backoff, so callers can surface the retry

    Returns:
        Decorated async generator function with retry logic
    """
    if tries is None and max_elapsed_time is None:
        raise ValueError(
            "async_generator_retry requires a bound: set `tries` or `max_elapsed_time`"
        )

    def decorator(
        func: Callable[P, AsyncGenerator[T]],
    ) -> Callable[P, AsyncGenerator[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> AsyncGenerator[T]:
            last_exc = None
            start = time.monotonic() if max_elapsed_time is not None else 0.0
            attempts = count() if tries is None else range(tries)
            for attempt in attempts:
                retry_error: Exception | None = None
                async with aclosing(func(*args, **kwargs)) as generator:
                    try:
                        first_item = await anext(generator)
                    except StopAsyncIteration:
                        return
                    except Exception as e:
                        last_exc = e
                        is_last = tries is not None and attempt >= tries - 1
                        budget_spent = (
                            max_elapsed_time is not None
                            and time.monotonic() - start >= max_elapsed_time
                        )
                        if is_last or budget_spent or not is_retryable(e):
                            raise
                        retry_error = e
                    else:
                        yield first_item
                        async for item in generator:
                            yield item
                        return

                assert retry_error is not None
                current_delay = _next_delay(
                    retry_error,
                    attempt,
                    delay_seconds,
                    backoff_factor,
                    max_delay_seconds,
                )
                logger.warning(
                    "Retrying %s after error attempt=%d delay=%.2fs error=%r",
                    func.__qualname__,
                    attempt + 1,
                    current_delay,
                    retry_error,
                )
                if on_retry is not None:
                    await on_retry(RetryReason.from_error(retry_error))
                await asyncio.sleep(current_delay)
            raise RuntimeError(
                f"Retries exhausted. Last error: {last_exc}"
            ) from last_exc

        return wrapper

    return decorator
