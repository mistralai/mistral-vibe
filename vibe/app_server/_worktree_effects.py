from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectResultDisplay,
    FailedEffectState,
    PublicError,
    RunningEffectState,
    WorktreeEffectDetail,
    WorktreeEffectInput,
    WorktreeEffectProgress,
)
from vibe.core.git.worktree import PreparedWorktree, WorktreeCreationProgress
from vibe.core.types import WorktreeContext
from vibe.observability.logging import logger

# The shortest gap between two published updates of a worktree's creation
# progress. Every update republishes the session's state, and git reports each
# percent, far more often than anyone reads; the reports in between collapse
# into the latest.
_PROGRESS_INTERVAL_SECONDS = 0.3


@dataclass(frozen=True)
class WorktreeEffect:
    """The settled transcript entry for a session-start worktree."""

    name: str
    branch: str
    path: str
    was_created: bool

    @classmethod
    def created(cls, worktree: PreparedWorktree) -> WorktreeEffect:
        return cls(
            name=worktree.name,
            branch=worktree.branch,
            path=str(worktree.root),
            was_created=True,
        )

    @classmethod
    def resolved(cls, worktree: PreparedWorktree) -> WorktreeEffect:
        return cls(
            name=worktree.name,
            branch=worktree.branch,
            path=str(worktree.root),
            was_created=worktree.created,
        )

    # Rebuilt from the session log rather than from the worktree, which by now
    # may be gone: the entry records what happened, not what still exists.
    @classmethod
    def restored(cls, context: WorktreeContext) -> WorktreeEffect:
        return cls(
            name=context.name,
            branch=context.branch,
            path=context.path,
            was_created=True,
        )

    @property
    def detail(self) -> WorktreeEffectDetail:
        present_verb = "Creating" if self.was_created else "Reusing"
        settled_verb = "Created" if self.was_created else "Reused"
        return WorktreeEffectDetail(
            tool_name="worktree",
            input=WorktreeEffectInput(
                name=self.name, branch=self.branch, path=self.path
            ),
            display=EffectCallDisplay(
                summary=f"worktree: {self.name}",
                verb=present_verb,
                message=self.name,
                settled_verb=settled_verb,
                settled_message=self._settled,
                status_text=f"{present_verb} worktree",
            ),
        )

    # Only ever a completed resolution: the worktree exists before this renders,
    # and a failure never reaches here because session/start raises before the
    # session exists, leaving no transcript to report into.
    #
    # No output either - the branch and path are already in detail.input, and
    # the runner renders a completed effect's output as raw JSON without one.
    @property
    def state(self) -> CompletedEffectState:
        return CompletedEffectState(
            display=EffectResultDisplay(
                success=True,
                verb="Created" if self.was_created else "Reused",
                message=self._settled,
            )
        )

    @property
    def _settled(self) -> str:
        return f"{self.name} on {self.branch}"


class WorktreeProgressFeed:
    """The latest progress of a worktree being created, for the loop to read.

    Reports arrive from the thread running git as well as from the loop, so
    they are handed over with call_soon_threadsafe and only ever read on the
    loop. Only the latest one matters: a reader that falls behind skips the
    reports in between rather than replaying them.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._latest: WorktreeCreationProgress | None = None
        self._changed = asyncio.Event()

    @property
    def latest(self) -> WorktreeCreationProgress | None:
        return self._latest

    def report(self, progress: WorktreeCreationProgress) -> None:
        self._loop.call_soon_threadsafe(self._set, progress)

    async def wait_for_change(self) -> None:
        await self._changed.wait()
        self._changed.clear()

    def _set(self, progress: WorktreeCreationProgress) -> None:
        self._latest = progress
        self._changed.set()


@dataclass(frozen=True)
class WorktreeProgress:
    """The visible step while a requested worktree is being prepared."""

    name: str | None
    feed: WorktreeProgressFeed

    @property
    def detail(self) -> WorktreeEffectDetail:
        message = self.name or "workspace"
        latest = self.feed.latest
        return WorktreeEffectDetail(
            tool_name="worktree",
            progress=(
                None
                if latest is None
                else WorktreeEffectProgress(
                    phase=latest.phase.value,
                    completed_files=latest.completed_files,
                    total_files=latest.total_files,
                )
            ),
            display=EffectCallDisplay(
                summary=f"worktree: {message}",
                verb="Creating",
                message=message,
                settled_verb="Created",
                settled_message=message,
                status_text="Creating worktree",
            ),
        )

    @property
    def state(self) -> RunningEffectState:
        return RunningEffectState()

    def failed_state(self, error: BaseException) -> FailedEffectState:
        message = str(error) or "Worktree creation failed"
        return FailedEffectState(
            error=PublicError(code=type(error).__name__, message=message),
            display=EffectResultDisplay(
                success=False, verb="Failed", message="Worktree creation failed"
            ),
        )


@asynccontextmanager
async def publishing_progress(
    progress: WorktreeProgress | None, publish: Callable[[], Awaitable[None]]
) -> AsyncIterator[None]:
    """Publish each change of the progress until the block is left.

    Nothing is published after the block exits, which is what lets a caller
    settle the entry the progress was shown on right after it.
    """
    if progress is None:
        yield
        return
    publisher = asyncio.create_task(_publish_changes(progress.feed, publish))
    try:
        yield
    finally:
        publisher.cancel()
        with suppress(asyncio.CancelledError):
            await publisher


async def _publish_changes(
    feed: WorktreeProgressFeed, publish: Callable[[], Awaitable[None]]
) -> None:
    # Best effort, like the worktree announce: a lost update costs a stale
    # step, not the worktree it describes.
    while True:
        await feed.wait_for_change()
        try:
            await publish()
        except Exception:
            logger.warning("Stopped publishing worktree progress", exc_info=True)
            return
        await asyncio.sleep(_PROGRESS_INTERVAL_SECONDS)
