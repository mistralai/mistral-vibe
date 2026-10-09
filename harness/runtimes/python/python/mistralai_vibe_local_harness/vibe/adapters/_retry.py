"""The retry policy shared by every model provider adapter.

One attempt covers the whole call: sending the request and reading its response
to the end. A failure part way through a stream is as retryable as one before it
starts. An attempt returns only once its response is complete, so nothing from a
failed attempt reaches the completion result.

Provisional output is different: a failed attempt may already have published
part of its stream through the delta observer. Each retry notice goes through
the same observer chain before the next attempt starts, which lets the
projection discard the failed attempt's output.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import logging
import random
import time

import httpx

from mistralai_vibe_local_harness.vibe._runtime_config import (
    ProviderRetry,
    ProviderRetryObserver,
)
from mistralai_vibe_local_harness.vibe.adapters._provider_failure import (
    ProviderErrorResponse,
    ProviderFailure,
    read_provider_failure,
)

logger = logging.getLogger(__name__)


async def call_with_retries[T](
    attempt: Callable[[], Awaitable[T]],
    *,
    max_elapsed_time_s: float,
    notices: RetryNotices,
) -> T:
    """Run ``attempt`` until it succeeds, fails for good, or the budget is spent.

    The budget bounds when a new attempt may start, not how long one may run:
    a retry whose wait would end after the budget is not made. The wait checked
    is the one the provider asked for, before the delay cap shortens it, since
    an attempt the provider said would fail is not worth making early. A stall is
    retried at most ``_MAX_STALL_RETRIES`` times: when the silence was a long
    generation the provider does not stream, every attempt repeats it, and each
    one is paid for.
    """
    started = time.monotonic()
    retry_attempt = 0
    stall_retries = 0
    while True:
        try:
            return await attempt()
        except Exception as error:
            failure = read_provider_failure(error)
            stalls_spent = failure.stalled and stall_retries >= _MAX_STALL_RETRIES
            if stalls_spent or not failure.retryable:
                raise
            wait_s = _requested_wait_s(
                failure,
                retry_attempt + 1,
                now=datetime.now(UTC),
                jitter_s=random.uniform(0, _MAX_RETRY_JITTER_S),
            )
            if time.monotonic() - started + wait_s > max_elapsed_time_s:
                raise
            delay_s = min(wait_s, _MAX_RETRY_DELAY_S)
            if failure.stalled:
                stall_retries += 1
            retry_attempt += 1
            logger.warning(
                "Retrying model completion (attempt %d, delay %.2fs): %r",
                retry_attempt,
                delay_s,
                error,
            )
            await notices.announce(
                ProviderRetry(
                    category=failure.category,
                    detail=failure.label,
                    delay_s=delay_s,
                    retry_attempt=retry_attempt,
                )
            )
            await asyncio.sleep(delay_s)


class RetryNotices:
    """The retry notice of one completion, from announcement to recovery.

    A notice stays up until the provider accepts a later attempt.
    ``clear_on_success`` is an HTTPX response hook, so it clears the notice as
    soon as a successful status arrives, before the response body streams.
    """

    def __init__(self, observer: ProviderRetryObserver | None) -> None:
        self._observer = observer
        self._showing = False

    async def announce(self, retry: ProviderRetry) -> None:
        self._showing = True
        if self._observer is not None:
            await self._observer(retry)

    async def clear_on_success(self, response: httpx.Response) -> None:
        if not response.is_success or not self._showing:
            return
        self._showing = False
        if self._observer is not None:
            await self._observer(None)


_INITIAL_RETRY_DELAY_S = 0.5
_RETRY_BACKOFF = 1.5
_MAX_RETRY_DELAY_S = 30.0
_MAX_RETRY_JITTER_S = 1.0
_MAX_STALL_RETRIES = 1
# The backoff reaches its maximum long before this exponent; the cap keeps a
# large retry budget from overflowing the power.
_MAX_BACKOFF_EXPONENT = 16


def _requested_wait_s(
    failure: ProviderFailure, retry_attempt: int, *, now: datetime, jitter_s: float
) -> float:
    """The wait before the next attempt: the provider's ``Retry-After``, uncapped, or the backoff."""
    if failure.response is not None:
        retry_after = _retry_after_s(failure.response, now)
        if retry_after is not None:
            return retry_after
    exponent = min(retry_attempt - 1, _MAX_BACKOFF_EXPONENT)
    backoff = _INITIAL_RETRY_DELAY_S * _RETRY_BACKOFF**exponent
    return min(backoff + jitter_s, _MAX_RETRY_DELAY_S)


def _retry_after_s(response: ProviderErrorResponse, now: datetime) -> float | None:
    """The wait a ``Retry-After`` header asks for, in seconds or as an HTTP date."""
    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max((retry_at - now).total_seconds(), 0.0)


__all__ = ["RetryNotices", "call_with_retries"]
