from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vibe.core.git.errors import (
    GitError,
    GitRepositoryError,
    GitRepositoryUnsupportedError,
)
from vibe.core.git.remote import (
    GitHubRemoteInfo,
    find_github_remote,
    parse_github_url,
    to_https_url,
)
from vibe.core.git.repo import GitRepo
from vibe.core.utils import AsyncExecutor
from vibe.utils.platform import (
    configure_git_python_executable,
    git_python_import_environment,
)

_GIT_EXECUTABLE = configure_git_python_executable()
if _GIT_EXECUTABLE is None:
    raise GitRepositoryError(
        "Vibe Code requires a trusted Git executable. Install Git or set "
        "GIT_PYTHON_GIT_EXECUTABLE to an absolute path."
    )

try:
    with git_python_import_environment(_GIT_EXECUTABLE):
        from git import Git, InvalidGitRepositoryError, NoSuchPathError, Repo, refresh
        from git.exc import GitCommandError, GitCommandNotFound
except ImportError as e:
    raise GitRepositoryError(
        "Vibe Code requires git to be installed. Please install git and try again."
    ) from e

try:
    if Git.GIT_PYTHON_GIT_EXECUTABLE != _GIT_EXECUTABLE:
        refresh(_GIT_EXECUTABLE)
except (GitCommandNotFound, PermissionError) as e:
    raise GitRepositoryError(
        "Vibe Code requires git to be installed. Please install git and try again."
    ) from e


@dataclass
class GitRepoInfo:
    remote_name: str
    remote_url: str
    owner: str
    repo: str
    branch: str | None
    commit: str
    default_branch: str | None = None
    repo_root: Path | None = None


class GitRepository:
    def __init__(self, workdir: Path | None = None) -> None:
        self._workdir = workdir or Path.cwd()
        self._repo: Repo | None = None
        self._executor = AsyncExecutor(max_workers=2, timeout=60.0, name="git")

    async def __aenter__(self) -> GitRepository:
        return self

    async def __aexit__(self, *_: object) -> None:
        self._executor.shutdown(wait=False)

    async def get_metadata(self) -> GitRepoInfo:
        repo = self._repo_or_raise()

        parsed = self._find_github_remote(repo)
        if not parsed:
            raise GitRepositoryUnsupportedError(
                "No GitHub remote found. Vibe Code only supports GitHub repositories."
            )

        try:
            commit = GitRepo(repo).head_commit()
        except GitError as e:
            raise GitRepositoryUnsupportedError(
                "Could not determine current commit"
            ) from e

        owner = parsed.owner
        repo_name = parsed.repo
        branch = _current_branch(repo)
        default_branch = _remote_ref_branch_name(
            await self._get_remote_default_branch(repo, parsed.name), parsed.name
        )

        return GitRepoInfo(
            remote_name=parsed.name,
            remote_url=self._to_https_url(owner, repo_name),
            owner=owner,
            repo=repo_name,
            branch=branch,
            commit=commit,
            default_branch=default_branch,
            repo_root=_repo_root_path(repo),
        )

    def _repo_or_raise(self) -> Repo:
        if self._repo is None:
            try:
                self._repo = Repo(self._workdir, search_parent_directories=True)
            except (InvalidGitRepositoryError, NoSuchPathError) as e:
                # NoSuchPathError: the workdir was moved/deleted between the
                # picker selecting it and this call. Surface it as an ineligible
                # root (mapped to a clear reject reason) instead of a generic
                # failure.
                raise GitRepositoryUnsupportedError(
                    "Vibe Code requires a git repository. cd into a project with a .git directory and try again."
                ) from e
        return self._repo

    @staticmethod
    def _find_github_remote(repo: Repo) -> GitHubRemoteInfo | None:
        return find_github_remote(repo)

    async def _ref_exists(self, repo: Repo, ref: str) -> bool:
        try:
            await self._executor.run(lambda: repo.git.rev_parse("--verify", ref))
            return True
        except (TimeoutError, GitCommandError):
            return False

    async def _get_remote_default_branch(self, repo: Repo, remote: str) -> str | None:
        ref = GitRepo(repo).remote_default_branch_ref(remote)
        if ref is not None and await self._ref_exists(repo, ref):
            return ref
        return None

    @staticmethod
    def _parse_github_url(url: str) -> tuple[str, str] | None:
        return parse_github_url(url)

    @staticmethod
    def _to_https_url(owner: str, repo: str) -> str:
        return to_https_url(owner, repo)


def _current_branch(repo: Repo) -> str | None:
    try:
        return GitRepo(repo).branch()
    except GitError as e:
        raise GitRepositoryError(str(e)) from e


def _remote_ref_branch_name(ref: str | None, remote: str) -> str | None:
    if ref is None:
        return None
    prefix = f"{remote}/"
    if ref.startswith(prefix):
        return ref.removeprefix(prefix)
    return ref


def _repo_root_path(repo: Repo) -> Path | None:
    working_tree_dir = repo.working_tree_dir
    if not isinstance(working_tree_dir, str):
        return None
    return Path(working_tree_dir).resolve()
