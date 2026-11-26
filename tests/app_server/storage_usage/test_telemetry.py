from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from tests.app_server.storage_usage._tree import Tree
from tests.stubs.background_worker import FakeBackgroundWorker
from vibe.app_server._background_worker_process import Job
from vibe.app_server._storage_usage.scan import SCAN_JOB, ScanRequest, scan
from vibe.app_server._storage_usage.service import StorageUsageService
from vibe.app_server._storage_usage.telemetry import (
    SCAN_DONE,
    ScanOutcome,
    duration_bucket,
    scan_properties,
)
from vibe.app_server._time import now_ms
from vibe.app_server.protocol import (
    StorageUsageReadResponse,
    StorageUsageRefreshParams,
    StorageUsageSnapshot,
)
from vibe.core.telemetry.send import TelemetryClient

_JOBS = "vibe.app_server._storage_usage.scan:storage_jobs"
_KEYS = {
    "status",
    "partial_reason",
    "time_limit_hit",
    "full",
    "total_bucket",
    "remeasured_worktrees_bucket",
    "duration_bucket",
    "platform",
}


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.sent = asyncio.Event()

    def send_telemetry_event(self, name: str, properties: dict[str, Any]) -> None:
        self.events.append((name, properties))
        self.sent.set()

    async def aclose(self) -> None:
        return None


def _service(tree: Tree, **overrides: Any) -> StorageUsageService:
    worker = FakeBackgroundWorker(**{
        _JOBS: {SCAN_JOB: Job(lambda args: scan(ScanRequest.from_args(args)))}
    })
    return StorageUsageService(
        worker,
        home=lambda: tree.home,
        worktrees_root=lambda: tree.worktrees_root,
        default_session_root=lambda: tree.sessions_root,
        clock_ms=now_ms,
        **overrides,
    )


async def _scan_event(
    tree: Tree, params: StorageUsageRefreshParams, **overrides: Any
) -> dict[str, Any]:
    service = _service(tree, **overrides)
    recorder = _Recorder()

    async def notify(_method: str, _params: Any) -> None:
        return None

    async def factory() -> TelemetryClient:
        return cast(TelemetryClient, recorder)

    service.listen(notify, factory)
    try:
        await service.refresh(params)
        await asyncio.wait_for(recorder.sent.wait(), 30)
    finally:
        await service.close()
    [(name, properties)] = recorder.events
    assert name == SCAN_DONE
    return properties


@pytest.mark.parametrize(
    ("seconds", "bucket"),
    [
        (3, "<5 s"),
        (200, "2–5 min"),
        (336, "5–8 min"),
        (599, "8–10 min"),
        (600, "10–15 min"),
        (1000, "15 min+"),
    ],
)
def test_duration_buckets_reach_past_the_ten_minute_limit(
    seconds: float, bucket: str
) -> None:
    """*Prepare*: Scan durations around and past the 10-minute limit.
    *Do*: Bucket them.
    *Assert*: A scan near the limit and one past it land in their own buckets.
    """
    # Do
    result = duration_bucket(seconds)

    # Assert
    assert result == bucket


@pytest.mark.asyncio
async def test_a_full_scan_cut_by_the_time_limit_says_so(tree: Tree) -> None:
    """*Prepare*: A worktree never measured and no time to measure it.
    *Do*: Refresh with ``full``.
    *Assert*: The event says partial because of the time limit, full, and
    nothing re-measured; it carries only aggregated properties.
    """
    # Prepare
    tree.session("secret-session-id", title="Secret title")
    tree.worktree("secret-branch", sessions=["secret-session-id"])

    # Do
    properties = await _scan_event(
        tree, StorageUsageRefreshParams(full=True), time_limit_s=0.0
    )

    # Assert
    assert set(properties) == _KEYS
    assert properties["status"] == "partial"
    assert properties["partial_reason"] == "time_limit"
    assert properties["time_limit_hit"] is True
    assert properties["full"] is True
    assert properties["remeasured_worktrees_bucket"] == "0"
    flattened = repr(properties)
    for private in ("secret", "Secret", str(tree.home)):
        assert private not in flattened


@pytest.mark.asyncio
async def test_an_incremental_scan_reports_what_it_re_measured(tree: Tree) -> None:
    """*Prepare*: Two worktrees, both measured once; then one changes signal.
    *Do*: Refresh again, not full.
    *Assert*: Incremental, up to date, one worktree re-measured, a duration.
    """
    # Prepare
    for name in ("a", "b"):
        tree.session(name)
        tree.worktree(name, sessions=[name])
    await _scan_event(tree, StorageUsageRefreshParams(full=True))
    tree.write_history("a")

    # Do
    properties = await _scan_event(tree, StorageUsageRefreshParams())

    # Assert
    assert properties["status"] == "up_to_date"
    assert properties["partial_reason"] is None
    assert properties["time_limit_hit"] is False
    assert properties["full"] is False
    assert properties["remeasured_worktrees_bucket"] == "1–10"
    assert properties["duration_bucket"] == "<5 s"


@pytest.mark.parametrize(
    ("time_limit_hit", "skipped", "reason"),
    [
        (False, 3, "unreadable"),
        (True, 0, "time_limit"),
        (True, 3, "time_limit_and_unreadable"),
        (False, 0, None),
    ],
)
def test_partial_scans_say_whether_the_limit_or_unreadable_entries_caused_it(
    time_limit_hit: bool, skipped: int, reason: str | None
) -> None:
    """*Prepare*: Scans cut by the limit, with unreadable entries, both, neither.
    *Do*: Build the event properties.
    *Assert*: ``partial_reason`` tells the causes apart.
    """
    # Prepare
    snapshot = StorageUsageSnapshot(
        scan_started_at=0, measured_at=0, skipped_entry_count=skipped
    )
    response = StorageUsageReadResponse(status="partial", snapshot=snapshot)
    outcome = ScanOutcome(
        duration_s=1.0, remeasured=0, time_limit_hit=time_limit_hit, full=False
    )

    # Do
    properties = scan_properties(response, outcome)

    # Assert
    assert properties["partial_reason"] == reason
    assert properties["time_limit_hit"] is time_limit_hit


def test_a_scan_that_never_ran_reports_failed_without_a_duration() -> None:
    """*Prepare*: A scan that failed before producing a result.
    *Do*: Build the event properties.
    *Assert*: Failed, no duration, nothing re-measured.
    """
    # Do
    properties = scan_properties(StorageUsageReadResponse(status="failed"), None)

    # Assert
    assert properties["status"] == "failed"
    assert properties["duration_bucket"] is None
    assert properties["remeasured_worktrees_bucket"] == "0"
