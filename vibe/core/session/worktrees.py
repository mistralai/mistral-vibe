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
from collections.abc import Awaitable, Callable, Collection
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


# Every directory a saved session would resume into, asked of whoever holds
# the sessions. The sweep needs it to tell an abandoned worktree from one
# belonging to a session the user simply has not opened today: closing a
# session drops its hold but leaves it on disk, so holders alone would read
# every past session's checkout as unclaimed.
#
# Supplied rather than read here, because the two backends store sessions
# differently by design -- the legacy index is a flat pair of files per
# session, the harness keeps a generational journal under its own root -- and
# a sweep that knew both layouts would carry a private detail of each.
#
# Raising is how a reader that cannot see its sessions says so. It abandons
# the sweep for this run and keeps every worktree, which is the safe way to be
# wrong: answering empty would let one unreadable listing delete them all.
type ResumableDirectories = Callable[[], Awaitable[Collection[Path]]]


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
    """Every worktree question a session has to answer.

    One instance per host process, which is the lifetime the swept-bucket set
    needs: longer would carry the answer across processes that may have left
    worktrees behind, shorter would sweep on every attach.
    """

    def __init__(self) -> None:
        self._swept_buckets: set[str] = set()
        self._sweeps: set[asyncio.Task[None]] = set()

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

        What the sweep reads to tell a worktree in use from one abandoned. A
        session that never marks its own is a session whose checkout can be
        deleted out from under it. `at` answers None for a directory Vibe did
        not create, which is most of them, so this does nothing outside one.
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

    # -- the ones nobody is standing in -------------------------------------

    def start_sweep(self, cwd: Path, resumable: ResumableDirectories) -> None:
        """Sweep in the background, so attaching a session does not wait on it.

        The task is held here rather than handed back. A caller that forgot to
        keep a reference would have the sweep collected mid-run, and the callers
        that would have to remember are the ones this exists to stop writing
        things twice. It is also the wrong work to route through a backend's own
        task tracking: the legacy runtime's tears the server down on an unhandled
        error, and a sweep failing is explicitly not worth that -- it swallows
        its own below.
        """
        task = asyncio.create_task(
            self.sweep(cwd, resumable), name="vibe-worktree-claim-sweep"
        )
        self._sweeps.add(task)
        task.add_done_callback(self._sweeps.discard)

    async def sweep(self, cwd: Path, resumable: ResumableDirectories) -> None:
        """Remove the worktrees of this repository no saved session resumes into."""
        bucket: str | None = None
        try:
            bucket = await asyncio.to_thread(WorktreeRepository.bucket_for, cwd)
            if bucket is None or bucket in self._swept_buckets:
                return
            # Marked before the work rather than after, so two sessions
            # attaching in the same repository do not both sweep it, and
            # dropped again below when the attempt failed. Marking only on
            # success would sweep twice; never dropping would let one listing
            # error cost every attach for the life of the process.
            self._swept_buckets.add(bucket)
            in_use = await resumable()
            await asyncio.to_thread(WorktreeRepository.sweep_claims, cwd, in_use=in_use)
        except Exception as exc:
            if bucket is not None:
                self._swept_buckets.discard(bucket)
            logger.debug("Worktree claim sweep failed", exc_info=exc)
