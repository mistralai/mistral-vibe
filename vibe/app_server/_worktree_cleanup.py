"""Removes archived sessions' worktrees off the request path, one worker per process."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading

from vibe.core.git.worktree import ManagedWorktree, WorktreeReleaseOutcome
from vibe.core.session.session_loader import SessionLoader
from vibe.observability.logging import logger

# None: every worktree a session joined.
type _Work = Path | None

_RETRY_DELAYS = (5.0, 30.0, 120.0)


def session_finished(session_dir: Path) -> bool:
    # Deleted or archived. An unreadable session keeps its worktree.
    if not session_dir.exists():
        return True
    try:
        return SessionLoader.load_metadata(session_dir).archived_at is not None
    except ValueError:
        return False


class WorktreeCleanup:
    def __init__(
        self,
        session_finished: Callable[[Path], bool] = session_finished,
        retry_delays: tuple[float, ...] = _RETRY_DELAYS,
    ) -> None:
        self._session_finished = session_finished
        self._retry_delays = retry_delays
        self._condition = threading.Condition()
        self._pending: dict[_Work, None] = {}
        self._busy = False
        self._worker: threading.Thread | None = None
        self._failures: dict[Path, int] = {}
        self._retries: dict[Path, threading.Timer] = {}

    def schedule(self, cwd: Path) -> None:
        managed = ManagedWorktree.at(cwd)
        if managed is None:
            return
        record = managed.claim.read()
        if record is None or not (record.session_dirs or record.reap_requested):
            return
        self._enqueue(managed.root)

    def sweep(self) -> None:
        self._enqueue(None)

    def wait_idle(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._pending and not self._busy and not self._retries,
                timeout,
            )

    def _enqueue(self, work: _Work) -> None:
        with self._condition:
            self._pending[work] = None
            if self._worker is not None:
                return
            self._worker = threading.Thread(
                target=self._run, name="vibe-worktree-cleanup", daemon=True
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            with self._condition:
                if not self._pending:
                    self._worker = None
                    self._condition.notify_all()
                    return
                work = next(iter(self._pending))
                del self._pending[work]
                self._busy = True
            try:
                if work is None:
                    ManagedWorktree.empty_trash()
                for managed in self._targets(work):
                    if not self._complete(managed):
                        self._retry_later(managed.root)
                        continue
                    with self._condition:
                        self._failures.pop(managed.root, None)
            # Broad: an escaping error would end the worker with work queued.
            except Exception as exc:
                logger.warning("Worktree cleanup failed", exc_info=exc)
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

    @staticmethod
    def _targets(work: _Work) -> tuple[ManagedWorktree, ...]:
        if work is None:
            return ManagedWorktree.joined()
        managed = ManagedWorktree.at(work)
        return () if managed is None else (managed,)

    def _complete(self, managed: ManagedWorktree) -> bool:
        # Broad: an escaping error would end the worker with work queued.
        try:
            release = managed.reap_if_finished(self._session_finished)
        except Exception as exc:
            logger.warning(
                "Keeping worktree %s for now: its reap failed",
                managed.root,
                exc_info=exc,
            )
            return False
        if release is None:
            return True
        logger.info("Reap of worktree %s finished: %s", managed.root, release.outcome)
        # Only a failed snapshot can succeed on retry.
        return release.outcome is not WorktreeReleaseOutcome.KEPT_DIRTY

    def _retry_later(self, root: Path) -> None:
        with self._condition:
            if root in self._retries:
                return
            attempt = self._failures.get(root, 0)
            if attempt >= len(self._retry_delays):
                self._failures.pop(root, None)
                logger.warning(
                    "Giving up on worktree %s until its next release or startup", root
                )
                return
            self._failures[root] = attempt + 1
            timer = threading.Timer(
                self._retry_delays[attempt], self._retry, args=(root,)
            )
            timer.daemon = True
            self._retries[root] = timer
            timer.start()

    def _retry(self, root: Path) -> None:
        with self._condition:
            self._retries.pop(root, None)
            self._enqueue(root)


_PROCESS_CLEANUP = WorktreeCleanup()


def worktree_cleanup() -> WorktreeCleanup:
    return _PROCESS_CLEANUP
