"""The background worker, run in this process.

For tests that patch what a job calls (a paused snapshot, a failing reap):
the jobs run on threads here, so the patches reach them. Tests of the worker
process itself use ``BackgroundWorker``.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import importlib
from typing import Any

from vibe.app_server._background_worker import (
    BackgroundJobError,
    BackgroundWorker,
    BackgroundWorkerClosedError,
)
from vibe.app_server._background_worker_process import Job
from vibe.app_server._worktree_cleanup import WorktreeCleanup, worktree_jobs
from vibe.app_server._worktree_housekeeping import WorktreeHousekeeping


class FakeBackgroundWorker(BackgroundWorker):
    def __init__(self, **job_sets: Any) -> None:
        super().__init__()
        self._jobs: dict[str, Job] | None = None
        self._job_overrides = job_sets
        self._pool = ThreadPoolExecutor(4, thread_name_prefix="fake-background")
        self.submitted: list[str] = []

    def submit(self, job: str, args: Any = None) -> Future[Any]:
        self.submitted.append(job)
        future: Future[Any] = Future()
        if self._closed:
            future.set_exception(BackgroundWorkerClosedError())
            return future
        found = self._load().get(job)
        if found is None:
            future.set_exception(LookupError(job))
            return future

        def run() -> None:
            try:
                future.set_result(found.run(args))
            except Exception as exc:
                future.set_exception(
                    BackgroundJobError(job, type(exc).__name__, str(exc))
                )

        if found.inline:
            run()
        else:
            self._pool.submit(run)
        return future

    def close_now(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=True)

    def _load(self) -> dict[str, Job]:
        if self._jobs is None:
            self._jobs = {}
            for spec in self._job_sets:
                if spec in self._job_overrides:
                    self._jobs.update(self._job_overrides[spec])
                    continue
                module, _, attribute = spec.partition(":")
                self._jobs.update(getattr(importlib.import_module(module), attribute)())
        return self._jobs


def in_process_worktree_housekeeping(
    cleanup: WorktreeCleanup | None = None,
) -> WorktreeHousekeeping:
    """Worktree housekeeping whose jobs run here, through ``cleanup``."""
    worker = FakeBackgroundWorker(**{
        "vibe.app_server._worktree_cleanup:worktree_jobs": worktree_jobs(cleanup)
    })
    return WorktreeHousekeeping(worker)


__all__ = ["FakeBackgroundWorker", "in_process_worktree_housekeeping"]
