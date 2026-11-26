from __future__ import annotations

import logging

import pytest

from vibe.core.config._retry import retry_on_conflict
from vibe.core.config.types import ConcurrencyConflictError


@pytest.mark.asyncio
async def test_retry_on_conflict_retries_until_a_write_lands() -> None:
    outcomes: list[list[BaseException]] = [[ConcurrencyConflictError("a", "b")], []]

    async def write() -> list[BaseException]:
        return outcomes.pop(0)

    assert await retry_on_conflict(write, description="test") == []
    assert outcomes == []


@pytest.mark.asyncio
async def test_retry_on_conflict_returns_other_failures_without_retrying() -> None:
    calls = 0
    error = RuntimeError("boom")

    async def write() -> list[BaseException]:
        nonlocal calls
        calls += 1
        return [error]

    assert await retry_on_conflict(write, description="test") == [error]
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_on_conflict_warns_when_it_gives_up(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    conflict = ConcurrencyConflictError("a", "b")

    async def write() -> list[BaseException]:
        nonlocal calls
        calls += 1
        return [conflict]

    with caplog.at_level(logging.WARNING):
        failures = await retry_on_conflict(write, description="test", attempts=3)

    assert failures == [conflict]
    assert calls == 3
    assert "Gave up on test after 3 conflicting writes" in caplog.text
