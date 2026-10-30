from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import cached_property
import os
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING, Self

# GitPython resolves the git executable at import time and raises when it is
# missing, so it is only imported at runtime by _git_python(). app-server must
# boot on machines without git; only the git operations themselves may need it.
if TYPE_CHECKING:
    from git import Git, InvalidGitRepositoryError, Repo
    from git.exc import GitCommandError, NoSuchPathError

from vibe.core.git.errors import (
    GitError,
    GitRepositoryNotFoundError,
    GitUnavailableError,
)
from vibe.core.git.fetch import UnsafeGitFetchError, fetch_remote
from vibe.core.git.remote import GitHubRemoteInfo, find_github_remote, find_remote_url
from vibe.core.trusted_folders import TrustedFoldersManager, trusted_folders_manager
from vibe.utils.platform import configure_git_python_executable

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
# A repository's own hooks run on more than checkout: `git fetch` can end
# with `gc --auto` (pre-auto-gc), and every ref update runs
# reference-transaction, so worktree add, fetch, and branch deletion in the
# pre-trust worktree flow would each execute a hook the repository shipped.
# -c on the command line takes precedence over the repository's config, so
# the repository being operated on cannot override this. The value is an
# absolute path that does not exist rather than an empty one, because git
# resolves a relative core.hooksPath against the directory the hooks would
# run in -- which is the untrusted worktree itself. On Windows a
# leading-slash path resolves against the current drive's root; planting a
# hook there takes the local write access this guard is not meant to stop.
# Every guarded call site pairs this with core.fsmonitor=, so both keys a
# repository can use to make git run a command are neutralized.
_NO_HOOKS_CONFIG = "core.hooksPath=/nonexistent-vibe-disabled-git-hooks"
# Filter drivers are the third, for a repository the user has not trusted:
# `.gitattributes` in the checkout selects a driver by name, and checkout,
# status and add run that driver's smudge, clean or process command. Only
# drivers the repository's own config defines are switched off; one the user
# configured themselves, such as Git LFS's, still runs. A trusted repository
# keeps its own drivers (git-crypt, an LFS --local install, nbstripout).
# `git config --get-regexp` exits 1 when nothing matches.
_FILTER_COMMAND_KEYS = ("smudge", "clean", "process")
_GIT_CONFIG_NO_MATCH_STATUS = 1
# Git writes a checkout's files one at a time by default, which dominates
# `worktree add` on a large repository: for ~70k files it measured 12-19s
# sequential against 6-10s parallel on a macOS laptop. Half the cores plus one
# leaves room for the rest of the machine; the curve is flat past that point.
# Only applied when the user has not chosen a worker count, since a -c override
# would otherwise replace their setting.
_CHECKOUT_WORKERS = (os.cpu_count() or 1) // 2 + 1
# Git prints this on stderr for every percent a checkout advances, once asked
# with --progress; it stays silent on a pipe otherwise. Matched in the C locale,
# which the checkout is run under.
_CHECKOUT_PROGRESS = re.compile(rb"Updating files:\s+\d+% \((\d+)/(\d+)\)")
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


def _require_git_executable(cwd: Path | None = None) -> None:
    if configure_git_python_executable(cwd=cwd) is None:
        raise GitUnavailableError(
            "Git operations require a trusted Git executable. Install Git or set "
            "GIT_PYTHON_GIT_EXECUTABLE to an absolute path."
        )


def _git_python() -> _GitPython:
    executable = configure_git_python_executable()
    if executable is None:
        raise GitUnavailableError(
            "Git operations require a trusted Git executable. Install Git or set "
            "GIT_PYTHON_GIT_EXECUTABLE to an absolute path."
        )
    try:
        from git import Git, InvalidGitRepositoryError, Repo, refresh
        from git.exc import GitCommandError, GitCommandNotFound, NoSuchPathError
    except ImportError as e:
        raise GitUnavailableError("Git operations require git to be installed.") from e
    try:
        if Git.GIT_PYTHON_GIT_EXECUTABLE != executable:
            refresh(executable)
    except (GitCommandNotFound, PermissionError) as e:
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


def sanitized_git(
    repo: Repo,
    *extra_config: str,
    cwd: Path | None = None,
    trust_store: TrustedFoldersManager | None = None,
    trust_root: Path | None = None,
) -> Git:
    """A repository's git with the config keys that name commands
    overridden, for one command.

    `core.fsmonitor` names a command git runs on index refresh, and
    `core.hooksPath` names the directory its hooks come from; neither
    may execute for a repository the user has not trusted yet. Nor may
    a filter driver the repository defines for itself, which checkout,
    status and add would run on its files.
    GitPython applies the -c overrides to the next command on the
    shared handle and then resets them, so a handle stored for reuse
    would run every command after the first unsanitized. Call this
    per command.

    `cwd` runs the command in another checkout of the repository, such
    as a linked worktree, through git's own -C.
    """
    config = [
        "core.fsmonitor=",
        _NO_HOOKS_CONFIG,
        *(
            ()
            if repository_is_trusted(repo, trust_store, trust_root=trust_root)
            else _repository_filter_overrides(repo, cwd=cwd)
        ),
        *extra_config,
    ]
    if cwd is None:
        return repo.git(c=config)
    return repo.git(c=config, C=str(cwd))


def repository_is_trusted(
    repo: Repo,
    trust_store: TrustedFoldersManager | None = None,
    *,
    trust_root: Path | None = None,
) -> bool:
    """Whether the user trusts the folder holding this repository's checkout."""
    root = repo.working_tree_dir
    folder = Path(root) if root is not None else Path(str(repo.git_dir)).parent
    return (trust_store or trusted_folders_manager).is_trusted(
        folder if trust_root is None else trust_root
    ) is True


def untrusted_status_options(
    repo: Repo,
    trust_store: TrustedFoldersManager | None = None,
    *,
    trust_root: Path | None = None,
) -> tuple[str, ...]:
    """Status options that keep git out of an untrusted repository's submodules.

    A submodule's own config defines filter drivers that the overrides
    above never see, and status runs them in each submodule it recurses into.
    """
    return (
        ()
        if repository_is_trusted(repo, trust_store, trust_root=trust_root)
        else ("--ignore-submodules=all",)
    )


def has_indexed_submodules(repo: Repo, *, base_commit: str | None = None) -> bool:
    """Detect current or removed gitlinks without opening submodules.

    Snapshots seed their index from HEAD, so a staged removal must still keep
    the submodule's unsaved contents. ls-files also needs the fsmonitor guard.
    """
    index = repo.git(c=["core.fsmonitor=", _NO_HOOKS_CONFIG]).ls_files("--stage", "-z")
    return any(entry.startswith("160000 ") for entry in index.split("\0")) or any(
        entry.startswith("160000 ")
        for ref in dict.fromkeys(("HEAD", base_commit))
        if ref is not None
        for entry in repo.git.ls_tree("-r", "-z", ref).split("\0")
    )


def _repository_filter_overrides(repo: Repo, *, cwd: Path | None = None) -> list[str]:
    """-c values that switch off every filter driver the repository defines.

    Inspect effective configuration in the command's checkout: includeIf can
    depend on its branch/gitdir, or on remote URLs from other config scopes.
    Local LFS extensions and transfer settings can turn even a global driver
    (possibly under another name or behind a wrapper) into repository-controlled
    execution. In that case, disable all effective drivers and preserve bytes.
    Older Git without --show-scope uses the same conservative fallback.
    """

    def listing(*options: str) -> str:
        try:
            command = repo.git if cwd is None else repo.git(C=str(cwd))
            return command.config(
                *options,
                "--includes",
                "--null",
                "--get-regexp",
                r"^(filter\.|lfs\.(extension\.|customtransfer\.|standalonetransferagent$))",
            )
        except _git_python().git_command_error as e:
            if e.status == _GIT_CONFIG_NO_MATCH_STATUS:
                return ""
            raise

    try:
        records = listing("--show-scope").split("\0")
    except _git_python().git_command_error as e:
        if e.status != _GIT_USAGE_ERROR_STATUS:
            raise GitError(
                f"Failed to read the repository's filter drivers: {e}"
            ) from e
        try:
            keys = [record.partition("\n")[0] for record in listing().split("\0")]
        except _git_python().git_command_error as fallback_error:
            raise GitError(
                f"Failed to read the repository's filter drivers: {fallback_error}"
            ) from fallback_error
        repository_keys = keys
    else:
        # --null emits scope NUL key LF value NUL, including embedded newlines
        # in values. Only Git parses config and evaluates conditional includes.
        if records[-1] != "" or len(records) % 2 != 1:
            raise GitError("Malformed scoped Git filter configuration")
        scoped_keys = [
            (records[i], records[i + 1].partition("\n")[0])
            for i in range(0, len(records) - 1, 2)
        ]
        if any(
            scope not in {"system", "global", "local", "worktree", "command"} or not key
            for scope, key in scoped_keys
        ):
            raise GitError("Malformed scoped Git filter configuration")
        keys = [key for _, key in scoped_keys]
        repository_keys = [
            key for scope, key in scoped_keys if scope in {"local", "worktree"}
        ]
    unsafe_lfs = any(key.startswith("lfs.") for key in repository_keys)
    drivers = {
        key.removeprefix("filter.").rpartition(".")[0]
        for key in (keys if unsafe_lfs else repository_keys)
        if key.startswith("filter.")
    }
    overrides: list[str] = []
    for driver in sorted(drivers):
        # -c splits at the first "=", so such a name cannot be overridden.
        if "=" in driver:
            raise GitError(f"Unsupported Git filter driver name {driver!r}")
        overrides.extend(f"filter.{driver}.{key}=" for key in _FILTER_COMMAND_KEYS)
        overrides.append(f"filter.{driver}.required=false")
    return overrides


class GitRepo:  # noqa: PLR0904 - every git operation goes through here
    """The one place GitPython is called and its failures become GitError.

    HEAD and refs are read with git commands, not GitPython's ref objects:
    those read .git/HEAD and the loose ref files, and a reftable repository
    keeps only the placeholder "ref: refs/heads/.invalid" there.
    """

    def __init__(
        self,
        repo: Repo,
        *,
        trust_store: TrustedFoldersManager | None = None,
        trust_root: Path | None = None,
    ) -> None:
        self._repo = repo
        self.trust_store = trust_store
        self._source_trust_root = trust_root

    @property
    def _trust_root(self) -> Path:
        return self._source_trust_root or self.paths.repo_root

    def run(
        self,
        subcommand: str,
        *args: str,
        cwd: Path | None = None,
        missing_ok: bool = False,
    ) -> str | None:
        """Run one git command, guarded as `sanitized_git` says, in `cwd` if given.

        Never prompts: a partial clone can fetch missing objects during a
        checkout, and these commands may run with nobody to answer. An
        untrusted repository keeps LFS pointers, as `_check_out` does.

        `missing_ok` answers None for exit status 1, which `git config --get`
        uses for a key that is not set.
        """
        command = getattr(self._sanitized_git(cwd=cwd), subcommand.replace("-", "_"))
        env = (
            _NON_INTERACTIVE_GIT_ENV
            if repository_is_trusted(
                self._repo, self.trust_store, trust_root=self._trust_root
            )
            else {**_NON_INTERACTIVE_GIT_ENV, "GIT_LFS_SKIP_SMUDGE": "1"}
        )
        try:
            return command(*args, env=env)
        except self._gitpy.git_command_error as e:
            if missing_ok and e.status == _GIT_CONFIG_NO_MATCH_STATUS:
                return None
            raise GitError(f"git {subcommand} failed: {e}") from e

    def _sanitized_git(self, *extra_config: str, cwd: Path | None = None) -> Git:
        return sanitized_git(
            self._repo,
            *extra_config,
            cwd=cwd,
            trust_store=self.trust_store,
            trust_root=self._trust_root,
        )

    @classmethod
    def open(
        cls,
        base: Path,
        *,
        trust_store: TrustedFoldersManager | None = None,
        trust_root: Path | None = None,
    ) -> Self:
        _require_git_executable(base)
        git = _git_python()
        try:
            return cls(
                git.repo(base, search_parent_directories=True),
                trust_store=trust_store,
                trust_root=trust_root,
            )
        except (git.invalid_git_repository_error, git.no_such_path_error) as e:
            raise GitRepositoryNotFoundError(
                "Path is not inside a git repository."
            ) from e

    # Opens the worktree in its own right rather than going through the
    # repository it is linked to, so the commit read is that worktree's HEAD.
    @classmethod
    def head_commit_at(cls, target: Path) -> str:
        _require_git_executable(target)
        git = _git_python()
        try:
            repo = git.repo(target)
        except (git.invalid_git_repository_error, git.no_such_path_error) as e:
            raise GitError(f"Failed to inspect HEAD for {target.name!r}: {e}") from e
        return cls(repo).head_commit()

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

    def _has_uncommitted_changes(self) -> bool:
        """Whether tracked or untracked work exists outside the current HEAD."""
        options = untrusted_status_options(
            self._repo, self.trust_store, trust_root=self._trust_root
        )
        status = self._sanitized_git().status(
            "--porcelain", "--untracked-files=all", *options
        )
        return bool(status.strip()) or bool(
            options and has_indexed_submodules(self._repo)
        )

    def github_remote(self) -> GitHubRemoteInfo | None:
        """The first GitHub remote, when this checkout has one."""
        return find_github_remote(self._repo)

    def has_commits(self) -> bool:
        """Whether HEAD resolves to a commit."""
        try:
            self._repo.git.rev_parse("--quiet", "--verify", "HEAD^{commit}")
        except self._gitpy.git_command_error as e:
            # An unborn HEAD names no commit; other failures are git's own.
            if e.status == 1:
                return False
            raise GitError(f"Failed to read the HEAD commit: {e}") from e
        return True

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
            ref = self._repo.git.symbolic_ref("--quiet", "HEAD")
        except self._gitpy.git_command_error as e:
            # Detached HEAD is checked out at a commit and has no branch name.
            if e.status == 1:
                return None
            raise GitError(f"Failed to read the current branch: {e}") from e
        return ref.removeprefix("refs/heads/")

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
        try:
            return self._repo.git.rev_parse("--verify", "HEAD^{commit}")
        except self._gitpy.git_command_error as e:
            raise GitError(f"Failed to read the HEAD commit: {e}") from e

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
            self._sanitized_git().branch("-D" if force else "-d", branch)
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
        # Use an explicit destination refspec so callers see the fetched tip
        # without trusting the repository's remote.<name>.fetch configuration.
        #
        # The wait is bounded here rather than with GitPython's
        # kill_after_timeout, which raises on Windows before it runs git at
        # all. Doing it directly also means one timeout on every platform, and
        # communicate() drains the pipes, so a fetch with a lot of progress
        # output cannot fill stderr and block on a full buffer.
        try:
            process = fetch_remote(
                self._repo,
                remote,
                (f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}",),
                allow_file=True,
                as_process=True,
                universal_newlines=True,
            )
        except (self._gitpy.git_command_error, UnsafeGitFetchError) as e:
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

    def add_worktree[T](
        self,
        target: Path,
        branch: str,
        *,
        branch_created: bool,
        start_point: str | None = None,
        on_checkout_progress: Callable[[int, int], None] | None = None,
        populate: Callable[[Path], T] | None = None,
    ) -> T | None:
        """Create the worktree, then check its files out.

        Two commands rather than one, because `worktree add` runs its checkout
        in a child whose progress git only prints to a terminal. Checking out
        separately with --progress reports it on a pipe too, as
        `on_checkout_progress(completed_files, total_files)`, called on this
        thread. It costs nothing measurable over the single command.

        `populate` runs between the two, on the registered but empty worktree,
        and its result is returned. Files it puts there that already match the
        index it leaves are not written again by the checkout.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            git = self._sanitized_git()
            if branch_created:
                create = ["add", "--no-checkout", "-b", branch, str(target)]
                # Omitted rather than defaulted, because git's own default is
                # the invoking checkout's HEAD and that is the right answer for
                # a repository with no remote to start from.
                if start_point is not None:
                    create.append(start_point)
                git.worktree(*create)
            else:
                git.worktree("add", "--no-checkout", str(target), branch)
        except self._gitpy.git_command_error as e:
            raise GitError(
                f"Failed to create worktree {target.name!r} for branch {branch!r}: {e}"
            ) from e
        try:
            populated = None if populate is None else populate(target)
            self._check_out(target, on_checkout_progress)
        except Exception as e:
            # The worktree is registered by now, and git only cleans up after a
            # failed `worktree add`, not after a failed checkout into one.
            with suppress(GitError):
                self.remove_worktree(target)
            if not isinstance(e, GitError):
                raise
            raise GitError(
                f"Failed to create worktree {target.name!r} for branch {branch!r}: {e}"
            ) from e
        return populated

    def _check_out(
        self, target: Path, on_progress: Callable[[int, int], None] | None
    ) -> None:
        git = self._sanitized_git(*self._checkout_config(), cwd=target)
        # Git LFS reads the repository's own config while it smudges (custom
        # transfers, extensions), so an untrusted checkout keeps the pointers.
        lfs = (
            {}
            if repository_is_trusted(
                self._repo, self.trust_store, trust_root=self._trust_root
            )
            else {"GIT_LFS_SKIP_SMUDGE": "1"}
        )
        # Held for the whole read: GitPython kills the process and closes its
        # pipes once this wrapper is garbage collected.
        process = git.checkout(
            "--progress",
            "--force",
            "--no-recurse-submodules",
            as_process=True,
            with_stdout=False,
            env=lfs,
        )
        proc = process.proc
        errors: list[bytes] = []
        pending = b""
        while chunk := proc.stderr.read1(4096):
            *lines, pending = re.split(rb"[\r\n]", pending + chunk)
            for line in lines:
                if match := _CHECKOUT_PROGRESS.search(line):
                    if on_progress is not None:
                        on_progress(int(match[1]), int(match[2]))
                elif line.strip():
                    errors.append(line)
        if proc.wait() != 0:
            raise GitError(b"\n".join([*errors, pending]).decode(errors="replace"))

    def _checkout_config(self) -> tuple[str, ...]:
        if self._repo.config_reader().has_option("checkout", "workers"):
            return ()
        return (f"checkout.workers={_CHECKOUT_WORKERS}",)

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
