from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import os
from pathlib import Path
import sys
import threading

import pytest
import pytest_asyncio

from vibe.app_server._background_worker import (
    BackgroundJobError,
    BackgroundWorker,
    BackgroundWorkerClosedError,
    BackgroundWorkerDiedError,
    _default_command,
)
from vibe.app_server._background_worker_process import FLAG

_TEST_JOBS = "tests.stubs.background_worker_jobs:test_jobs"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _worker_imports_the_test_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    path = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        str(_PROJECT_ROOT) if not path else f"{_PROJECT_ROOT}{os.pathsep}{path}",
    )


@pytest_asyncio.fixture
async def worker() -> AsyncIterator[BackgroundWorker]:
    worker = BackgroundWorker()
    worker.add_jobs(_TEST_JOBS)
    yield worker
    await worker.close()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_a_job_runs_in_a_process_of_its_own(worker: BackgroundWorker) -> None:
    assert worker.pid is None

    pid = await worker.run("test.pid")

    # Not compared with worker.pid: on Windows a venv's python.exe is a
    # launcher that runs the interpreter as its child.
    assert worker.pid is not None
    assert pid != os.getpid()
    assert await worker.run("test.pid") == pid


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_waiting_for_a_job_leaves_the_event_loop_free(
    worker: BackgroundWorker,
) -> None:
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        await worker.run("test.sleep", 0.5)
    finally:
        ticker.cancel()

    # The spawn and the job together take well over 0.5 s.
    assert ticks >= 20


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_a_failed_job_reports_its_error_and_the_worker_lives_on(
    worker: BackgroundWorker,
) -> None:
    pid = await worker.run("test.pid")

    with pytest.raises(BackgroundJobError, match="ValueError: the job failed"):
        await worker.run("test.fail")
    with pytest.raises(BackgroundJobError, match="LookupError"):
        await worker.run("test.unknown")

    assert await worker.run("test.pid") == pid


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_a_request_or_answer_that_is_not_json_fails_alone(
    worker: BackgroundWorker,
) -> None:
    pid = await worker.run("test.pid")

    with pytest.raises(BackgroundJobError, match="TypeError"):
        await worker.run("test.pid", object())
    with pytest.raises(BackgroundJobError, match="TypeError"):
        await worker.run("test.unserializable")

    assert await worker.run("test.pid") == pid


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_an_answer_that_is_not_an_object_is_ignored() -> None:
    worker = BackgroundWorker(
        command=[
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('42\\n\"stray\"\\n'); sys.stdout.flush(); "
            "from vibe.app_server._background_worker_process import main; main()",
        ]
    )
    worker.add_jobs(_TEST_JOBS)
    try:
        assert await worker.run("test.pid") != os.getpid()
    finally:
        await worker.close()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_a_worker_that_died_fails_its_jobs_and_is_started_again(
    worker: BackgroundWorker,
) -> None:
    """*Prepare*: A running worker with a job in flight.
    *Do*: Crash the worker, then submit another job.
    *Assert*: The job in flight fails, the next runs in a new process.
    """
    # Prepare
    first = await worker.run("test.pid")
    in_flight = worker.submit("test.sleep", 30)

    # Do
    with pytest.raises(BackgroundWorkerDiedError):
        await worker.run("test.crash")
    second = await worker.run("test.pid")

    # Assert
    with pytest.raises(BackgroundWorkerDiedError):
        await asyncio.wrap_future(in_flight)
    assert second != first


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_a_worker_that_died_with_work_queues_its_recovery() -> None:
    recovered = threading.Event()
    worker = BackgroundWorker()
    worker.add_jobs(_TEST_JOBS, after_restart=recovered.set)
    try:
        with pytest.raises(BackgroundWorkerDiedError):
            await worker.run("test.crash")

        assert await asyncio.to_thread(recovered.wait, 10)
    finally:
        await worker.close()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_closing_stops_the_worker_and_fails_what_was_left(
    worker: BackgroundWorker,
) -> None:
    await worker.run("test.pid")
    in_flight = worker.submit("test.sleep", 30)

    await worker.close()

    with pytest.raises(BackgroundWorkerClosedError):
        await asyncio.wrap_future(in_flight)
    with pytest.raises(BackgroundWorkerClosedError):
        await worker.run("test.pid")
    assert worker.pid is None


@pytest.mark.asyncio
async def test_closing_a_worker_that_never_started_starts_nothing() -> None:
    worker = BackgroundWorker()
    worker.add_jobs(_TEST_JOBS)

    await worker.close()

    assert worker.pid is None


def test_a_frozen_build_runs_itself_as_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Vibe/vibe-app-server")

    assert _default_command() == ["/Applications/Vibe/vibe-app-server", FLAG]


@pytest.mark.asyncio
@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    "entry_point",
    [
        "vibe/app_server/entrypoint.py",
        "-c from vibe.acp.entrypoint import main; main()",
    ],
)
async def test_the_bundled_entry_points_serve_as_the_worker(entry_point: str) -> None:
    """What a frozen build runs: its own entry point, with the worker flag."""
    head = (
        entry_point.split(" ", 1)
        if entry_point.startswith("-c ")
        else [str(_PROJECT_ROOT / entry_point)]
    )
    worker = BackgroundWorker(command=[sys.executable, *head, FLAG])
    worker.add_jobs(_TEST_JOBS)
    try:
        assert await worker.run("test.pid") != os.getpid()
    finally:
        await worker.close()
