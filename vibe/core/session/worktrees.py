"""The worktree side of a session's lifecycle: where it starts, and what it leaves.

Three things a front end has to get right -- start in the right directory, say
that directory is occupied, reclaim the ones nobody is standing in -- written
once so that none of them is written twice.

Everything here is shaped by paths, not by a wire format. That is what lets the
app server's two session backends and the CLI share one lifecycle instead of
each carrying its own: a caller states what it wants in the vocabulary below and
translates its own request into it, rather than this module learning every
caller's request type.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from vibe.core.git.worktree import (
    ManagedWorktree,
    PreparedWorktree,
    WorktreeError,
    WorktreeRepository,
)
from vibe.core.git.worktree.naming_model import suggest_worktree_name
from vibe.observability.logging import logger


@dataclass(frozen=True, slots=True)
class UseExistingWorktree:
    """Run in a worktree that is already linked to the project."""

    cwd: Path


@dataclass(frozen=True, slots=True)
class CreateNamedWorktree:
    """Raise a worktree under a name the caller has already chosen."""

    name: str
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class CreateWorktreeForPrompt:
    """Raise a worktree and let the naming model choose what to call it."""

    prompt: str | None = None


type WorktreeRequest = (
    UseExistingWorktree | CreateNamedWorktree | CreateWorktreeForPrompt
)


@dataclass(frozen=True, slots=True)
class ResolvedWorktree:
    """The directory a session will run in, and what raising it created.

    `prepared` is None for a worktree that was already there, which is also what
    makes it the record of what a failed start has to undo: nothing to undo when
    nothing was raised.
    """

    cwd: Path
    prepared: PreparedWorktree | None = None


class SessionWorktrees:
    """Every worktree question a session has to answer."""

    # -- where a session starts -------------------------------------------

    @staticmethod
    def resolve(
        request: WorktreeRequest, base_cwd: Path, suggested_name: str | None = None
    ) -> ResolvedWorktree:
        """Turn a request into the directory the session will run in."""
        base_cwd = base_cwd.expanduser().resolve()
        if not base_cwd.is_dir():
            raise WorktreeError(f"Local project path is not a directory: {base_cwd}")

        match request:
            case UseExistingWorktree(cwd=cwd):
                requested = cwd.expanduser().resolve()
                with WorktreeRepository.open(base_cwd) as repository:
                    linked = repository.linked()
                if not any(worktree.path == requested for worktree in linked):
                    raise WorktreeError(
                        f"Worktree is not linked to the local project: {requested}"
                    )
                return ResolvedWorktree(cwd=requested)
            case CreateNamedWorktree(name=name, branch=branch):
                with WorktreeRepository.open(base_cwd) as repository:
                    created = repository.prepare(name, branch=branch)
            case CreateWorktreeForPrompt(prompt=prompt):
                with WorktreeRepository.open(base_cwd) as repository:
                    created = repository.prepare_auto(
                        prompt=prompt, suggested_name=suggested_name
                    )
        return ResolvedWorktree(cwd=created.path, prepared=created)

    async def resolve_for_start(
        self, request: WorktreeRequest, base_cwd: Path
    ) -> ResolvedWorktree:
        """Resolve off the event loop, cleaning up if the start is cancelled.

        Shielded because a cancellation between `git worktree add` returning and
        this coroutine resuming would leave a directory nobody knows about. The
        shield lets the creation finish so there is something to clean up, and
        the caller then cleans it up before re-raising.
        """
        suggested_name = await self._suggest_name(request, base_cwd)
        resolve = asyncio.create_task(
            asyncio.to_thread(self.resolve, request, base_cwd, suggested_name)
        )
        try:
            return await asyncio.shield(resolve)
        except asyncio.CancelledError:
            with suppress(BaseException):
                await self.cleanup((await resolve).prepared)
            raise

    @staticmethod
    async def _suggest_name(request: WorktreeRequest, base_cwd: Path) -> str | None:
        """Ask the model for a name, before the resolve that runs in a thread.

        Only the prompt arm pays for the call: every other one was given a name
        by the caller, and waiting on a model to confirm one is latency for
        nothing.
        """
        if not isinstance(request, CreateWorktreeForPrompt):
            return None
        return await suggest_worktree_name(request.prompt, cwd=base_cwd)

    @staticmethod
    async def cleanup(worktree: PreparedWorktree | None) -> None:
        """Undo what this start did, and only that.

        Takes what raising the worktree produced rather than the resolution it
        came in, because a resolution that raised nothing has nothing here to
        undo -- which is the same None a caller who never asked for one holds.

        Best effort throughout: the session has already failed, and a directory
        left behind is worth less than the error the caller is about to raise.
        """
        if worktree is None or not worktree.created:
            return
        try:
            await asyncio.to_thread(
                worktree.remove, delete_branch=worktree.branch_created
            )
        except Exception as exc:
            logger.warning(
                "Failed to clean up worktree after session startup failure",
                exc_info=exc,
            )
            return
        if managed := ManagedWorktree.at(worktree.root):
            managed.forget()

    # -- who is standing in it ---------------------------------------------

    @staticmethod
    def hold(cwd: Path, session_id: str) -> None:
        """Mark the worktree a session is standing in as occupied.

        Retention pruning reads this marker before deleting a worktree. A session
        that never marks its own can lose its checkout. `at` answers None for a
        directory Vibe did not create, which is most of them, so this does
        nothing outside one.
        """
        if managed := ManagedWorktree.at(cwd):
            managed.hold(session_id)

    @staticmethod
    def root(cwd: Path) -> Path | None:
        """Which managed worktree a directory sits in, if any.

        None for a path outside every one, which compares unequal to any root
        and equal to another such path: two unmanaged directories share no hold
        to preserve, so a move between them takes and gives back nothing.
        """
        managed = ManagedWorktree.at(cwd)
        return None if managed is None else managed.root

    @staticmethod
    def release(cwd: Path, session_id: str) -> None:
        """Drop the mark on the way out, so the worktree can be reclaimed later.

        A marker that outlives its process keeps that worktree undeletable for
        good, so this runs before the slower parts of a close rather than after.
        """
        if managed := ManagedWorktree.at(cwd):
            managed.release_holder(session_id)
