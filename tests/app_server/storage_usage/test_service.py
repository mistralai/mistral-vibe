from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import json
import os
import shutil
import threading
import time
from typing import Any

import pytest
import pytest_asyncio

from tests.app_server.storage_usage._tree import Tree, write
from tests.stubs.background_worker import FakeBackgroundWorker
from vibe.app_server._background_worker_process import Job
from vibe.app_server._model import ProtocolModel
from vibe.app_server._storage_usage import files
from vibe.app_server._storage_usage.scan import SCAN_JOB, ScanRequest, scan
from vibe.app_server._storage_usage.service import StorageUsageService
from vibe.app_server._time import now_ms
from vibe.app_server.protocol import (
    StorageUsageRefreshParams,
    StorageUsageUpdatedParams,
)

_JOBS = "vibe.app_server._storage_usage.scan:storage_jobs"


class _Gate:
    """Holds every scan job until opened, and counts the scans."""

    def __init__(self) -> None:
        self.open = threading.Event()
        self.open.set()
        self.started = threading.Event()
        self.requests: list[ScanRequest] = []

    def job(self, args: dict[str, Any]) -> dict[str, Any]:
        request = ScanRequest.from_args(args)
        self.requests.append(request)
        self.started.set()
        assert self.open.wait(30)
        return scan(request)


class _Updates:
    def __init__(self) -> None:
        self.items: list[StorageUsageUpdatedParams] = []
        self._changed = asyncio.Event()

    async def __call__(self, method: str, params: ProtocolModel) -> None:
        assert method == "storage/usage/updated"
        assert isinstance(params, StorageUsageUpdatedParams)
        self.items.append(params)
        self._changed.set()

    async def wait_for(
        self, predicate: Callable[[StorageUsageUpdatedParams], bool]
    ) -> StorageUsageUpdatedParams:
        async with asyncio.timeout(30):
            while True:
                for item in self.items:
                    if predicate(item):
                        return item
                self._changed.clear()
                await self._changed.wait()


def _done(item: StorageUsageUpdatedParams) -> bool:
    return item.status != "refreshing"


class _Clock:
    """Real time, moved forward by ``skip``: the stale marker has a real file time."""

    def __init__(self) -> None:
        self.offset = 0

    def skip(self, milliseconds: int) -> None:
        self.offset += milliseconds

    def __call__(self) -> int:
        return now_ms() + self.offset


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _service(
    tree: Tree, gate: _Gate, clock: _Clock, **overrides: Any
) -> StorageUsageService:
    worker = FakeBackgroundWorker(**{_JOBS: {SCAN_JOB: Job(gate.job)}})
    return StorageUsageService(
        worker,
        home=lambda: tree.home,
        worktrees_root=lambda: tree.worktrees_root,
        default_session_root=lambda: tree.sessions_root,
        clock_ms=clock,
        **({"lock_wait_s": 30.0, "removal_wait_s": 30.0} | overrides),
    )


@pytest_asyncio.fixture
async def services(
    tree: Tree, clock: _Clock
) -> AsyncIterator[Callable[..., StorageUsageService]]:
    built: list[StorageUsageService] = []

    def build(gate: _Gate | None = None, **overrides: Any) -> StorageUsageService:
        service = _service(tree, gate or _Gate(), clock, **overrides)
        built.append(service)
        return service

    yield build
    for service in built:
        await service.close()


async def _refreshed(
    service: StorageUsageService, updates: _Updates, **params: Any
) -> StorageUsageUpdatedParams:
    start = len(updates.items)
    response = await service.refresh(StorageUsageRefreshParams(**params))
    assert response.started, response
    return await updates.wait_for(
        lambda item: _done(item) and updates.items.index(item) >= start
    )


@pytest.mark.asyncio
async def test_no_numbers_before_the_first_scan(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A home with no cache.
    *Do*: Read.
    *Assert*: No snapshot, and the live disk figures.
    """
    # Prepare
    service = services()

    # Do
    response = await service.read()

    # Assert
    assert response.snapshot is None
    assert response.disk.total_bytes > 0
    assert response.disk.low_threshold_bytes > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content", ["{not json", json.dumps({"version": 99, "snapshot": {}})]
)
async def test_a_corrupt_or_unknown_version_cache_gives_no_numbers(
    tree: Tree, services: Callable[..., StorageUsageService], content: str
) -> None:
    """*Prepare*: A cache file that is not JSON, or of another version.
    *Do*: Read.
    *Assert*: No snapshot, as before the first scan.
    """
    # Prepare
    (tree.home / files.CACHE_FILENAME).write_text(content)
    service = services()

    # Do
    response = await service.read()

    # Assert
    assert response.snapshot is None


@pytest.mark.asyncio
async def test_a_refresh_scans_writes_the_cache_and_notifies(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: One session, a listener.
    *Do*: Refresh.
    *Assert*: "refreshing" first, then the result with increasing event ids,
    and the cache file holds it.
    """
    # Prepare
    tree.session("s", size=5000)
    service = services()
    updates = _Updates()
    service.listen(updates)

    # Do
    result = await _refreshed(service, updates)

    # Assert
    assert updates.items[0].status == "refreshing"
    assert result.snapshot is not None
    assert [row.session_id for row in result.snapshot.sessions] == ["s"]
    assert [item.event_id for item in updates.items] == sorted({
        item.event_id for item in updates.items
    })
    assert files.read_cache(tree.home, 1) is not None


@pytest.mark.asyncio
async def test_a_fresh_snapshot_is_not_scanned_again_unless_stale(
    tree: Tree, services: Callable[..., StorageUsageService], clock: _Clock
) -> None:
    """*Prepare*: A scan just finished.
    *Do*: Refresh with a 10-minute max age, then touch the stale marker and
    refresh again.
    *Assert*: The first is "fresh"; once stale, a scan starts.
    """
    # Prepare
    tree.session("s")
    service = services()
    updates = _Updates()
    service.listen(updates)
    await _refreshed(service, updates)
    clock.skip(60_000)

    # Do
    fresh = await service.refresh(StorageUsageRefreshParams(max_age_ms=600_000))
    time.sleep(0.01)
    files.touch_stale_marker(tree.home)
    read = await service.read()
    stale = await service.refresh(StorageUsageRefreshParams(max_age_ms=600_000))

    # Assert
    assert not fresh.started and fresh.reason == "fresh"
    assert read.stale
    assert stale.started


@pytest.mark.asyncio
async def test_two_processes_share_one_scan_and_the_newest_cache(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: Two services on one home, as two app-server processes are;
    A's scan held in its worker.
    *Do*: Refresh on A, then a plain refresh on B while A scans; let A finish.
    *Assert*: B answers "running_elsewhere" and forwards A's result; one scan
    ran; B reads the cache A wrote.
    """
    # Prepare
    tree.session("s", size=5000)
    gate = _Gate()
    gate.open.clear()
    first, second = services(gate), services(_Gate())
    first_updates, second_updates = _Updates(), _Updates()
    first.listen(first_updates)
    second.listen(second_updates)

    # Do
    started = await first.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(gate.started.wait, 30)
    elsewhere = await second.refresh(StorageUsageRefreshParams())
    refreshing = await second.read()
    gate.open.set()
    forwarded = await second_updates.wait_for(
        lambda item: _done(item) and item.snapshot is not None
    )

    # Assert
    assert started.started
    assert not elsewhere.started and elsewhere.reason == "running_elsewhere"
    assert refreshing.status == "refreshing"
    assert len(gate.requests) == 1
    assert forwarded.snapshot is not None
    assert [row.session_id for row in forwarded.snapshot.sessions] == ["s"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ask", [{"full": True}, {"wait_for_session_ids": ["s"]}])
async def test_full_and_wait_refreshes_wait_for_the_other_scan_then_run_their_own(
    tree: Tree, services: Callable[..., StorageUsageService], ask: dict[str, Any]
) -> None:
    """*Prepare*: A's scan held; an archive touches the stale marker meanwhile.
    *Do*: Ask B for a ``full`` or ``waitForSessionIds`` refresh, let A finish.
    *Assert*: B starts, waits for the lock, then runs its own scan, started
    after the archive, so its result is not stale.
    """
    # Prepare
    tree.session("s", size=5000)
    held = _Gate()
    held.open.clear()
    first = services(held)
    second_gate = _Gate()
    second = services(second_gate)
    second_updates = _Updates()
    second.listen(second_updates)
    await first.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(held.started.wait, 30)
    files.touch_stale_marker(tree.home)
    marker = files.stale_marker_ms(tree.home)

    # Do
    response = await second.refresh(StorageUsageRefreshParams(**ask))
    await asyncio.sleep(0.3)
    waited = not second_gate.requests
    held.open.set()
    result = await second_updates.wait_for(
        lambda item: _done(item) and item.snapshot is not None and not item.stale
    )

    # Assert
    assert response.started
    assert waited
    assert len(second_gate.requests) == 1
    assert second_gate.requests[0].full is bool(ask.get("full"))
    assert second_gate.requests[0].allow_daily_full is not bool(
        ask.get("wait_for_session_ids")
    )
    assert result.snapshot is not None
    assert result.snapshot.scan_started_at >= 0
    assert files.stale_marker_ms(tree.home) == marker


@pytest.mark.asyncio
async def test_a_waiting_refresh_fails_past_the_lock_wait(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan holding the lock longer than B may wait.
    *Do*: Ask B for a full refresh.
    *Assert*: B reports "failed".
    """
    # Prepare
    held = _Gate()
    held.open.clear()
    first = services(held)
    second = services(_Gate(), lock_wait_s=0.3)
    updates = _Updates()
    second.listen(updates)
    await first.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(held.started.wait, 30)

    # Do
    await second.refresh(StorageUsageRefreshParams(full=True))
    failed = await updates.wait_for(lambda item: item.status == "failed")
    held.open.set()

    # Assert
    assert failed.status == "failed"


@pytest.mark.asyncio
async def test_a_newer_cache_from_another_process_is_reloaded(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: B read A's first result; then A scans again with a new session.
    *Do*: B reads.
    *Assert*: B sees the new session without scanning.
    """
    # Prepare
    tree.session("first")
    first_gate = _Gate()
    first, second = services(first_gate), services(_Gate())
    updates = _Updates()
    first.listen(updates)
    await _refreshed(first, updates)
    before = await second.read()
    tree.session("second")
    time.sleep(0.01)
    await _refreshed(first, updates)

    # Do
    after = await second.read()

    # Assert
    assert before.snapshot is not None and after.snapshot is not None
    assert {row.session_id for row in before.snapshot.sessions} == {"first"}
    assert {row.session_id for row in after.snapshot.sessions} == {"first", "second"}


@pytest.mark.asyncio
async def test_a_refresh_waits_for_the_archived_sessions_worktree_to_go(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: An archived session whose worktree another thread removes
    after a moment, as the reaper of another process does.
    *Do*: Refresh with ``waitForSessionIds``.
    *Assert*: The scan ran after the folder was gone, ``complete`` is true.
    """
    # Prepare
    tree.session("s", archived=True)
    folder = tree.worktree("w", sessions=["s"], size=100_000)
    service = services()
    updates = _Updates()
    service.listen(updates)
    remover = threading.Timer(0.5, shutil.rmtree, args=(folder,))
    remover.start()

    # Do
    result = await _refreshed(service, updates, wait_for_session_ids=["s"])

    # Assert
    remover.join()
    assert result.complete
    assert result.snapshot is not None
    assert result.snapshot.worktrees == []


@pytest.mark.asyncio
async def test_past_the_removal_wait_the_scan_runs_anyway_incomplete(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: An archived session whose worktree stays.
    *Do*: Refresh with ``waitForSessionIds`` and a short removal wait.
    *Assert*: The scan runs, still counting the folder, with ``complete`` false.
    """
    # Prepare
    tree.session("s", archived=True)
    tree.worktree("w", sessions=["s"])
    service = services(removal_wait_s=0.3)
    updates = _Updates()
    service.listen(updates)

    # Do
    result = await _refreshed(service, updates, wait_for_session_ids=["s"])

    # Assert
    assert not result.complete
    assert result.snapshot is not None and len(result.snapshot.worktrees) == 1


@pytest.mark.asyncio
async def test_a_removal_after_the_scan_started_reads_stale(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan that counted a worktree.
    *Do*: The worktree's removal finishes and touches the marker.
    *Assert*: The next read is stale.
    """
    # Prepare
    tree.session("s")
    tree.worktree("w", sessions=["s"])
    service = services()
    updates = _Updates()
    service.listen(updates)
    await _refreshed(service, updates)
    time.sleep(0.01)

    # Do
    files.touch_stale_marker(tree.home)
    response = await service.read()

    # Assert
    assert response.stale


@pytest.mark.asyncio
async def test_read_answers_in_under_50_ms_with_300_sessions(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: 300 sessions, 30 worktrees and their lease files, scanned.
    *Do*: Read ten times, warm.
    *Assert*: A read takes under 50 ms.
    """
    # Prepare
    for index in range(300):
        tree.session(f"s{index:03d}", size=100, idle_days=index % 60)
        write(tree.sessions_root / "active" / f"s{index:03d}.lock", 1)
    for index in range(30):
        tree.worktree(f"w{index}", sessions=[f"s{index:03d}"], size=100)
    service = services()
    updates = _Updates()
    service.listen(updates)
    await _refreshed(service, updates)
    await service.read()

    # Do
    timings: list[float] = []
    responses = []
    for _ in range(10):
        started = time.perf_counter()
        responses.append(await service.read())
        timings.append(time.perf_counter() - started)
    response = responses[-1]

    # Assert
    assert response.snapshot is not None and len(response.snapshot.sessions) == 300
    # The fastest warm read: what a read costs, not how long a host running
    # the whole suite in parallel kept it waiting. Measured alone, the median
    # read takes about 15 ms on an M-series laptop.
    fastest = min(timings)
    assert fastest < 0.05, f"{fastest * 1000:.1f} ms"


@pytest.mark.asyncio
async def test_a_refresh_during_a_scan_that_becomes_stale_queues_one_more(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan held in the worker.
    *Do*: An archive touches the marker, two plain refreshes arrive, the scan ends.
    *Assert*: Exactly one more scan runs.
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    gate.open.clear()
    service = services(gate)
    updates = _Updates()
    service.listen(updates)
    await service.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(gate.started.wait, 30)

    # Do
    time.sleep(0.01)
    files.touch_stale_marker(tree.home)
    busy = [await service.refresh(StorageUsageRefreshParams()) for _ in range(2)]
    gate.open.set()
    await updates.wait_for(lambda item: _done(item) and not item.stale)

    # Assert
    assert all(item.reason == "already_running" for item in busy)
    assert len(gate.requests) == 2


@pytest.mark.asyncio
async def test_a_home_behind_a_symlink_counts_each_history_once(
    tree: Tree, clock: _Clock, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """*Prepare*: One session, and a service that reaches the home through a symlink.
    *Do*: Refresh.
    *Assert*: The history is in Active sessions only, not again in Other.
    """
    # Prepare
    tree.session("s", size=500_000)
    link = tmp_path_factory.mktemp("link") / "home"
    link.symlink_to(tree.home)
    gate = _Gate()
    worker = FakeBackgroundWorker(**{_JOBS: {SCAN_JOB: Job(gate.job)}})
    service = StorageUsageService(
        worker,
        home=lambda: link,
        worktrees_root=lambda: link / "worktrees",
        default_session_root=lambda: link / "logs" / "session",
        clock_ms=clock,
    )
    updates = _Updates()
    service.listen(updates)
    try:
        # Do
        result = await _refreshed(service, updates)
    finally:
        await service.close()

    # Assert
    assert result.snapshot is not None
    totals = result.snapshot.totals
    assert totals.active_sessions_bytes >= 500_000
    assert totals.other_vibe_data_bytes < 500_000


@pytest.mark.asyncio
async def test_reading_never_makes_a_starting_scan_think_another_runs(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A service with no scan running anywhere.
    *Do*: Read many times while refreshing from another service.
    *Assert*: The refresh starts and runs its own scan.
    """
    # Prepare
    tree.session("s")
    reader = services()
    gate = _Gate()
    refresher = services(gate)
    updates = _Updates()
    refresher.listen(updates)

    # Do
    reads = asyncio.gather(*(reader.read() for _ in range(20)))
    response = await refresher.refresh(StorageUsageRefreshParams())
    await reads
    await updates.wait_for(lambda item: _done(item) and item.snapshot is not None)

    # Assert
    assert response.started
    assert len(gate.requests) == 1
    assert not (tree.home / files.SCANNING_FILENAME).exists()


@pytest.mark.asyncio
async def test_a_scan_that_raises_reports_failed(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan job that raises something unexpected.
    *Do*: Refresh.
    *Assert*: "failed" follows "refreshing", and the next read says failed.
    """
    # Prepare
    gate = _Gate()

    def broken(_args: dict[str, Any]) -> dict[str, Any]:
        return {"version": 1, "snapshot": None}

    gate.job = broken  # type: ignore[method-assign]
    service = services(gate)
    updates = _Updates()
    service.listen(updates)

    # Do
    await service.refresh(StorageUsageRefreshParams())
    failed = await updates.wait_for(lambda item: item.status == "failed")
    read = await service.read()

    # Assert
    assert failed.status == "failed"
    assert read.status == "failed"


@pytest.mark.asyncio
async def test_a_marker_left_by_a_crashed_scanner_does_not_block_refreshes(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scanning marker naming a live process (this one, as a pid
    reused after the scanner crashed) with nobody holding the lock.
    *Do*: Read, then refresh.
    *Assert*: The refresh starts its own scan and the result is not "refreshing".
    """
    # Prepare
    tree.session("s")
    (tree.home / files.SCANNING_FILENAME).write_text(f"{os.getpid()} crashed-owner")
    gate = _Gate()
    service = services(gate)
    updates = _Updates()
    service.listen(updates)

    # Do
    response = await service.refresh(StorageUsageRefreshParams())
    result = await updates.wait_for(
        lambda item: _done(item) and item.snapshot is not None
    )

    # Assert
    assert response.started
    assert len(gate.requests) == 1
    assert result.status == "up_to_date"
    assert not files.scanning_elsewhere(tree.home, "someone-else")


def test_scanning_elsewhere_ignores_dead_and_own_scanners(tree: Tree) -> None:
    """*Prepare*: Markers for a dead pid, this service, and a live other owner.
    *Do*: Ask whether a scan runs elsewhere.
    *Assert*: Only the live other owner counts.
    """
    marker = tree.home / files.SCANNING_FILENAME
    results = []
    for content in ("999999999 other", f"{os.getpid()} me", f"{os.getpid()} other"):
        marker.write_text(content)
        results.append(files.scanning_elsewhere(tree.home, "me"))

    assert results == [False, False, True]


@pytest.mark.asyncio
async def test_a_cache_from_a_newer_version_keeps_the_rows_it_can_read(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scanned cache, then rows and values only a newer Vibe knows.
    *Do*: Read it from this version.
    *Assert*: The snapshot is kept, minus the unknown rows; the unknown size
    kind reads as allocated.
    """
    # Prepare
    tree.session("s", size=5000)
    service = services()
    updates = _Updates()
    service.listen(updates)
    await _refreshed(service, updates)
    path = tree.home / files.CACHE_FILENAME
    document = json.loads(path.read_text())
    document["snapshot"]["other"].append({"kind": "new_kind", "path": "/x", "bytes": 1})
    document["snapshot"]["sizeKind"] = "compressed"
    document["snapshot"]["newField"] = {"nested": True}
    document["snapshot"]["sessions"][0]["newRowField"] = 1
    document["snapshot"]["totals"]["newTotal"] = 1
    time.sleep(0.01)
    path.write_text(json.dumps(document))

    # Do
    response = await service.read()

    # Assert
    assert response.snapshot is not None
    assert [row.session_id for row in response.snapshot.sessions] == ["s"]
    assert all(row.kind != "new_kind" for row in response.snapshot.other)
    assert response.snapshot.size_kind == "allocated"


@pytest.mark.asyncio
async def test_refreshes_arriving_together_start_one_scan(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: One service, its scan held in the worker.
    *Do*: Two connections refresh at the same moment.
    *Assert*: One starts, the other is told a scan is already running.
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    gate.open.clear()
    service = services(gate)

    # Do
    responses = await asyncio.gather(
        service.refresh(StorageUsageRefreshParams()),
        service.refresh(StorageUsageRefreshParams()),
    )
    await asyncio.to_thread(gate.started.wait, 30)
    gate.open.set()

    # Assert
    assert sorted(item.started for item in responses) == [False, True]
    assert [item.reason for item in responses if not item.started] == [
        "already_running"
    ]


@pytest.mark.asyncio
async def test_a_newer_good_cache_clears_a_failed_status(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A's scan failed; then B scans successfully.
    *Do*: A reads.
    *Assert*: A reports B's result, not "failed".
    """
    # Prepare
    tree.session("s")
    broken = _Gate()

    def fail(_args: dict[str, Any]) -> dict[str, Any]:
        raise OSError("disk went away")

    broken.job = fail  # type: ignore[method-assign]
    first, second = services(broken), services(_Gate())
    first_updates, second_updates = _Updates(), _Updates()
    first.listen(first_updates)
    second.listen(second_updates)
    await first.refresh(StorageUsageRefreshParams())
    await first_updates.wait_for(lambda item: item.status == "failed")
    await _refreshed(second, second_updates)

    # Do
    read = await first.read()

    # Assert
    assert read.status == "up_to_date"
    assert read.snapshot is not None


@pytest.mark.asyncio
async def test_a_started_scan_names_the_events_that_belong_to_it(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: One session, a listener.
    *Do*: Refresh twice in a row.
    *Assert*: Each refresh returns a new scan id, and the scan's "refreshing"
    and result events carry exactly that id.
    """
    # Prepare
    tree.session("s")
    service = services()
    updates = _Updates()
    service.listen(updates)

    # Do
    first = await service.refresh(StorageUsageRefreshParams())
    await updates.wait_for(lambda item: _done(item) and item.scan_id == first.scan_id)
    second = await service.refresh(StorageUsageRefreshParams(full=True))
    await updates.wait_for(lambda item: _done(item) and item.scan_id == second.scan_id)

    # Assert
    assert first.started and second.started
    assert first.scan_id is not None and second.scan_id is not None
    assert second.scan_id > first.scan_id
    by_scan = {
        scan_id: [item.status for item in updates.items if item.scan_id == scan_id]
        for scan_id in (first.scan_id, second.scan_id)
    }
    assert by_scan == {
        first.scan_id: ["refreshing", "up_to_date"],
        second.scan_id: ["refreshing", "up_to_date"],
    }


@pytest.mark.asyncio
async def test_a_failure_carries_the_id_of_the_scan_that_failed(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A first scan that fails late, while a second waits behind it.
    *Do*: Start the first, ask for a full refresh meanwhile, let the first fail.
    *Assert*: The failure carries the first scan's id, and the full refresh's
    own id gets its own result, so a client waiting for it never takes the
    old failure for its answer.
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    gate.open.clear()
    calls: list[int] = []

    def fail_first(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            assert gate.open.wait(30)
            raise OSError("disk went away")
        return scan(ScanRequest.from_args(args))

    gate.job = fail_first  # type: ignore[method-assign]
    service = services(gate)
    updates = _Updates()
    service.listen(updates)
    first = await service.refresh(StorageUsageRefreshParams())

    # Do
    measuring = await service.refresh(StorageUsageRefreshParams(full=True))
    gate.open.set()
    answer = await updates.wait_for(
        lambda item: _done(item) and item.scan_id == measuring.scan_id
    )

    # Assert
    assert first.started and first.scan_id is not None
    assert not measuring.started and measuring.reason == "already_running"
    assert measuring.scan_id is not None and measuring.scan_id != first.scan_id
    failures = [item for item in updates.items if item.status == "failed"]
    assert [item.scan_id for item in failures] == [first.scan_id]
    assert answer.status == "up_to_date"


@pytest.mark.asyncio
async def test_a_plain_refresh_during_a_scan_gets_that_scans_id(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan held in the worker.
    *Do*: A plain refresh arrives.
    *Assert*: It is answered by the scan under way: same id.
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    gate.open.clear()
    service = services(gate)
    updates = _Updates()
    service.listen(updates)
    running = await service.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(gate.started.wait, 30)

    # Do
    joined = await service.refresh(StorageUsageRefreshParams())
    gate.open.set()
    await updates.wait_for(lambda item: _done(item) and item.scan_id == running.scan_id)

    # Assert
    assert joined.reason == "already_running"
    assert joined.scan_id == running.scan_id


@pytest.mark.asyncio
async def test_a_scan_followed_in_another_process_is_named_by_the_follower(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: Another process's scan held in its worker.
    *Do*: A plain refresh here.
    *Assert*: "running_elsewhere" with an id, and the forwarded result carries it.
    """
    # Prepare
    tree.session("s")
    held = _Gate()
    held.open.clear()
    other, here = services(held), services(_Gate())
    updates = _Updates()
    here.listen(updates)
    await other.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(held.started.wait, 30)

    # Do
    response = await here.refresh(StorageUsageRefreshParams())
    held.open.set()
    forwarded = await updates.wait_for(
        lambda item: _done(item) and item.scan_id == response.scan_id
    )

    # Assert
    assert response.reason == "running_elsewhere"
    assert response.scan_id is not None
    assert forwarded.snapshot is not None


@pytest.mark.asyncio
async def test_a_fresh_answer_names_no_scan(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan just finished.
    *Do*: Refresh with a 10-minute max age.
    *Assert*: "fresh", and no scan id: no event will answer it.
    """
    # Prepare
    tree.session("s")
    service = services()
    updates = _Updates()
    service.listen(updates)
    await _refreshed(service, updates)

    # Do
    response = await service.refresh(StorageUsageRefreshParams(max_age_ms=600_000))

    # Assert
    assert response.reason == "fresh"
    assert response.scan_id is None
    assert "scanId" not in response.model_dump(by_alias=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("ask", [{"wait_for_session_ids": ["s"]}, {"full": True}])
async def test_a_refresh_after_an_archive_names_a_scan_started_after_it(
    tree: Tree,
    services: Callable[..., StorageUsageService],
    clock: _Clock,
    ask: dict[str, Any],
) -> None:
    """*Prepare*: A scan under way that started before an archive.
    *Do*: The archive's ``waitForSessionIds`` (or ``full``) refresh arrives.
    *Assert*: It names another scan, which starts after the call: its
    snapshot's scan start is later than the call.
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    gate.open.clear()
    service = services(gate)
    updates = _Updates()
    service.listen(updates)
    before = await service.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(gate.started.wait, 30)
    called_at = clock()

    # Do
    after = await service.refresh(StorageUsageRefreshParams(**ask))
    gate.open.set()
    answer = await updates.wait_for(
        lambda item: _done(item) and item.scan_id == after.scan_id
    )

    # Assert
    assert after.reason == "already_running"
    assert after.scan_id not in {None, before.scan_id}
    assert answer.snapshot is not None
    assert answer.snapshot.scan_started_at >= called_at


@pytest.mark.asyncio
async def test_a_followed_result_ends_its_scan_id_while_a_local_scan_runs(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: Another process scans; this one follows it and starts a
    full scan of its own, held in its worker once the other lock is free.
    *Do*: The other scan ends while this process's own scan still runs.
    *Assert*: The followed id gets a settled result, not "refreshing"; the
    own scan's id gets its own result later.
    """
    # Prepare
    tree.session("s")
    held = _Gate()
    held.open.clear()
    own_gate = _Gate()
    own_gate.open.clear()
    other, here = services(held), services(own_gate)
    updates = _Updates()
    here.listen(updates)
    await other.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(held.started.wait, 30)
    followed = await here.refresh(StorageUsageRefreshParams())
    own = await here.refresh(StorageUsageRefreshParams(full=True))

    # Do
    held.open.set()
    answer = await updates.wait_for(
        lambda item: _done(item) and item.scan_id == followed.scan_id
    )
    still_scanning = not own_gate.open.is_set()
    own_gate.open.set()
    own_answer = await updates.wait_for(
        lambda item: _done(item) and item.scan_id == own.scan_id
    )

    # Assert
    assert followed.reason == "running_elsewhere"
    assert own.started and own.scan_id != followed.scan_id
    assert still_scanning
    assert answer.status == "up_to_date"
    assert own_answer.status == "up_to_date"


@pytest.mark.asyncio
async def test_a_scan_id_handed_to_the_follower_while_it_publishes_still_settles(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: Another process scans; this one follows it.
    *Do*: While the follower publishes its settled result, another scan of
    this process finds the lock held and hands the follower its id.
    *Assert*: That id gets a settled result too.
    """
    # Prepare
    tree.session("s")
    held = _Gate()
    held.open.clear()
    other, here = services(held), services(_Gate())
    updates = _Updates()
    here.listen(updates)
    await other.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(held.started.wait, 30)
    followed = await here.refresh(StorageUsageRefreshParams())
    assert followed.scan_id is not None
    late: list[int] = []

    async def hand_over_while_publishing(method: str, params: ProtocolModel) -> None:
        assert isinstance(params, StorageUsageUpdatedParams)
        if params.scan_id == followed.scan_id and _done(params) and not late:
            # As a scan of ours that found the lock held, mid-publish.
            late.append(here._follow_elsewhere(None, here._new_scan_id()))
            await asyncio.sleep(0)

    here.listen(hand_over_while_publishing)

    # Do
    held.open.set()
    await updates.wait_for(
        lambda item: _done(item) and item.scan_id == followed.scan_id
    )

    # Assert
    answer = await updates.wait_for(
        lambda item: bool(late) and _done(item) and item.scan_id == late[0]
    )
    assert answer.status == "up_to_date"


@pytest.mark.asyncio
async def test_a_follow_up_scan_that_finds_nothing_stale_still_answers_its_id(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan held in the worker; a plain refresh joins it.
    *Do*: Let the scan end, nothing stale meanwhile.
    *Assert*: The follow-up the joined refresh left settles under a new id
    without a second walk, and the service is idle only after it.
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    gate.open.clear()
    service = services(gate)
    updates = _Updates()
    service.listen(updates)
    started = await service.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(gate.started.wait, 30)
    await service.refresh(StorageUsageRefreshParams())

    # Do
    gate.open.set()
    answer = await updates.wait_for(
        lambda item: (
            _done(item)
            and item.scan_id is not None
            and started.scan_id is not None
            and item.scan_id > started.scan_id
        )
    )
    await asyncio.wait_for(_idle(service), 30)

    # Assert
    assert answer.status == "up_to_date"
    assert len(gate.requests) == 1


async def _idle(service: StorageUsageService) -> None:
    while service._running():
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_refresh_between_a_scan_and_its_follow_up_joins_the_follow_up(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan held in the worker; a waiting refresh queues a
    follow-up behind it.
    *Do*: Let the scan end, then refresh as the follow-up starts.
    *Assert*: The refresh is told a scan is already running (the follow-up),
    and no third scan starts.
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    gate.open.clear()
    service = services(gate)
    updates = _Updates()
    service.listen(updates)
    first = await service.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(gate.started.wait, 30)
    queued = await service.refresh(StorageUsageRefreshParams(full=True))
    gate.started.clear()

    # Do
    gate.open.set()
    await updates.wait_for(lambda item: _done(item) and item.scan_id == first.scan_id)
    later = await service.refresh(StorageUsageRefreshParams())
    await updates.wait_for(lambda item: _done(item) and item.scan_id == queued.scan_id)
    await asyncio.wait_for(_idle(service), 30)

    # Assert
    assert queued.reason == "already_running"
    assert later.reason == "already_running"
    assert later.scan_id == queued.scan_id
    assert len(gate.requests) == 2


@pytest.mark.asyncio
async def test_a_scan_past_its_time_limit_and_margin_fails_and_frees_the_lock(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A scan job that never answers, as a walk stuck in one folder.
    *Do*: Refresh with a short time limit and margin.
    *Assert*: The scan's id ends "failed" and the scan lock is free again,
    while the job still runs.
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    gate.open.clear()
    service = services(gate, time_limit_s=0.2, scan_margin_s=0.2)
    updates = _Updates()
    service.listen(updates)

    # Do
    response = await service.refresh(StorageUsageRefreshParams())
    failed = await updates.wait_for(
        lambda item: _done(item) and item.scan_id == response.scan_id
    )
    lock = files.ScanLock(tree.home, "test")
    free = await asyncio.to_thread(lock.acquire, time.monotonic() + 5)
    lock.release()
    still_running = not gate.open.is_set()
    gate.open.set()

    # Assert
    assert failed.status == "failed"
    assert free
    assert still_running
    assert gate.requests[0].submitted_at is not None


@pytest.mark.asyncio
async def test_a_followed_scan_that_writes_no_cache_is_reported_failed(
    tree: Tree, services: Callable[..., StorageUsageService]
) -> None:
    """*Prepare*: A cache from an earlier scan; another process's next scan
    held in its worker, then failing.
    *Do*: A plain refresh here follows it; the other scan fails.
    *Assert*: The followed id ends "failed", not the old snapshot as
    "up_to_date".
    """
    # Prepare
    tree.session("s")
    gate = _Gate()
    real_job = gate.job

    def first_succeeds(args: dict[str, Any]) -> dict[str, Any]:
        document = real_job(args)
        if len(gate.requests) > 1:
            raise RuntimeError("scan failed")
        return document

    gate.job = first_succeeds  # type: ignore[method-assign]
    other = services(gate)
    other_updates = _Updates()
    other.listen(other_updates)
    await _refreshed(other, other_updates)
    gate.open.clear()
    gate.started.clear()
    here = services(_Gate())
    updates = _Updates()
    here.listen(updates)
    await other.refresh(StorageUsageRefreshParams())
    await asyncio.to_thread(gate.started.wait, 30)

    # Do
    response = await here.refresh(StorageUsageRefreshParams())
    gate.open.set()
    answer = await updates.wait_for(
        lambda item: _done(item) and item.scan_id == response.scan_id
    )

    # Assert
    assert response.reason == "running_elsewhere"
    assert answer.status == "failed"
    assert answer.snapshot is not None
