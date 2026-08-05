from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum, auto
import functools
import logging

import httpx

logger = logging.getLogger("vibe")

_RETRYABLE_REQUEST_ERRORS: tuple[type[httpx.RequestError], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)

_SERVER_ERROR_STATUS_FLOOR = 500


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
        if isinstance(error, httpx.HTTPStatusError):
            return cls.from_http_status(error.response.status_code)
        return cls(RetryCategory.for_transport_error(error), type(error).__name__)


type RetryObserver = Callable[[RetryReason], Awaitable[None]]


def _is_retryable_http_error(e: Exception) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504, 529}
    if isinstance(e, _RETRYABLE_REQUEST_ERRORS):
        return True
    return False


def async_retry[T, **P](
    tries: int = 3,
    delay_seconds: float = 0.5,
    backoff_factor: float = 2.0,
    is_retryable: Callable[[Exception], bool] = _is_retryable_http_error,
    on_retry: RetryObserver | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Args:
        tries: Number of retry attempts
        delay_seconds: Initial delay between retries in seconds
        backoff_factor: Multiplier for delay on each retry
        is_retryable: Function to determine if an exception should trigger a retry
                     (defaults to checking for retryable HTTP errors from both urllib and httpx)
        on_retry: Notified before each backoff, so callers can surface the retry

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
                    if attempt < tries - 1 and is_retryable(e):
                        current_delay = (delay_seconds * (backoff_factor**attempt)) + (
                            0.05 * attempt
                        )
                        logger.warning(
                            "Retrying %s after error attempt=%d/%d delay=%.2fs error=%r",
                            func.__qualname__,
                            attempt + 1,
                            tries,
                            current_delay,
                            e,
                        )
                        if on_retry is not None:
                            await on_retry(RetryReason.from_error(e))
                        await asyncio.sleep(current_delay)
                        continue
                    raise e
            raise RuntimeError(
                f"Retries exhausted. Last error: {last_exc}"
            ) from last_exc

        return wrapper

    return decorator


def async_generator_retry[T, **P](
    tries: int = 3,
    delay_seconds: float = 0.5,
    backoff_factor: float = 2.0,
    is_retryable: Callable[[Exception], bool] = _is_retryable_http_error,
    on_retry: RetryObserver | None = None,
) -> Callable[[Callable[P, AsyncGenerator[T]]], Callable[P, AsyncGenerator[T]]]:
    """Retry decorator for async generators.

    Only the first item is retried: once an item has been yielded the caller has
    seen output, and restarting would duplicate it.

    Args:
        tries: Number of retry attempts
        delay_seconds: Initial delay between retries in seconds
        backoff_factor: Multiplier for delay on each retry
        is_retryable: Function to determine if an exception should trigger a retry
                     (defaults to checking for retryable HTTP errors from both urllib and httpx)
        on_retry: Notified before each backoff, so callers can surface the retry

    Returns:
        Decorated async generator function with retry logic
    """

    def decorator(
        func: Callable[P, AsyncGenerator[T]],
    ) -> Callable[P, AsyncGenerator[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> AsyncGenerator[T]:
            last_exc = None
            for attempt in range(tries):
                generator = func(*args, **kwargs)
                try:
                    first_item = await anext(generator)
                except StopAsyncIteration:
                    return
                except Exception as e:
                    last_exc = e
                    await generator.aclose()
                    if attempt < tries - 1 and is_retryable(e):
                        current_delay = (delay_seconds * (backoff_factor**attempt)) + (
                            0.05 * attempt
                        )
                        logger.warning(
                            "Retrying %s after error attempt=%d/%d delay=%.2fs error=%r",
                            func.__qualname__,
                            attempt + 1,
                            tries,
                            current_delay,
                            e,
                        )
                        if on_retry is not None:
                            await on_retry(RetryReason.from_error(e))
                        await asyncio.sleep(current_delay)
                        continue
                    raise
                yield first_item
                async for item in generator:
                    yield item
                return
            raise RuntimeError(
                f"Retries exhausted. Last error: {last_exc}"
            ) from last_exc

        return wrapper

    return decorator
