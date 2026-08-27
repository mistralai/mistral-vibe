from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, Self

# GitPython resolves the git executable at import time and raises when it is
# missing, so it is only imported at runtime by _git_python(). app-server must
# boot on machines without git; only the git operations themselves may need it.
if TYPE_CHECKING:
    from git import InvalidGitRepositoryError, Repo
    from git.exc import GitCommandError, NoSuchPathError

from vibe.core.git.errors import (
    GitError,
    GitRepositoryNotFoundError,
    GitUnavailableError,
)
from vibe.core.git.remote import find_remote_url

_GIT_USAGE_ERROR_STATUS = 129
_DEFAULT_REMOTE = "origin"
# Generous enough that it only fires when the remote is unreachable rather than
# slow, since the caller carries on with the stale ref either way.
_FETCH_TIMEOUT_SECONDS = 10
# Git must never stop to ask. A remote needing credentials otherwise opens a
# helper, which on Windows is a dialog behind the app, and blocks until someone
# notices. Failing is the right answer for a refresh the caller treats as
# optional.
_NON_INTERACTIVE_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
# Ordered by how likely each is to be the trunk of a repository that never set
# origin/HEAD.
CONVENTIONAL_BASE_BRANCHES = ("main", "master", "develop")
_NUMSTAT_FIELDS = 3


@dataclass(frozen=True)
class BranchChanges:
    """Lines this branch has added and removed against its base."""

    additions: int
    deletions: int


@dataclass(frozen=True)
class GitStatus:
    worktree_name: str
    root: Path
    branch: str | None
    base_branch: str | None
    # None when the checkout has no remote at all. Names the same repository the
    # project's own list names, so a caller holding both can tell they are one
    # thing.
    repo_url: str | None


@dataclass(frozen=True)
class _GitPython:
    repo: type[Repo]
    invalid_git_repository_error: type[InvalidGitRepositoryError]
    git_command_error: type[GitCommandError]
    no_such_path_error: type[NoSuchPathError]


def _git_python() -> _GitPython:
    try:
        from git import InvalidGitRepositoryError, Repo
        from git.exc import GitCommandError, NoSuchPathError
    except ImportError as e:
        raise GitUnavailableError("Git operations require git to be installed.") from e
    return _GitPython(
        repo=Repo,
        invalid_git_repository_error=InvalidGitRepositoryError,
        git_command_error=GitCommandError,
        no_such_path_error=NoSuchPathError,
    )


@dataclass(frozen=True)
class RepoPaths:
    common_git_dir: Path
    repo_root: Path


class GitRepo:
    """The one place GitPython is called and its failures become GitError."""

    def __init__(self, repo: Repo) -> None:
        self._repo = repo

    @classmethod
    def open(cls, base: Path) -> Self:
        git = _git_python()
        try:
            return cls(git.repo(base, search_parent_directories=True))
        except (git.invalid_git_repository_error, git.no_such_path_error) as e:
            raise GitRepositoryNotFoundError(
                "Path is not inside a git repository."
            ) from e

    # Opens the worktree in its own right rather than going through the
    # repository it is linked to, so the commit read is that worktree's HEAD.
    @classmethod
    def head_commit_at(cls, target: Path) -> str:
        git = _git_python()
        try:
            return cls(git.repo(target)).head_commit()
        except (
            git.invalid_git_repository_error,
            git.no_such_path_error,
            git.git_command_error,
            ValueError,
        ) as e:
            raise GitError(f"Failed to inspect HEAD for {target.name!r}: {e}") from e

    @cached_property
    def _gitpy(self) -> _GitPython:
        return _git_python()

    def close(self) -> None:
        self._repo.close()

    # GitPython keeps `git cat-file --batch` children alive holding handles into
    # .git until the repository is closed, and this is read on every session
    # listing: a caller that forgets leaks a set per refresh rather than once.
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @property
    def working_dir(self) -> Path:
        return Path(self._repo.working_dir)

    def status(self) -> GitStatus:
        """This repository's own checkout, as the interface names it.

        Read off the open this object already holds. It describes the checkout
        the repository was opened at and no other: the facts that differ per
        worktree come from the worktree listing, which is read off the same
        open, and the rest -- the base branch, the remote -- are the
        repository's and the same whichever of its checkouts you ask from.
        """
        # A bare repository has no working tree, so there is no checkout to
        # describe. Refused rather than reported empty, because the caller that
        # opened it still has to close it either way, and this runs on every
        # session listing: a `None` that hid the open would leak a set of
        # cat-file handles per refresh.
        if self._repo.working_tree_dir is None:
            raise GitRepositoryNotFoundError(
                "A bare repository has no checkout to report on."
            )
        root = self.working_dir.resolve()
        return GitStatus(
            worktree_name=root.name,
            root=root,
            branch=self.branch(),
            base_branch=self.base_branch(),
            repo_url=find_remote_url(self._repo),
        )

    def branch_changes(self) -> BranchChanges | None:
        """Committed lines added and removed on this branch, against its base.

        Measured from the merge base rather than the base tip, so commits that
        landed on the base after this branch started are not counted here as
        deletions. Codex settled on the same definition for its status line,
        and the reasoning holds: a number that describes a branch should not
        move while someone edits.

        Uncommitted work is deliberately absent. Folding it in would make the
        figure change on every keystroke, and counting it separately costs a
        ``git status`` that dominates the whole probe. An agent that has not
        committed therefore reads as having changed nothing, which is a known
        cost of matching Codex here rather than an oversight.
        """
        return self.changes_on("HEAD")

    def changes_on(self, ref: str) -> BranchChanges | None:
        """:meth:`branch_changes` for a branch this checkout is not on.

        Every worktree of a repository is a ref in the same object database, so
        one checkout can measure them all. That is the point: a picker listing
        five worktrees would otherwise open five GitPython repositories, each
        holding the ``cat-file`` handles this class exists to close once.
        """
        base_ref = self._base_ref()
        if base_ref is None:
            return None
        git = _git_python()
        try:
            merge_base = self._repo.git.merge_base(ref, base_ref).strip()
            if not merge_base:
                return None
            numstat = self._repo.git.diff("--numstat", f"{merge_base}..{ref}")
        except git.git_command_error:
            # An unborn HEAD, a ref that is gone, or a base that shares no
            # history with it. All are "nothing to compare" rather than a
            # failure worth surfacing.
            return None
        return _sum_numstat(numstat)

    def _base_ref(self) -> str | None:
        """The ref to measure against, preferring the remote-tracking one.

        A local branch of the same name can sit behind what the remote has, and
        measuring against it would credit this branch with commits it merely
        has not pulled.
        """
        base = self.base_branch()
        if base is None:
            return None
        return next(
            (
                ref
                for ref in (f"refs/remotes/origin/{base}", f"refs/heads/{base}")
                if self._has_ref(ref)
            ),
            None,
        )

    def branch(self) -> str | None:
        try:
            return self._repo.active_branch.name
        except TypeError:
            # Detached HEAD is checked out at a commit and has no branch name.
            return None

    def base_branch(self) -> str | None:
        """Best-effort guess at the branch this work will merge back into.

        Deliberately avoids `git remote show origin`, which hits the network.
        """
        return (
            self._origin_head_branch()
            or self._configured_default_branch()
            or self._conventional_default_branch()
        )

    def _origin_head_branch(self) -> str | None:
        git = _git_python()
        try:
            ref = self._repo.git.symbolic_ref("--short", "refs/remotes/origin/HEAD")
        except git.git_command_error:
            # Unset on clones made with --no-checkout or a bare --mirror origin.
            return None
        return ref.strip().removeprefix("origin/") or None

    def _configured_default_branch(self) -> str | None:
        configured = self._repo.config_reader().get_value("init", "defaultBranch", "")
        branch = str(configured).strip()
        if not branch or not self._has_branch(branch):
            return None
        return branch

    def _conventional_default_branch(self) -> str | None:
        return next(
            (
                branch
                for branch in CONVENTIONAL_BASE_BRANCHES
                if self._has_branch(branch)
            ),
            None,
        )

    def _has_branch(self, branch: str) -> bool:
        return any(
            self._has_ref(ref)
            for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}")
        )

    def _has_ref(self, ref: str) -> bool:
        git = _git_python()
        try:
            self._repo.git.show_ref("--verify", "--quiet", ref)
        except git.git_command_error:
            # Any failure here means the ref could not be confirmed, which for a
            # best-effort base-branch guess is the same as it not being there.
            return False
        return True

    def head_commit(self) -> str:
        return self._repo.head.commit.hexsha

    # Derived on first use, not in __init__: reading the repository layout can
    # fail on a linked worktree with a separate git directory, and a caller that
    # only wanted to open the repository must not pay for that.
    @cached_property
    def paths(self) -> RepoPaths:
        common_git_dir = self.resolve_git_dir(
            self._repo.git.rev_parse("--git-common-dir")
        )
        return RepoPaths(
            common_git_dir=common_git_dir,
            repo_root=self._primary_worktree_root(common_git_dir),
        )

    def resolve_git_dir(self, value: str) -> Path:
        git_dir = Path(value)
        if git_dir.is_absolute():
            return git_dir.resolve()
        return (self.working_dir / git_dir).resolve()

    def _primary_worktree_root(self, common_git_dir: Path) -> Path:
        # Repositories using --separate-git-dir report that directory as the first
        # worktree, so the primary checkout's working directory is authoritative.
        if Path(self._repo.git_dir).resolve() == common_git_dir:
            return self.working_dir.resolve()
        if common_git_dir.name == ".git":
            return common_git_dir.parent
        raise GitError(
            "Cannot determine the primary checkout from a linked worktree "
            "using a separate Git directory."
        )

    def relative_base(self, base: Path) -> Path:
        working_root = self.working_dir.resolve()
        try:
            return base.resolve().relative_to(working_root)
        except ValueError as e:
            raise GitError(
                f"Path {base.resolve()} is outside git repository {working_root}."
            ) from e

    def validate_branch(self, branch: str) -> None:
        try:
            self._repo.git.check_ref_format("--branch", branch)
        except (self._gitpy.git_command_error, ValueError) as e:
            raise GitError(f"Invalid branch {branch!r}.") from e

    def branch_exists(self, branch: str) -> bool:
        try:
            self._repo.git.show_ref("--verify", "--quiet", f"refs/heads/{branch}")
        except self._gitpy.git_command_error as e:
            if e.status == 1:
                return False
            raise GitError(f"Failed to inspect branch {branch!r}: {e}") from e
        return True

    # Carries git's error text and nothing else: this is a cleanup primitive,
    # and only the caller knows whether the failure is worth reporting and in
    # what words.
    def delete_branch(self, branch: str, *, force: bool = False) -> None:
        try:
            self._repo.git.branch("-D" if force else "-d", branch)
        except self._gitpy.git_command_error as e:
            raise GitError(str(e)) from e

    def remote_default_branch_ref(self, remote: str = _DEFAULT_REMOTE) -> str | None:
        """The remote-tracking ref for the remote's default branch.

        None when the remote is absent, or when its HEAD is not recorded
        locally, which is the case for clones made with --single-branch.
        """
        try:
            ref = self._repo.git.symbolic_ref("--quiet", f"refs/remotes/{remote}/HEAD")
        except self._gitpy.git_command_error:
            return None
        prefix = "refs/remotes/"
        return ref[len(prefix) :] if ref.startswith(prefix) else None

    def fetch_branch(self, remote: str, branch: str) -> None:
        # A source-only refspec looks like it would only write FETCH_HEAD, but
        # git also updates the remote-tracking ref whenever the configured
        # remote.<name>.fetch covers it, which is how callers see the new tip
        # under refs/remotes. An explicit destination would force the update
        # past a deliberately narrow refspec, so leave the configured one to
        # decide.
        #
        # The wait is bounded here rather than with GitPython's
        # kill_after_timeout, which raises on Windows before it runs git at
        # all. Doing it directly also means one timeout on every platform, and
        # communicate() drains the pipes, so a fetch with a lot of progress
        # output cannot fill stderr and block on a full buffer.
        try:
            process = self._repo.git.fetch(
                remote,
                branch,
                as_process=True,
                universal_newlines=True,
                env=_NON_INTERACTIVE_GIT_ENV,
            )
        except self._gitpy.git_command_error as e:
            raise GitError(f"Failed to fetch {remote}/{branch}: {e}") from e

        proc = process.proc
        try:
            _, stderr = proc.communicate(timeout=_FETCH_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            proc.communicate()
            raise GitError(
                f"Timed out fetching {remote}/{branch} after {_FETCH_TIMEOUT_SECONDS}s."
            ) from e
        if proc.returncode != 0:
            raise GitError(
                f"Failed to fetch {remote}/{branch}: {(stderr or '').strip()}"
            )

    def add_worktree(
        self,
        target: Path,
        branch: str,
        *,
        branch_created: bool,
        start_point: str | None = None,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if branch_created:
                create = ["add", "-b", branch, str(target)]
                # Omitted rather than defaulted, because git's own default is
                # the invoking checkout's HEAD and that is the right answer for
                # a repository with no remote to start from.
                if start_point is not None:
                    create.append(start_point)
                self._repo.git.worktree(*create)
            else:
                self._repo.git.worktree("add", str(target), branch)
        except self._gitpy.git_command_error as e:
            raise GitError(
                f"Failed to create worktree {target.name!r} for branch {branch!r}: {e}"
            ) from e

    # Carries git's error text and nothing else: this is a cleanup primitive,
    # and only the caller knows whether the failure is worth reporting and in
    # what words.
    def remove_worktree(self, target: Path) -> None:
        try:
            self._repo.git.worktree("remove", "--force", str(target))
        except self._gitpy.git_command_error as e:
            raise GitError(str(e)) from e

    def records(self) -> tuple[_WorktreeRecord, ...]:
        try:
            output = self._list_porcelain(null_terminated=True)
            return _parse_worktree_records(output, separator="\0")
        except self._gitpy.git_command_error as e:
            if e.status != _GIT_USAGE_ERROR_STATUS:
                raise GitError(f"Failed to list git worktrees: {e}") from e
        try:
            output = self._list_porcelain(null_terminated=False)
        except self._gitpy.git_command_error as e:
            raise GitError(f"Failed to list git worktrees: {e}") from e
        return _parse_worktree_records(output, separator="\n")

    def _list_porcelain(self, *, null_terminated: bool) -> str:
        args = ["list", "--porcelain"]
        if null_terminated:
            args.append("-z")
        return self._repo.git.worktree(*args)


@dataclass(frozen=True)
class _WorktreeRecord:
    root: Path
    branch: str | None
    prunable: bool


def _parse_worktree_records(
    output: str, *, separator: str
) -> tuple[_WorktreeRecord, ...]:
    records: list[_WorktreeRecord] = []
    current: _WorktreeRecord | None = None

    for token in output.split(separator):
        if not token:
            if current is not None:
                records.append(current)
            current = None
            continue

        field, _, value = token.partition(" ")
        if field == "worktree":
            if current is not None:
                records.append(current)
            current = _WorktreeRecord(Path(value), None, False)
        elif field == "branch" and current is not None:
            current = _WorktreeRecord(
                current.root, value.removeprefix("refs/heads/"), current.prunable
            )
        elif field == "prunable" and current is not None:
            current = _WorktreeRecord(current.root, current.branch, True)

    if current is not None:
        records.append(current)
    return tuple(records)


def _sum_numstat(numstat: str) -> BranchChanges:
    """Total one ``git diff --numstat`` block.

    Binary files report their counts as ``-`` and contribute nothing, since
    there are no lines to add up.
    """
    additions = 0
    deletions = 0
    for line in numstat.splitlines():
        fields = line.split("\t")
        if len(fields) < _NUMSTAT_FIELDS:
            continue
        added, removed = fields[0], fields[1]
        if added.isdigit():
            additions += int(added)
        if removed.isdigit():
            deletions += int(removed)
    return BranchChanges(additions=additions, deletions=deletions)
