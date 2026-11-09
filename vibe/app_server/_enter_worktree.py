"""The ``enter_worktree`` tool: the agent moves its session into a worktree.

The session owns the move (``UnifiedHarnessBackendAdapter.enter_worktree``):
it raises the worktree with the lifecycle a session start uses, holds and
records it, and lands the move at the next model step, so the calls the model
asked for beside this one keep the checkout they were planned in.

Every worktree the session enters stays one of its workspace roots, so the
agent can still reach the others by absolute path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ENTER_WORKTREE_TOOL_NAME = "enter_worktree"

ENTER_WORKTREE_TOOL_DESCRIPTION = """\
Move this session into a git worktree so your edits stay off the user's \
checkout. Left out, `path` creates a new \
worktree on a fresh branch from the default branch, named after `name`. Pass \
`branch` to continue an existing branch (a PR's, say) instead. Pass `path` to \
go back into a worktree this session already entered. From the next \
step on, commands and relative paths run in the worktree; every worktree you \
entered stays reachable by absolute path, and the user's checkout only with \
their approval."""


class EnterWorktreeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "A few words naming the change, used for the worktree and its "
            "branch. Left out, the worktree is named after the user's request. "
            "Ignored with `path`."
        ),
    )
    branch: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "An existing branch to check out in the new worktree, local or on "
            "the remote, rather than a fresh `vibe/` branch. Ignored with `path`."
        ),
    )
    path: str | None = Field(
        default=None,
        min_length=1,
        description="A worktree this session already entered, to move back into.",
    )


class EnterWorktreeResult(BaseModel):
    path: str
    branch: str
    created: bool
    message: str


class EnterWorktreeError(Exception):
    pass


type WorktreeEnterer = Callable[[str, EnterWorktreeArgs], Awaitable[EnteredWorktree]]


class EnteredWorktree(BaseModel):
    path: Path
    branch: str
    created: bool
    previous_cwd: Path
    previous_kept: bool = False
    branched_off_from: str | None = None


async def run_enter_worktree(
    args: EnterWorktreeArgs, *, session_id: str, enter: WorktreeEnterer
) -> EnterWorktreeResult:
    entered = await enter(session_id, args)
    # The system prompt names the directory the session started in, and it
    # cannot change mid-session: the result is where the model learns the move.
    return EnterWorktreeResult(
        path=str(entered.path),
        branch=entered.branch,
        created=entered.created,
        message=(
            f"From your next step, commands and relative paths run in "
            f"{entered.path} on branch {entered.branch}"
            + (
                f", a new branch from the tip of {entered.branched_off_from}, which "
                "is checked out in another worktree. To update it (and its pull "
                f"request), push with `git push origin HEAD:{entered.branched_off_from}`"
                if entered.branched_off_from is not None
                else ""
            )
            + ". Edit files there"
            + (
                f"; {entered.previous_cwd} is a worktree of yours too and stays "
                "in your workspace."
                if entered.previous_kept
                else f": {entered.previous_cwd} is now outside your workspace "
                "and needs the user's approval."
            )
        ),
    )


__all__ = [
    "ENTER_WORKTREE_TOOL_DESCRIPTION",
    "ENTER_WORKTREE_TOOL_NAME",
    "EnterWorktreeArgs",
    "EnterWorktreeError",
    "EnterWorktreeResult",
    "EnteredWorktree",
    "WorktreeEnterer",
    "run_enter_worktree",
]
