"""Git worktree management for isolated session environments.

This module provides functionality to create and manage git worktrees,
allowing multiple concurrent Vibe sessions in the same repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING

from vibe.core.utils import is_windows

if TYPE_CHECKING:
    pass


@dataclass
class WorktreeInfo:
    """Information about a git worktree."""

    path: Path
    branch: str
    commit: str
    is_current: bool
    is_locked: bool
    created_at: datetime | None = None

    @property
    def worktree_name(self) -> str:
        """Get the worktree name from the path."""
        return self.path.name


class GitWorktreeManager:
    """Manages git worktrees for isolated Vibe sessions.

    Git worktrees allow multiple working directories linked to the same
    repository, each potentially on different branches. This enables
    running multiple Vibe sessions concurrently without conflicts.
    """

    WORKTREE_PREFIX = "vibe-session-"

    def __init__(self, repo_path: Path | None = None) -> None:
        """Initialize the worktree manager.

        Args:
            repo_path: Path to the git repository. If None, uses current directory.
        """
        self._repo_path = repo_path or Path.cwd()
        self._git_dir: Path | None = None

    @property
    def repo_path(self) -> Path:
        """Get the repository root path."""
        return self._repo_path

    @property
    def git_dir(self) -> Path:
        """Get the .git directory path."""
        if self._git_dir is None:
            self._git_dir = self._find_git_dir()
        return self._git_dir

    @property
    def worktrees_dir(self) -> Path:
        """Get the directory where worktrees are stored."""
        return self.git_dir / "worktrees"

    def _find_git_dir(self) -> Path:
        """Find the .git directory for the current repository."""
        current = self._repo_path
        while current != current.parent:
            git_dir = current / ".git"
            if git_dir.exists():
                if git_dir.is_file():
                    # Gitdir file (for worktrees)
                    content = git_dir.read_text().strip()
                    if content.startswith("gitdir:"):
                        git_dir = Path(content[7:].strip())
                return git_dir.resolve()
            current = current.parent
        raise ValueError(f"Not a git repository: {self._repo_path}")

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command with the specified arguments.

        Args:
            args: Git command arguments (without 'git').
            check: Whether to raise an exception on non-zero exit code.

        Returns:
            CompletedProcess instance with the result.

        Raises:
            GitWorktreeError: If the command fails and check is True.
        """
        cmd = ["git"] + args
        try:
            result = subprocess.run(
                cmd,
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=30.0,
                stdin=subprocess.DEVNULL if is_windows() else None,
            )
            if check and result.returncode != 0:
                raise GitWorktreeError(
                    f"Git command failed: {' '.join(cmd)}\n"
                    f"stderr: {result.stderr.strip()}"
                )
            return result
        except subprocess.TimeoutExpired as e:
            raise GitWorktreeError(f"Git command timed out: {' '.join(cmd)}") from e
        except FileNotFoundError as e:
            raise GitWorktreeError("Git is not installed or not in PATH") from e

    def is_git_repository(self) -> bool:
        """Check if the current directory is a git repository."""
        try:
            self._find_git_dir()
            return True
        except ValueError:
            return False

    def list_worktrees(self) -> list[WorktreeInfo]:
        """List all worktrees in the repository.

        Returns:
            List of WorktreeInfo objects for each worktree.
        """
        result = self._run_git(["worktree", "list", "--porcelain"])
        worktrees = []
        current_worktree = None

        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current_worktree:
                    worktrees.append(current_worktree)
                path = Path(line[9:].strip())
                current_worktree = WorktreeInfo(
                    path=path,
                    branch="",
                    commit="",
                    is_current=False,
                    is_locked=False,
                )
            elif line.startswith("HEAD "):
                if current_worktree:
                    current_worktree.commit = line[5:].strip()
            elif line.startswith("branch "):
                if current_worktree:
                    branch = line[7:].strip()
                    # Remove refs/heads/ prefix
                    if branch.startswith("refs/heads/"):
                        branch = branch[11:]
                    current_worktree.branch = branch
            elif line == "current":
                if current_worktree:
                    current_worktree.is_current = True
            elif line == "locked":
                if current_worktree:
                    current_worktree.is_locked = True

        if current_worktree:
            worktrees.append(current_worktree)

        return worktrees

    def get_vibe_worktrees(self) -> list[WorktreeInfo]:
        """List all Vibe-managed worktrees.

        Returns:
            List of WorktreeInfo objects for Vibe worktrees only.
        """
        all_worktrees = self.list_worktrees()
        return [
            wt for wt in all_worktrees
            if wt.worktree_name.startswith(self.WORKTREE_PREFIX)
        ]

    def create_worktree(
        self,
        branch: str | None = None,
        name: str | None = None,
        detach: bool = False,
    ) -> WorktreeInfo:
        """Create a new worktree for a Vibe session.

        Args:
            branch: Branch name to checkout. If None, creates a new branch.
            name: Custom name for the worktree. If None, generates a unique name.
            detach: If True, create a detached HEAD worktree.

        Returns:
            WorktreeInfo for the newly created worktree.

        Raises:
            GitWorktreeError: If worktree creation fails.
        """
        if name is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"{self.WORKTREE_PREFIX}{timestamp}"

        worktree_path = self.git_dir.parent / name

        if worktree_path.exists():
            raise GitWorktreeError(f"Worktree path already exists: {worktree_path}")

        args = ["worktree", "add"]

        if detach:
            args.append("--detach")
        elif branch:
            # Check if branch exists
            try:
                self._run_git(["rev-parse", "--verify", branch], check=False)
                # Branch exists, checkout it
                args.append(str(worktree_path))
                args.append(branch)
            except GitWorktreeError:
                # Branch doesn't exist, create it
                args.append("-b")
                args.append(branch)
                args.append(str(worktree_path))
        else:
            # No branch specified, create new branch with same name as worktree
            args.append("-b")
            args.append(name)
            args.append(str(worktree_path))

        self._run_git(args)

        # Get the created worktree info
        worktrees = self.list_worktrees()
        for wt in worktrees:
            if wt.path == worktree_path:
                return wt

        raise GitWorktreeError(f"Failed to get info for created worktree: {worktree_path}")

    def remove_worktree(self, worktree_path: Path | str, force: bool = False) -> None:
        """Remove a worktree.

        Args:
            worktree_path: Path to the worktree to remove.
            force: If True, force removal even if worktree has uncommitted changes.

        Raises:
            GitWorktreeError: If worktree removal fails.
        """
        worktree_path = Path(worktree_path)

        if not worktree_path.exists():
            # Worktree might already be removed but still in git's registry
            pass

        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree_path))

        self._run_git(args)

    def prune_worktrees(self) -> list[str]:
        """Remove stale worktree references.

        Returns:
            List of pruned worktree paths.
        """
        self._run_git(["worktree", "prune"])
        # Git doesn't output anything on success, so we return empty list
        # The actual pruning is done by git
        return []

    def lock_worktree(self, worktree_path: Path | str, reason: str | None = None) -> None:
        """Lock a worktree to prevent removal.

        Args:
            worktree_path: Path to the worktree to lock.
            reason: Optional reason for the lock.

        Raises:
            GitWorktreeError: If locking fails.
        """
        worktree_path = Path(worktree_path)
        args = ["worktree", "lock"]
        if reason:
            args.append("--reason")
            args.append(reason)
        args.append(str(worktree_path))
        self._run_git(args)

    def unlock_worktree(self, worktree_path: Path | str) -> None:
        """Unlock a worktree.

        Args:
            worktree_path: Path to the worktree to unlock.

        Raises:
            GitWorktreeError: If unlocking fails.
        """
        worktree_path = Path(worktree_path)
        args = ["worktree", "unlock", str(worktree_path)]
        self._run_git(args)

    def get_current_worktree(self) -> WorktreeInfo | None:
        """Get information about the current worktree.

        Returns:
            WorktreeInfo for the current worktree, or None if not in a worktree.
        """
        worktrees = self.list_worktrees()
        for wt in worktrees:
            if wt.is_current:
                return wt
        return None

    def is_in_worktree(self) -> bool:
        """Check if the current directory is inside a git worktree."""
        current = self.get_current_worktree()
        return current is not None and not current.is_current

    def get_worktree_for_session(self, session_id: str) -> WorktreeInfo | None:
        """Find a worktree by session ID.

        Args:
            session_id: The session ID to search for.

        Returns:
            WorktreeInfo if found, None otherwise.
        """
        worktrees = self.get_vibe_worktrees()
        for wt in worktrees:
            if session_id[:8] in wt.worktree_name:
                return wt
        return None

    def cleanup_old_worktrees(self, max_age_hours: int = 24) -> list[str]:
        """Remove Vibe worktrees older than the specified age.

        Args:
            max_age_hours: Maximum age in hours before a worktree is considered stale.

        Returns:
            List of removed worktree paths.
        """
        removed = []
        cutoff = datetime.now()

        for wt in self.get_vibe_worktrees():
            # Try to get the worktree's age from git metadata
            try:
                result = self._run_git(
                    ["-C", str(wt.path), "log", "-1", "--format=%ci", "HEAD"],
                    check=False
                )
                if result.returncode != 0:
                    continue
                # Parse git commit date
                commit_date_str = result.stdout.strip()
                # Simple parsing: "2024-01-15 10:30:00 +0000"
                match = re.match(r"(\d{4}-\d{2}-\d{2})", commit_date_str)
                if not match:
                    continue
                commit_date = datetime.strptime(match.group(1), "%Y-%m-%d")
                age_hours = (cutoff - commit_date).total_seconds() / 3600
                if age_hours > max_age_hours:
                    self.remove_worktree(wt.path, force=True)
                    removed.append(str(wt.path))
            except GitWorktreeError:
                # If we can't check the age, skip this worktree
                continue

        return removed


class GitWorktreeError(Exception):
    """Exception raised for git worktree operations."""

    pass
