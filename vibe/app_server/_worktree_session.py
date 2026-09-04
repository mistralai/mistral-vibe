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

from dataclasses import dataclass
from pathlib import Path

from vibe.app_server._dispatch import RequestFailure
from vibe.app_server.protocol import ProtocolErrorCode, SessionOptions
from vibe.core.git.worktree import PreparedWorktree
from vibe.core.session.worktrees import (
    CreateNamedWorktree,
    CreateWorktreeForPrompt,
    ResolvedWorktree,
    ResumableDirectories,
    SessionWorktrees as WorktreeLifecycle,
    UseExistingWorktree,
    WorktreeRequest,
)


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
            return WorktreeResolution(options=options)
        resolved = WorktreeLifecycle.resolve(
            request, _base_cwd(options), suggested_name
        )
        return _rewritten(options, resolved)

    async def resolve_for_start(self, options: SessionOptions) -> WorktreeResolution:
        """Resolve off the event loop, cleaning up if the start is cancelled."""
        request = _requested(options)
        if request is None:
            return WorktreeResolution(options=options)
        resolved = await self._lifecycle.resolve_for_start(request, _base_cwd(options))
        return _rewritten(options, resolved)

    async def cleanup(self, resolution: WorktreeResolution) -> None:
        """Undo what this start did, and only that."""
        await self._lifecycle.cleanup(resolution.prepared_worktree)

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
    def hold(cwd: Path, session_id: str) -> None:
        WorktreeLifecycle.hold(cwd, session_id)

    @staticmethod
    def root(cwd: Path) -> Path | None:
        return WorktreeLifecycle.root(cwd)

    @staticmethod
    def release(cwd: Path, session_id: str) -> None:
        WorktreeLifecycle.release(cwd, session_id)

    def start_sweep(self, cwd: Path, resumable: ResumableDirectories) -> None:
        self._lifecycle.start_sweep(cwd, resumable)

    async def sweep(self, cwd: Path, resumable: ResumableDirectories) -> None:
        await self._lifecycle.sweep(cwd, resumable)


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
    return WorktreeResolution(
        options=options.model_copy(
            update={"cwd": cwd, "workspace_roots": [cwd], "worktree": None}
        ),
        prepared_worktree=resolved.prepared,
    )
