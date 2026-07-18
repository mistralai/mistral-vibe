from __future__ import annotations

import pytest

from vibe.core.watchdog.event_adapter import PendingWatchdogEvent
from vibe.core.watchdog.events import EventKind
from vibe.core.watchdog.queue import WatchdogEventQueue


def pending(
    kind: EventKind, *, critical: bool, key: str | None = None, value: int = 0
) -> PendingWatchdogEvent:
    return PendingWatchdogEvent(
        kind=kind,
        observed_at_monotonic=float(value),
        payload={"value": value},
        critical=critical,
        coalesce_key=key,
    )


@pytest.mark.asyncio
async def test_progress_is_coalesced_without_losing_critical_result() -> None:
    queue = WatchdogEventQueue(capacity=4, critical_reserve=2)

    queue.put_nowait(pending(EventKind.TOOL_PROGRESS, critical=False, key="x"))
    queue.put_nowait(pending(EventKind.TOOL_PROGRESS, critical=False, key="x", value=2))
    result = queue.put_nowait(pending(EventKind.TOOL_FINISHED, critical=True))
    queue.close()

    assert result.accepted
    progress = await queue.get()
    finished = await queue.get()

    assert progress is not None
    assert progress.payload == {"value": 2}
    assert finished is not None
    assert finished.kind == EventKind.TOOL_FINISHED


def test_critical_overflow_reports_integrity_loss() -> None:
    queue = WatchdogEventQueue(capacity=2, critical_reserve=1)
    queue.put_nowait(pending(EventKind.TOOL_STARTED, critical=True))
    queue.put_nowait(pending(EventKind.TOOL_FINISHED, critical=True))

    result = queue.put_nowait(pending(EventKind.RUN_FAILED, critical=True))

    assert not result.accepted
    assert result.integrity_lost
