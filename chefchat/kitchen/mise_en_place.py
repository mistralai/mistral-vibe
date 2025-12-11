"""ChefChat Mise en Place - Git State Management.

"Mise en place" - everything in its place before cooking.
This module provides Git snapshot functionality to safely
undo changes made by the AI.

Usage:
    from chefchat.kitchen.mise_en_place import create_snapshot, restore_snapshot
    await create_snapshot()  # Before making changes
    await restore_snapshot()  # To undo (/chef undo)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class SnapshotResult:
    """Result of a snapshot operation."""

    success: bool
    message: str
    stash_ref: str | None = None


async def _run_git_command(
    *args: str, cwd: str | Path | None = None
) -> tuple[int, str, str]:
    """Run a git command and return results.

    Args:
        *args: Git command arguments
        cwd: Working directory

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    cwd = str(cwd) if cwd else None
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def is_git_repo(project_root: str | Path | None = None) -> bool:
    """Check if the current directory is a git repository.

    Args:
        project_root: Project root directory

    Returns:
        True if in a git repository
    """
    code, _, _ = await _run_git_command("rev-parse", "--git-dir", cwd=project_root)
    return code == 0


async def has_changes(project_root: str | Path | None = None) -> bool:
    """Check if there are uncommitted changes.

    Args:
        project_root: Project root directory

    Returns:
        True if there are changes to stash
    """
    code, stdout, _ = await _run_git_command("status", "--porcelain", cwd=project_root)
    return code == 0 and bool(stdout.strip())


async def create_snapshot(
    project_root: str | Path | None = None, message: str | None = None
) -> SnapshotResult:
    """Create a git stash snapshot before making changes.

    Args:
        project_root: Project root directory
        message: Optional stash message

    Returns:
        SnapshotResult with status
    """
    root = Path(project_root) if project_root else Path.cwd()

    # Check if git repo
    if not await is_git_repo(root):
        return SnapshotResult(
            success=False, message="Not a git repository. Undo not available."
        )

    # Check if there are changes to stash
    if not await has_changes(root):
        return SnapshotResult(
            success=True, message="No changes to snapshot.", stash_ref=None
        )

    # Create stash
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stash_message = message or f"ChefChat snapshot - {timestamp}"

    code, stdout, stderr = await _run_git_command(
        "stash", "push", "-m", stash_message, "--include-untracked", cwd=root
    )

    if code != 0:
        return SnapshotResult(success=False, message=f"Stash failed: {stderr}")

    # Get stash ref
    code, stdout, _ = await _run_git_command("stash", "list", "-n", "1", cwd=root)
    stash_ref = stdout.strip().split(":")[0] if stdout else "stash@{0}"

    return SnapshotResult(
        success=True, message=f"Snapshot created: {stash_ref}", stash_ref=stash_ref
    )


async def restore_snapshot(
    project_root: str | Path | None = None, stash_ref: str = "stash@{0}"
) -> SnapshotResult:
    """Restore from the most recent stash.

    Args:
        project_root: Project root directory
        stash_ref: Stash reference (defaults to most recent)

    Returns:
        SnapshotResult with status
    """
    root = Path(project_root) if project_root else Path.cwd()

    # Check if git repo
    if not await is_git_repo(root):
        return SnapshotResult(success=False, message="Not a git repository.")

    # Check if there are stashes
    code, stdout, _ = await _run_git_command("stash", "list", cwd=root)
    if code != 0 or not stdout.strip():
        return SnapshotResult(success=False, message="No snapshots to restore.")

    # Check if this is a ChefChat stash
    if "ChefChat snapshot" not in stdout.split("\n")[0]:
        return SnapshotResult(
            success=False,
            message="Most recent stash is not a ChefChat snapshot. Use `git stash pop` manually.",
        )

    # Pop the stash
    code, stdout, stderr = await _run_git_command("stash", "pop", stash_ref, cwd=root)

    if code != 0:
        return SnapshotResult(success=False, message=f"Restore failed: {stderr}")

    return SnapshotResult(success=True, message="Snapshot restored successfully!")


async def list_snapshots(project_root: str | Path | None = None) -> list[str]:
    """List available ChefChat snapshots.

    Args:
        project_root: Project root directory

    Returns:
        List of snapshot descriptions
    """
    root = Path(project_root) if project_root else Path.cwd()

    code, stdout, _ = await _run_git_command("stash", "list", cwd=root)
    if code != 0 or not stdout.strip():
        return []

    snapshots = []
    for line in stdout.strip().split("\n"):
        if "ChefChat snapshot" in line:
            snapshots.append(line)

    return snapshots


async def discard_changes(project_root: str | Path | None = None) -> SnapshotResult:
    """Discard all uncommitted changes (hard reset).

    ⚠️ DESTRUCTIVE - Use with caution!

    Args:
        project_root: Project root directory

    Returns:
        SnapshotResult with status
    """
    root = Path(project_root) if project_root else Path.cwd()

    # Reset tracked files
    code, _, stderr = await _run_git_command("checkout", "--", ".", cwd=root)
    if code != 0:
        return SnapshotResult(success=False, message=f"Reset failed: {stderr}")

    # Clean untracked files
    code, _, stderr = await _run_git_command("clean", "-fd", cwd=root)
    if code != 0:
        return SnapshotResult(success=False, message=f"Clean failed: {stderr}")

    return SnapshotResult(success=True, message="All changes discarded.")
