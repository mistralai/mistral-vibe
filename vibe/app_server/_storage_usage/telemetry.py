"""``vibe.storage_scan.done``: aggregated, never a path, title, branch or id.

Enough to decide whether the 10-minute scan limit fits real ``~/.vibe``
folders: how long scans take (buckets reaching past the limit), whether the
limit cut one short, told apart from entries it could not read, full or
incremental, and how many worktrees it re-measured.

Sent with the newest open channel's credentials (a scan belongs to no
session); ``enable_telemetry`` decides as for any event.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import platform
from typing import Any

from vibe.app_server.protocol import StorageUsageReadResponse
from vibe.core.telemetry.send import TelemetryClient
from vibe.observability.logging import logger

SCAN_DONE = "vibe.storage_scan.done"
_GB = 10**9
_MINUTE_S = 60.0


# (upper bound, label), first match wins; past the last bound, the default.
_SIZES = (
    (1 * _GB, "<1 GB"),
    (5 * _GB, "1–5 GB"),
    (20 * _GB, "5–20 GB"),
    (50 * _GB, "20–50 GB"),
)
_COUNTS = ((1, "0"), (11, "1–10"), (51, "11–50"), (101, "51–100"))
# Past the 10-minute limit too: a scan that ends there was cut short, and one
# ending just below it is how close the limit came.
_DURATIONS = (
    (5.0, "<5 s"),
    (30.0, "5–30 s"),
    (2 * _MINUTE_S, "30 s–2 min"),
    (5 * _MINUTE_S, "2–5 min"),
    (8 * _MINUTE_S, "5–8 min"),
    (10 * _MINUTE_S, "8–10 min"),
    (15 * _MINUTE_S, "10–15 min"),
)


def _bucket(value: float, bounds: tuple[tuple[float, str], ...], last: str) -> str:
    return next((label for bound, label in bounds if value < bound), last)


def size_bucket(size: int) -> str:
    return _bucket(size, _SIZES, "50+ GB")


def count_bucket(count: int) -> str:
    return _bucket(count, _COUNTS, "100+")


def duration_bucket(seconds: float) -> str:
    return _bucket(seconds, _DURATIONS, "15 min+")


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    """What the scan itself reported, beyond the snapshot clients read."""

    duration_s: float
    remeasured: int
    time_limit_hit: bool
    full: bool

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> ScanOutcome:
        duration_ms = document.get("durationMs")
        remeasured = document.get("remeasured")
        return cls(
            duration_s=duration_ms / 1000 if isinstance(duration_ms, int) else 0.0,
            remeasured=remeasured if isinstance(remeasured, int) else 0,
            time_limit_hit=document.get("cutByTimeLimit") is True,
            full=document.get("fullScan") is True,
        )


def scan_properties(
    response: StorageUsageReadResponse, scan: ScanOutcome | None
) -> dict[str, object]:
    """``scan`` is None for a scan that never produced a result."""
    snapshot = response.snapshot
    total = snapshot.totals.total_bytes if snapshot is not None else 0
    skipped = snapshot.skipped_entry_count if snapshot is not None and scan else 0
    time_limit_hit = scan is not None and scan.time_limit_hit
    return {
        "status": response.status,
        "partial_reason": _partial_reason(
            time_limit_hit=time_limit_hit, skipped=skipped
        ),
        "time_limit_hit": time_limit_hit,
        "full": scan is not None and scan.full,
        "total_bucket": size_bucket(total),
        "remeasured_worktrees_bucket": count_bucket(scan.remeasured if scan else 0),
        "duration_bucket": duration_bucket(scan.duration_s) if scan else None,
        "platform": platform.system().lower(),
    }


def _partial_reason(*, time_limit_hit: bool, skipped: int) -> str | None:
    if time_limit_hit and skipped:
        return "time_limit_and_unreadable"
    if time_limit_hit:
        return "time_limit"
    if skipped:
        return "unreadable"
    return None


type TelemetryFactory = Callable[[], Awaitable[TelemetryClient]]


class ScanTelemetry:
    """Sends with a client built for each event from the newest open channel's
    factory, so the user's current config and that channel's credentials apply,
    and a closed channel is never reached through it.
    """

    def __init__(self) -> None:
        self._factories: dict[int, TelemetryFactory] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def offer(self, key: int, factory: TelemetryFactory) -> None:
        self._factories[key] = factory

    def withdraw(self, key: int) -> None:
        self._factories.pop(key, None)

    def scan_finished(
        self, response: StorageUsageReadResponse, scan: ScanOutcome
    ) -> None:
        self._send(scan_properties(response, scan))

    def scan_failed(self, response: StorageUsageReadResponse) -> None:
        self._send(
            scan_properties(response.model_copy(update={"status": "failed"}), None)
        )

    def _send(self, properties: dict[str, object]) -> None:
        task = asyncio.create_task(self._send_async(properties))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_async(self, properties: dict[str, object]) -> None:
        if not self._factories:
            return
        factory = self._factories[max(self._factories)]
        try:
            client = await factory()
        # Broad: telemetry never fails a scan.
        except Exception:
            logger.debug("No telemetry client for the storage scan", exc_info=True)
            return
        try:
            client.send_telemetry_event(SCAN_DONE, properties)
        finally:
            await client.aclose()

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


__all__ = [
    "SCAN_DONE",
    "ScanOutcome",
    "ScanTelemetry",
    "count_bucket",
    "duration_bucket",
    "scan_properties",
    "size_bucket",
]
