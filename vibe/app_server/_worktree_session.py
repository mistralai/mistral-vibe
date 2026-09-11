"""The worktree lifecycle, in the app server's vocabulary.

The lifecycle itself is `vibe.core.session.worktrees`, which speaks paths. This
translates: a `worktree` off the wire becomes a request the core understands,
the directory it resolves to becomes rewritten `SessionOptions`, and a request
that cannot be honoured becomes a protocol error.

Kept apart because the lifecycle has callers with no protocol anywhere near them
-- the CLI raises and holds worktrees for a session it runs in-process -- and
because `protocol.py` already holds the same line from the other side: the wire
contract does not import core.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from vibe.app_server._dispatch import RequestFailure
from vibe.app_server.protocol import ProtocolErrorCode, SessionOptions
from vibe.core.git.errors import GitError
from vibe.core.git.worktree import (
    ManagedWorktree,
    PendingSessionHold,
    PreparedWorktree,
    RetainedRepositoryMapping,
)
from vibe.core.paths import dedup_paths
from vibe.core.session.worktrees import (
    CreateNamedWorktree,
    CreateWorktreeForPrompt,
    ResolvedWorktree,
    SessionWorktrees as WorktreeLifecycle,
    UseExistingWorktree,
    WorktreeRequest,
)

type MoveSession = Callable[[SessionOptions], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class WorktreeResolution:
    """Rewritten options, and what a failed start would have to undo.

    The directory travels as options rather than beside them, because that is
    the only channel it has: every backend builds its context from
    `SessionOptions`, and one told a path separately would have two answers to
    the same question.
    """

    options: SessionOptions
    prepared_worktree: PreparedWorktree | None = None
    pending_hold: PendingSessionHold | None = None


class SessionWorktrees:
    """The worktree lifecycle as the app server's backends call it.

    Holds a lifecycle rather than extending one: the two speak different
    languages, and inheritance would put `SessionOptions` in the signatures the
    CLI has to call.
    """

    def __init__(self) -> None:
        self._lifecycle = WorktreeLifecycle()

    # -- where a session starts -------------------------------------------

    @staticmethod
    def resolve(
        options: SessionOptions, suggested_name: str | None = None
    ) -> WorktreeResolution:
        """Turn a `worktree` request into the directory the session will run in."""
        request = _requested(options)
        if request is None:
            managed = ManagedWorktree.at(_base_cwd(options))
            return WorktreeResolution(
                options=options,
                pending_hold=(
                    None if managed is None else managed.hold_for_attachment()
                ),
            )
        resolved = WorktreeLifecycle.resolve(
            request, _base_cwd(options), suggested_name
        )
        return _rewritten(options, resolved)

    async def resolve_for_start(self, options: SessionOptions) -> WorktreeResolution:
        """Resolve off the event loop, cleaning up if the start is cancelled."""
        request = _requested(options)
        if request is None:
            managed = ManagedWorktree.at(_base_cwd(options))
            if managed is None:
                return WorktreeResolution(options=options)
            acquire_hold = asyncio.create_task(
                asyncio.to_thread(managed.hold_for_attachment)
            )
            try:
                return WorktreeResolution(
                    options=options, pending_hold=await asyncio.shield(acquire_hold)
                )
            except asyncio.CancelledError:
                with suppress(BaseException):
                    pending_hold = await acquire_hold
                    if pending_hold is not None:
                        pending_hold.release()
                raise
        resolved = await self._lifecycle.resolve_for_start(request, _base_cwd(options))
        return _rewritten(options, resolved)

    async def raise_behind(
        self,
        session_id: str,
        move: MoveSession,
        requested: SessionOptions,
        started_in: SessionOptions,
    ) -> None:
        """Raise the worktree the session asked for and move it in.

        Raises rather than logging: the caller holds the session's turns behind
        this, and a session that asked to be isolated and is still standing in
        the project must refuse to run rather than quietly write it.
        """
        previous = _base_cwd(started_in)
        resolution = await self.resolve_for_start(requested)
        cwd = _base_cwd(resolution.options)
        try:
            self.hold(cwd, session_id, resolution.pending_hold)
            await move(resolution.options)
        except BaseException:
            # Inside the same arm as the move: a worktree created and then not
            # held could be selected by retention while startup is unwinding.
            with suppress(BaseException):
                self.release(cwd, session_id)
            await self.cleanup(resolution)
            raise
        # The session is standing in the worktree by now. Letting go of where it
        # was is bookkeeping, and must not be the reason its turns are refused.
        with suppress(Exception):
            self.release(previous, session_id)

    async def cleanup(self, resolution: WorktreeResolution) -> None:
        """Undo what this start did, and only that."""
        await self._lifecycle.cleanup(
            resolution.prepared_worktree, resolution.pending_hold
        )

    async def restore(self, cwd: Path) -> bool:
        try:
            return await self._lifecycle.restore(cwd)
        except GitError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc

    @staticmethod
    def retained_repository_mapping(cwd: Path) -> RetainedRepositoryMapping | None:
        managed = ManagedWorktree.at(cwd)
        if managed is None:
            return None
        return managed.retained_repository_mapping(cwd)

    @staticmethod
    def is_managed(cwd: Path) -> bool:
        return ManagedWorktree.at(cwd) is not None

    @staticmethod
    def reject_input(options: SessionOptions) -> None:
        """Refuse a `worktree` on anything but a start.

        A resumed session already has a directory, and honouring one here would
        move it out from under a transcript that names the old paths. Enforced
        here rather than in the lifecycle because it is a rule about which
        requests are valid, which is the wire contract's to state.
        """
        if options.worktree is None:
            return
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            "worktree is only supported when starting a session",
        )

    # -- who is standing in it, and the ones nobody is ----------------------
    #
    # Already path-shaped, so these hand straight through. Kept on this class
    # rather than asking every caller to reach for the lifecycle, so a backend
    # holds one object for all of it.

    @staticmethod
    def hold(
        cwd: Path, session_id: str, pending_hold: PendingSessionHold | None = None
    ) -> None:
        WorktreeLifecycle.hold(cwd, session_id, pending_hold)

    @staticmethod
    def root(cwd: Path) -> Path | None:
        return WorktreeLifecycle.root(cwd)

    @staticmethod
    def release(cwd: Path, session_id: str) -> None:
        WorktreeLifecycle.release(cwd, session_id)


def _requested(options: SessionOptions) -> WorktreeRequest | None:
    """The wire's `worktree` in the lifecycle's vocabulary, or None for neither.

    The match is here rather than in the lifecycle so that the protocol's three
    shapes stay the protocol's: a caller with no wire format states what it
    wants directly.
    """
    worktree = options.worktree
    if worktree is None:
        return None
    match worktree.kind:
        case "existing":
            return UseExistingWorktree(cwd=Path(worktree.cwd))
        case "create":
            return CreateNamedWorktree(name=worktree.name, branch=worktree.branch)
        case "auto":
            return CreateWorktreeForPrompt(prompt=worktree.prompt)
        case _:
            raise TypeError(f"Unsupported worktree input: {worktree!r}")


def _base_cwd(options: SessionOptions) -> Path:
    return Path(options.cwd or Path.cwd())


def _rewritten(
    options: SessionOptions, resolved: ResolvedWorktree
) -> WorktreeResolution:
    """Options that start where the lifecycle decided, with the request spent."""
    cwd = str(resolved.cwd)
    previous_cwd = _base_cwd(options).expanduser().resolve()
    # Moving replaces the checkout root, not extra directories the caller
    # authorized, such as Desktop's attachment cache.
    workspace_roots = [
        cwd,
        *(
            str(root)
            for root in dedup_paths(
                Path(root).expanduser() for root in options.workspace_roots
            )
            if root not in {previous_cwd, resolved.cwd}
        ),
    ]
    return WorktreeResolution(
        options=options.model_copy(
            update={"cwd": cwd, "workspace_roots": workspace_roots, "worktree": None}
        ),
        prepared_worktree=resolved.prepared,
        pending_hold=resolved.pending_hold,
    )
