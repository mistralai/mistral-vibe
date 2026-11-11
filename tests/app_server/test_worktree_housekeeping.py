from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import os
from pathlib import Path
import signal
import threading
from typing import Any

from git import Repo
import pytest
import pytest_asyncio

from tests.stubs.background_worker import FakeBackgroundWorker
from vibe.app_server._background_worker import BackgroundWorker
from vibe.app_server._background_worker_process import Job
from vibe.app_server._worktree_housekeeping import WorktreeHousekeeping
from vibe.core.git.worktree import ManagedWorktree, WorktreeRepository

_JOBS = "vibe.app_server._worktree_cleanup:worktree_jobs"


def _init_repo(root: Path) -> None:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    repo.close()


def _finished_worktree(root: Path, name: str) -> Path:
    with WorktreeRepository.open(root) as repository:
        worktree = repository.prepare_auto(suggested_name=name)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold(name, session_dir=root / "deleted-sessions" / name)
    managed.release_holder(name)
    return worktree.root


@pytest_asyncio.fixture
async def worker() -> AsyncIterator[BackgroundWorker]:
    worker = BackgroundWorker()
    yield worker
    await worker.close()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_a_released_worktree_is_reaped_by_the_worker_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: BackgroundWorker
) -> None:
    """*Prepare*: A worktree whose session is gone, and a reap that would fail
    if it ran in this process.
    *Do*: Schedule its reap.
    *Assert*: The worker process removes it.
    """
    # Prepare
    _init_repo(tmp_path)
    root = _finished_worktree(tmp_path, "finished")

    def in_this_process(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("reaped in the app-server process")

    monkeypatch.setattr(ManagedWorktree, "reap_if_finished", in_this_process)
    housekeeping = WorktreeHousekeeping(worker)

    # Do
    housekeeping.schedule_reap(root)

    # Assert
    assert await asyncio.to_thread(housekeeping.wait_idle, 30)
    assert not root.exists()
    assert worker.pid is not None and worker.pid != os.getpid()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_a_worker_that_died_sweeps_again_once_restarted(
    tmp_path: Path, worker: BackgroundWorker
) -> None:
    """*Prepare*: A worktree whose session is gone, and a worker that dies.
    *Do*: Let the worker restart.
    *Assert*: Its recovery sweep removes the worktree nobody asked for again.
    """
    # Prepare
    _init_repo(tmp_path)
    housekeeping = WorktreeHousekeeping(worker)
    await housekeeping.prune(100)
    root = _finished_worktree(tmp_path, "orphaned")
    pid = worker.pid
    assert pid is not None

    # Do
    os.kill(pid, signal.SIGTERM)

    # Assert
    async def removed() -> None:
        while root.exists():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(removed(), 30)
    assert worker.pid != pid


@pytest.mark.asyncio
async def test_prunes_asked_for_while_one_runs_share_the_next() -> None:
    """*Prepare*: A prune in progress.
    *Do*: Ask for five more meanwhile.
    *Assert*: One more prune runs, at the last limit asked, and all five get
    its answer.
    """
    # Prepare
    started = threading.Event()
    release = threading.Event()
    limits: list[int] = []

    def prune(args: dict[str, Any]) -> int:
        limits.append(args["limit"])
        started.set()
        release.wait(10)
        return len(limits)

    worker = FakeBackgroundWorker(**{_JOBS: {"worktrees.prune": Job(prune)}})
    housekeeping = WorktreeHousekeeping(worker)
    first = asyncio.create_task(housekeeping.prune(1))
    assert await asyncio.to_thread(started.wait, 10)

    # Do
    later = [asyncio.create_task(housekeeping.prune(limit)) for limit in range(2, 7)]
    await asyncio.sleep(0)
    release.set()

    # Assert
    assert await first == 1
    assert await asyncio.gather(*later) == [2] * 5
    assert limits == [1, 6]
    worker.close_now()


@pytest.mark.asyncio
async def test_a_cancelled_prune_leaves_the_others_waiting_with_it_answered() -> None:
    """*Prepare*: A prune in progress, and two requests sharing the next.
    *Do*: Cancel the running request and one of the next ones.
    *Assert*: The other request still gets the next prune's answer.
    """
    # Prepare
    release = threading.Event()
    started = threading.Event()
    runs: list[int] = []

    def prune(args: dict[str, Any]) -> int:
        runs.append(args["limit"])
        started.set()
        release.wait(10)
        return len(runs)

    worker = FakeBackgroundWorker(**{_JOBS: {"worktrees.prune": Job(prune)}})
    housekeeping = WorktreeHousekeeping(worker)
    running = asyncio.create_task(housekeeping.prune(1))
    assert await asyncio.to_thread(started.wait, 10)
    cancelled = asyncio.create_task(housekeeping.prune(2))
    kept = asyncio.create_task(housekeeping.prune(3))
    await asyncio.sleep(0)

    # Do
    running.cancel()
    cancelled.cancel()
    await asyncio.sleep(0)
    release.set()

    # Assert
    assert await asyncio.wait_for(kept, 10) == 2
    assert runs == [1, 3]
    worker.close_now()
