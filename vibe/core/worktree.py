from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PureWindowsPath

from git import InvalidGitRepositoryError, Repo
from git.exc import GitCommandError

from vibe.core.paths import WORKTREES_DIR


class WorktreeError(Exception): ...


@dataclass(frozen=True)
class PreparedWorktree:
    name: str
    branch: str
    root: Path
    path: Path
    repo_root: Path
    base_commit: str
    created: bool
    branch_created: bool


@dataclass(frozen=True)
class WorktreeCleanupState:
    has_uncommitted_changes: bool
    has_untracked_files: bool
    new_commit_count: int

    @property
    def is_clean(self) -> bool:
        return (
            not self.has_uncommitted_changes
            and not self.has_untracked_files
            and self.new_commit_count == 0
        )

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.has_uncommitted_changes:
            reasons.append("uncommitted changes")
        if self.has_untracked_files:
            reasons.append("untracked files")
        if self.new_commit_count:
            noun = "commit" if self.new_commit_count == 1 else "commits"
            reasons.append(
                f"{self.new_commit_count} {noun} added to the branch during this session"
            )
        return tuple(reasons)


def prepare_worktree(name: str, base: Path) -> Path:
    return prepare_worktree_session(name, base).path


def prepare_worktree_session(name: str, base: Path) -> PreparedWorktree:
    _validate_worktree_name(name)
    repo = _open_repo(base)

    common_git_dir = _common_git_dir(repo)
    repo_root = common_git_dir.parent
    relative_base = base.resolve().relative_to(Path(repo.working_dir).resolve())
    target = _worktree_root(repo_root, common_git_dir) / name

    if target.is_dir():
        _validate_existing_worktree(target, name, common_git_dir)
        return _build_prepared(
            name, target, relative_base, repo_root, created=False, branch_created=False
        )

    branch_created = name not in (h.name for h in repo.heads)
    _create_worktree(repo, target, name, branch_created=branch_created)
    return _build_prepared(
        name,
        target,
        relative_base,
        repo_root,
        created=True,
        branch_created=branch_created,
    )


def _open_repo(base: Path) -> Repo:
    try:
        return Repo(base, search_parent_directories=True)
    except InvalidGitRepositoryError as e:
        raise WorktreeError("--worktree requires a git repository.") from e


def _create_worktree(
    repo: Repo, target: Path, name: str, *, branch_created: bool
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if branch_created:
            repo.git.worktree("add", "-b", name, str(target))
        else:
            repo.git.worktree("add", str(target), name)
    except GitCommandError as e:
        raise WorktreeError(f"Failed to create worktree {name!r}: {e}") from e


def _build_prepared(
    name: str,
    target: Path,
    relative_base: Path,
    repo_root: Path,
    *,
    created: bool,
    branch_created: bool,
) -> PreparedWorktree:
    # base_commit is the worktree's own HEAD at session start, so cleanup counts
    # only commits added during this session (not commits an attached or reused
    # branch already carried, which the invoking checkout's HEAD would miss).
    return PreparedWorktree(
        name=name,
        branch=name,
        root=target,
        path=_target_cwd(target, relative_base),
        repo_root=repo_root,
        base_commit=Repo(target).head.commit.hexsha,
        created=created,
        branch_created=branch_created,
    )


def inspect_worktree_for_cleanup(worktree: PreparedWorktree) -> WorktreeCleanupState:
    try:
        repo = Repo(worktree.root)
        status_lines = repo.git.status(
            "--porcelain", "--untracked-files=all"
        ).splitlines()
        new_commit_count = int(
            repo.git.rev_list(
                "--count", f"{worktree.base_commit}..{worktree.branch}"
            ).strip()
        )
    except (InvalidGitRepositoryError, GitCommandError, ValueError) as e:
        raise WorktreeError(f"Failed to inspect worktree {worktree.name!r}: {e}") from e

    return WorktreeCleanupState(
        has_uncommitted_changes=any(not line.startswith("??") for line in status_lines),
        has_untracked_files=any(line.startswith("??") for line in status_lines),
        new_commit_count=new_commit_count,
    )


def remove_worktree(worktree: PreparedWorktree, *, delete_branch: bool = True) -> None:
    _leave_worktree_if_current_directory(worktree)
    try:
        repo = Repo(worktree.repo_root)
        repo.git.worktree("remove", "--force", str(worktree.root))
        if delete_branch:
            repo.git.branch("-D", worktree.branch)
    except (InvalidGitRepositoryError, GitCommandError) as e:
        raise WorktreeError(f"Failed to remove worktree {worktree.name!r}: {e}") from e


def _validate_worktree_name(name: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or Path(name).parts != (name,)
        or PureWindowsPath(name).parts != (name,)
    ):
        raise WorktreeError("--worktree NAME must be a single path segment.")


def _common_git_dir(repo: Repo) -> Path:
    common_git_dir = Path(repo.git.rev_parse("--git-common-dir"))
    if common_git_dir.is_absolute():
        return common_git_dir.resolve()
    return (Path(repo.working_dir) / common_git_dir).resolve()


def _worktree_root(repo_root: Path, common_git_dir: Path) -> Path:
    repo_hash = hashlib.sha256(str(common_git_dir).encode()).hexdigest()[:12]
    repo_dir = f"{repo_root.name}-{repo_hash}"
    return WORKTREES_DIR.path / repo_dir


def _validate_existing_worktree(
    target: Path, expected_branch: str, expected_common_git_dir: Path
) -> None:
    if not (target / ".git").is_file():
        raise WorktreeError(f"Path {target} already exists but is not a git worktree.")

    try:
        existing_repo = Repo(target)
    except InvalidGitRepositoryError as e:
        raise WorktreeError(
            f"Path {target} already exists but is not a git worktree."
        ) from e

    existing_common_git_dir = _common_git_dir(existing_repo)
    if existing_common_git_dir != expected_common_git_dir:
        raise WorktreeError(f"Path {target} belongs to a different git repository.")

    branch = existing_repo.git.branch("--show-current").strip()
    if branch != expected_branch:
        actual = branch or "detached HEAD"
        raise WorktreeError(
            f"Path {target} is checked out on {actual!r}, expected {expected_branch!r}."
        )


def _target_cwd(target: Path, relative_base: Path) -> Path:
    target_cwd = target / relative_base
    if not target_cwd.is_dir():
        raise WorktreeError(
            f"Worktree path {target_cwd} does not exist after checkout."
        )
    return target_cwd


def _leave_worktree_if_current_directory(worktree: PreparedWorktree) -> None:
    try:
        cwd = Path.cwd().resolve()
    except FileNotFoundError:
        os.chdir(worktree.repo_root)
        return
    if cwd.is_relative_to(worktree.root.resolve()):
        os.chdir(worktree.repo_root)
