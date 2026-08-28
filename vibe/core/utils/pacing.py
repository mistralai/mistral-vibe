from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import time


def _monotonic() -> float:
    return time.monotonic()


async def _real_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


@dataclass
class AdaptivePacer:
    """Proactive call pacing with AIMD-style adaptation.

    Maintains a minimum interval between successive calls. It is a no-op until
    the first rate limit, then backs off multiplicatively on each rate limit and
    recovers additively once a quiet window passes with no rate limits.

    Sits alongside reactive retry: retry rescues a single rate-limited call,
    while the pacer lowers the rate of *subsequent* calls so fewer of them hit
    the limit at all. For unsupervised long-running work against a shared rate
    limit, this keeps the agent progressing instead of stalling on every 429.

    The interval gates the gap between call *starts* (computed at acquire time
    from the interval then in effect), so a rate limit observed during one call
    slows the next one, not the retry attempts inside the same call.
    """

    base_interval_seconds: float = 1.0
    decrease_factor: float = 2.0
    max_interval_seconds: float = 60.0
    recovery_window_seconds: float = 60.0
    additive_decrease_seconds: float = 0.5
    clock: Callable[[], float] = _monotonic
    sleep: Callable[[float], Awaitable[None]] = _real_sleep

    def __post_init__(self) -> None:
        self._min_interval = 0.0
        self._last_call_at: float | None = None
        self._last_rate_limited_at: float = 0.0
        self._seen_rate_limit = False
        self._rate_limited_this_call = False
        # Guards acquire() only: the on_* / _settle methods are synchronous, so
        # the event loop already runs them atomically. The single await is the
        # sleep in acquire, where two callers could otherwise both skip the wait.
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait, if needed, so successive calls stay at least `min_interval` apart."""
        async with self._lock:
            if self._last_call_at is not None:
                elapsed = self._clock_now() - self._last_call_at
                wait = self._min_interval - elapsed
                if wait > 0:
                    await self.sleep(wait)
            self._last_call_at = self._clock_now()
            self._rate_limited_this_call = False

    def on_rate_limited(self) -> None:
        """Slow down after a rate limit. Multiplicative increase of the interval."""
        if self._min_interval == 0.0:
            self._min_interval = self.base_interval_seconds
        else:
            self._min_interval = min(
                self._min_interval * self.decrease_factor, self.max_interval_seconds
            )
        self._last_rate_limited_at = self._clock_now()
        self._seen_rate_limit = True
        self._rate_limited_this_call = True

    def on_success(self) -> None:
        """Record a successful call.

        Recovers (additive decrease, floored at 0) once no rate limit has been
        seen for `recovery_window_seconds`. A success that followed a rate limit
        in the same call (e.g. retried through a `Retry-After`) instead restarts
        the quiet window, so retry delay is not counted as idle time.
        """
        self._settle(recover=True)

    def on_failure(self) -> None:
        """Record a failed call (e.g. retries exhausted).

        Never recovers, but still restarts the quiet window if the call was rate
        limited, so a later call does not count this call's `Retry-After` backoff
        as idle time and recover prematurely.
        """
        self._settle(recover=False)

    def _settle(self, *, recover: bool) -> None:
        if not self._seen_rate_limit or self._min_interval == 0.0:
            self._rate_limited_this_call = False
            return
        if self._rate_limited_this_call:
            self._last_rate_limited_at = self._clock_now()
            self._rate_limited_this_call = False
            return
        if not recover:
            return
        if (
            self._clock_now() - self._last_rate_limited_at
            < self.recovery_window_seconds
        ):
            return
        self._min_interval = max(
            self._min_interval - self.additive_decrease_seconds, 0.0
        )

    @property
    def min_interval(self) -> float:
        """Current enforced gap between calls, in seconds (0 = no pacing)."""
        return self._min_interval

    def _clock_now(self) -> float:
        return self.clock()
