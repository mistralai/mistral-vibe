from __future__ import annotations

import asyncio

import pytest

from vibe.core.utils.pacing import AdaptivePacer


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _make_pacer(
    *,
    clock: _FakeClock,
    sleeps: list[float],
    base_interval_seconds: float = 1.0,
    decrease_factor: float = 2.0,
    max_interval_seconds: float = 60.0,
    recovery_window_seconds: float = 60.0,
    additive_decrease_seconds: float = 0.5,
) -> AdaptivePacer:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return AdaptivePacer(
        base_interval_seconds=base_interval_seconds,
        decrease_factor=decrease_factor,
        max_interval_seconds=max_interval_seconds,
        recovery_window_seconds=recovery_window_seconds,
        additive_decrease_seconds=additive_decrease_seconds,
        clock=clock,
        sleep=fake_sleep,
    )


@pytest.mark.asyncio
async def test_no_pacing_until_first_rate_limit() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(clock=clock, sleeps=sleeps)

    # Three calls with no rate limit: never sleeps, interval stays 0.
    for _ in range(3):
        await pacer.acquire()
        pacer.on_success()
        clock.advance(0.1)

    assert sleeps == []
    assert pacer.min_interval == 0.0


@pytest.mark.asyncio
async def test_first_rate_limit_sets_base_interval_and_slows_next_call() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(clock=clock, sleeps=sleeps, base_interval_seconds=1.0)

    await pacer.acquire()  # first call, no wait
    pacer.on_rate_limited()  # a 429 happened during this call
    assert pacer.min_interval == 1.0

    clock.advance(0.0)  # immediate next call
    await pacer.acquire()  # should wait ~1.0s
    assert sleeps == [pytest.approx(1.0)]


@pytest.mark.asyncio
async def test_repeated_rate_limits_back_off_multiplicatively() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(clock=clock, sleeps=sleeps, base_interval_seconds=1.0)

    await pacer.acquire()
    pacer.on_rate_limited()
    assert pacer.min_interval == 1.0
    pacer.on_rate_limited()
    assert pacer.min_interval == 2.0
    pacer.on_rate_limited()
    assert pacer.min_interval == 4.0


def test_interval_capped_at_max() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(
        clock=clock, sleeps=sleeps, base_interval_seconds=1.0, max_interval_seconds=3.0
    )

    pacer.on_rate_limited()
    assert pacer.min_interval == 1.0
    pacer.on_rate_limited()
    assert pacer.min_interval == 2.0
    pacer.on_rate_limited()
    assert pacer.min_interval == 3.0  # 4 capped to 3
    pacer.on_rate_limited()
    assert pacer.min_interval == 3.0


@pytest.mark.asyncio
async def test_success_does_not_recover_within_quiet_window() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(clock=clock, sleeps=sleeps, recovery_window_seconds=60.0)

    await pacer.acquire()
    pacer.on_rate_limited()
    assert pacer.min_interval == 1.0

    clock.advance(30.0)
    await pacer.acquire()  # new call, clears the per-call rate-limit flag
    pacer.on_success()
    assert pacer.min_interval == 1.0  # still within window


@pytest.mark.asyncio
async def test_success_recovers_additively_after_quiet_window() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(
        clock=clock,
        sleeps=sleeps,
        base_interval_seconds=2.0,
        recovery_window_seconds=60.0,
        additive_decrease_seconds=0.5,
    )

    await pacer.acquire()
    pacer.on_rate_limited()
    assert pacer.min_interval == 2.0
    pacer.on_rate_limited()
    assert pacer.min_interval == 4.0

    clock.advance(61.0)  # past the recovery window
    await pacer.acquire()  # new call without a rate limit
    pacer.on_success()
    assert pacer.min_interval == 3.5
    pacer.on_success()
    assert pacer.min_interval == 3.0


@pytest.mark.asyncio
async def test_recovery_floors_at_zero_disabling_pacing() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(
        clock=clock,
        sleeps=sleeps,
        base_interval_seconds=1.0,
        recovery_window_seconds=0.01,
        additive_decrease_seconds=10.0,  # overshoot to force floor
    )

    await pacer.acquire()
    pacer.on_rate_limited()
    assert pacer.min_interval == 1.0
    clock.advance(0.02)
    await pacer.acquire()  # new call, no rate limit this call
    pacer.on_success()
    assert pacer.min_interval == 0.0


@pytest.mark.asyncio
async def test_same_call_success_after_retry_does_not_recover() -> None:
    """Retry-After sleep must not count as the quiet recovery window."""
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(
        clock=clock,
        sleeps=sleeps,
        base_interval_seconds=1.0,
        recovery_window_seconds=60.0,
        additive_decrease_seconds=0.5,
    )

    await pacer.acquire()
    pacer.on_rate_limited()
    assert pacer.min_interval == 1.0

    clock.advance(60.0)  # Retry-After wait inside the same call
    pacer.on_success()
    assert pacer.min_interval == 1.0  # restarts the window, does not recover

    await pacer.acquire()  # next call, still within the restarted window
    pacer.on_success()
    assert pacer.min_interval == 1.0

    clock.advance(61.0)  # now past the quiet window
    await pacer.acquire()
    pacer.on_success()
    assert pacer.min_interval == 0.5


def test_unseen_rate_limit_means_success_is_noop() -> None:
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(clock=clock, sleeps=sleeps)

    pacer.on_success()  # never saw a rate limit
    assert pacer.min_interval == 0.0


@pytest.mark.asyncio
async def test_failed_call_with_rate_limit_restarts_window_not_recovers() -> None:
    """A call that exhausts 429 retries (on_failure) restarts the quiet window,
    so the next successful call does not recover prematurely.
    """
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(
        clock=clock,
        sleeps=sleeps,
        base_interval_seconds=1.0,
        recovery_window_seconds=60.0,
        additive_decrease_seconds=0.5,
    )

    await pacer.acquire()
    pacer.on_rate_limited()
    assert pacer.min_interval == 1.0
    clock.advance(60.0)  # Retry-After backoff inside the failing call
    pacer.on_failure()
    assert pacer.min_interval == 1.0  # failure never recovers

    # Next call succeeds. Even though 60s+ elapsed since the last 429, the
    # failure restarted the window, so this success must not recover yet.
    await pacer.acquire()
    pacer.on_success()
    assert pacer.min_interval == 1.0

    clock.advance(61.0)  # now a genuine quiet window
    await pacer.acquire()
    pacer.on_success()
    assert pacer.min_interval == 0.5


@pytest.mark.asyncio
async def test_failed_call_without_rate_limit_does_not_recover() -> None:
    """A failure with no rate limit this call (e.g. a 500) must not recover,
    even if the quiet window has otherwise passed.
    """
    clock = _FakeClock()
    sleeps: list[float] = []
    pacer = _make_pacer(
        clock=clock,
        sleeps=sleeps,
        base_interval_seconds=2.0,
        recovery_window_seconds=60.0,
        additive_decrease_seconds=0.5,
    )

    await pacer.acquire()
    pacer.on_rate_limited()
    assert pacer.min_interval == 2.0

    clock.advance(61.0)  # quiet window elapsed
    await pacer.acquire()  # new call, no rate limit this call
    pacer.on_failure()
    assert pacer.min_interval == 2.0  # failure never recovers


@pytest.mark.asyncio
async def test_acquire_serializes_concurrent_callers() -> None:
    """Concurrent acquires must not both skip the wait: the lock serializes
    them so their pacing sleeps never overlap.
    """
    in_sleep = 0
    max_concurrent = 0

    async def tracking_sleep(seconds: float) -> None:
        nonlocal in_sleep, max_concurrent
        in_sleep += 1
        max_concurrent = max(max_concurrent, in_sleep)
        await asyncio.sleep(0)  # yield so any unsynchronized caller could overlap
        in_sleep -= 1

    pacer = AdaptivePacer(
        base_interval_seconds=1.0, clock=lambda: 0.0, sleep=tracking_sleep
    )
    pacer.on_rate_limited()  # arm pacing so every acquire waits

    await asyncio.gather(pacer.acquire(), pacer.acquire(), pacer.acquire())

    assert max_concurrent == 1
