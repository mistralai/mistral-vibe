from __future__ import annotations

from pathlib import Path

from tests.app_server.storage_usage._tree import Tree
from vibe.app_server._storage_usage.removal import finished_worktrees


def test_finished_worktrees_are_named_under_the_given_root(
    tree: Tree, tmp_path: Path
) -> None:
    """*Prepare*: A claimed worktree of an archived session, and one of a
    session still active.
    *Do*: List the finished worktrees of both sessions under the tree's root,
    then under another root.
    *Assert*: Only the archived session's worktree, each time under the root
    given.
    """
    # Prepare
    tree.session("done", archived=True)
    tree.session("busy")
    tree.worktree("w-done", sessions=["done"])
    tree.worktree("w-busy", sessions=["busy"])
    other_root = tmp_path / "elsewhere"

    # Do
    here = finished_worktrees(["done", "busy"], tree.worktrees_root)
    elsewhere = finished_worktrees(["done", "busy"], other_root)

    # Assert
    assert here == [tree.worktrees_root / "repo-0123456789ab" / "w-done"]
    assert elsewhere == [other_root / "repo-0123456789ab" / "w-done"]
