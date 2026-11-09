from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server._enter_worktree import (
    EnteredWorktree,
    EnterWorktreeArgs,
    run_enter_worktree,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_kept", [False, True])
async def test_the_result_says_whether_the_left_directory_stays_writable(
    previous_kept: bool,
) -> None:
    """*Prepare*: An entered worktree, leaving the user's checkout or a
        worktree the session holds.
    *Do*: Build the tool result.
    *Assert*: Only leaving the checkout says writing there needs approval.
    """

    async def enter(_session_id: str, _args: EnterWorktreeArgs) -> EnteredWorktree:
        return EnteredWorktree(
            path=Path("/wt/b"),
            branch="vibe/b",
            created=True,
            previous_cwd=Path("/wt/a"),
            previous_kept=previous_kept,
        )

    result = await run_enter_worktree(
        EnterWorktreeArgs(name="b"), session_id="s", enter=enter
    )

    assert ("needs the user's approval" in result.message) is (not previous_kept)
    assert ("stays in your workspace" in result.message) is previous_kept


@pytest.mark.asyncio
async def test_a_branched_off_worktree_says_how_to_update_the_branch_it_left() -> None:
    """*Prepare*: A worktree started from ``feat/x``'s tip on ``feat-x``,
        because ``feat/x`` is checked out elsewhere.
    *Do*: Build the tool result.
    *Assert*: It names the new branch and how to push to ``feat/x``.
    """

    async def enter(_session_id: str, _args: EnterWorktreeArgs) -> EnteredWorktree:
        return EnteredWorktree(
            path=Path("/wt/feat-x"),
            branch="feat-x",
            created=True,
            previous_cwd=Path("/repo"),
            branched_off_from="feat/x",
        )

    result = await run_enter_worktree(
        EnterWorktreeArgs(branch="feat/x"), session_id="s", enter=enter
    )

    assert "on branch feat-x, a new branch from the tip of feat/x" in result.message
    assert "git push origin HEAD:feat/x" in result.message
