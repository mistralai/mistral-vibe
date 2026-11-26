"""Worktree housekeeping as the app server asks for it, run by the worker.

Reaps, sweeps and retention pruning run in the housekeeping worker process
(``_worktree_cleanup.worktree_jobs``); this side only names the work. The
worker remembers queued reaps, their retries, and the broken checkouts it
reported, so a worker that died is swept once restarted.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any

from vibe.app_server._background_worker import BackgroundWorker
from vibe.app_server._worktree_cleanup import worktree_jobs
from vibe.core.git.worktree import ManagedWorktree
from vibe.core.git.worktree.record import CLAIMS_DIR_NAME, TRASH_DIR_NAME
from vibe.core.paths import WORKTREES_DIR
from vibe.core.trusted_folders import TrustedFoldersManager, trusted_folders_manager

# Imported, not only named, so a frozen build bundles the module the worker loads.
_JOBS = f"{worktree_jobs.__module__}:{worktree_jobs.__name__}"


@dataclass(slots=True)
class _Prune:
    limit: int
    trust: dict[str, list[str]]
    future: Future[int] = field(default_factory=Future)


class WorktreeHousekeeping:
    def __init__(
        self,
        worker: BackgroundWorker,
        *,
        trust_store: TrustedFoldersManager | None = None,
    ) -> None:
        self._worker = worker
        self._trust_store = trust_store or trusted_folders_manager
        self._lock = threading.Lock()
        self._running: _Prune | None = None
        self._next: _Prune | None = None
        self._used = False
        worker.add_jobs(_JOBS, after_restart=self.sweep)

    def schedule_reap(
        self, cwd: Path, *, trust_store: TrustedFoldersManager | None = None
    ) -> None:
        if ManagedWorktree.at(cwd) is None:
            return
        self._post("worktrees.reap", {"cwd": str(cwd)}, trust_store)

    def sweep(self, *, trust_store: TrustedFoldersManager | None = None) -> None:
        self._post("worktrees.sweep", {}, trust_store)

    async def prune(
        self, limit: int, *, trust_store: TrustedFoldersManager | None = None
    ) -> int:
        """Remove the oldest worktrees over ``limit``; how many it removed.

        A request made while a prune runs joins the one after it, which every
        request made meanwhile shares: it still sees what changed before it
        was made, and a burst of requests costs two prunes, not one each.
        """
        trust = self._trust(trust_store)
        with self._lock:
            self._used = True
            if self._next is None:
                self._next = _Prune(limit, trust)
            else:
                self._next.limit, self._next.trust = limit, trust
            prune = self._next
            start = self._running is None
        if start:
            self._start_next()
        # Shielded: the prune is shared, so one caller giving up must not
        # cancel it for the others.
        return await asyncio.shield(asyncio.wrap_future(prune.future))

    def wait_idle(self, timeout: float) -> bool:
        with self._lock:
            if not self._used:
                return True
        idle: Future[Any] = self._worker.submit(
            "worktrees.wait_idle", {"timeout": timeout}
        )
        return bool(idle.result(timeout + 5))

    def _start_next(self) -> None:
        with self._lock:
            prune = self._next
            if prune is None or self._running is not None:
                return
            self._next = None
            self._running = prune
        job = self._worker.submit(
            "worktrees.prune", {"limit": prune.limit, "trust": prune.trust}
        )

        def settle(done: Future[Any]) -> None:
            with self._lock:
                self._running = None
            try:
                error = done.exception()
                if error is not None:
                    prune.future.set_exception(error)
                else:
                    prune.future.set_result(done.result())
            except InvalidStateError:
                pass
            finally:
                self._start_next()

        job.add_done_callback(settle)

    def _post(
        self, job: str, args: dict[str, Any], trust_store: TrustedFoldersManager | None
    ) -> None:
        with self._lock:
            self._used = True
        self._worker.post(job, {**args, "trust": self._trust(trust_store)})

    def _trust(self, trust_store: TrustedFoldersManager | None) -> dict[str, list[str]]:
        # Persisted decisions only: a session's temporary grant never
        # authorizes background work, as before the move.
        return (trust_store or self._trust_store).persisted_decisions()


def has_managed_worktrees() -> bool:
    """Whether there is anything a sweep could do: a process that never made
    a worktree starts no worker.
    """
    root = WORKTREES_DIR.path
    try:
        if any(entry.is_dir() for entry in (root / CLAIMS_DIR_NAME).iterdir()):
            return True
    except OSError:
        pass
    try:
        return any((root / TRASH_DIR_NAME).iterdir())
    except OSError:
        return False


__all__ = ["WorktreeHousekeeping", "has_managed_worktrees"]
