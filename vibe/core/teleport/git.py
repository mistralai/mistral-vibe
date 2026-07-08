from __future__ import annotations

from configparser import NoOptionError, NoSectionError
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from git import InvalidGitRepositoryError, Repo
from git.exc import GitCommandError
from giturlparse import parse as parse_git_url

from vibe.core.teleport.errors import (
    ServiceTeleportError,
    ServiceTeleportNotSupportedError,
)
from vibe.core.utils import AsyncExecutor


@dataclass
class GitHubRemoteInfo:
    name: str
    owner: str
    repo: str


@dataclass
class GitRepoInfo:
    remote_name: str
    remote_url: str
    owner: str
    repo: str
    branch: str | None
    commit: str
    diff: str
    default_branch: str | None = None


class GitRepository:
    def __init__(self, workdir: Path | None = None) -> None:
        self._workdir = workdir or Path.cwd()
        self._repo: Repo | None = None
        # For network I/O (fetch, push) and potentially slow git commands (diff, rev-list)
        self._executor = AsyncExecutor(max_workers=2, timeout=60.0, name="git")

    async def __aenter__(self) -> GitRepository:
        return self

    async def __aexit__(self, *_: object) -> None:
        self._executor.shutdown(wait=False)

    async def is_supported(self) -> bool:
        try:
            repo = self._repo_or_raise()
        except ServiceTeleportNotSupportedError:
            return False
        return self._find_github_remote(repo) is not None

    async def get_info(self) -> GitRepoInfo:
        repo = self._repo_or_raise()

        parsed = self._find_github_remote(repo)
        if not parsed:
            raise ServiceTeleportNotSupportedError(
                "No GitHub remote found. Teleport only supports GitHub repositories."
            )

        try:
            commit = repo.head.commit.hexsha
        except (ValueError, TypeError) as e:
            raise ServiceTeleportNotSupportedError(
                "Could not determine current commit"
            ) from e

        if not commit:
            raise ServiceTeleportNotSupportedError("Could not determine current commit")

        owner = parsed.owner
        repo_name = parsed.repo
        branch = None if repo.head.is_detached else repo.active_branch.name
        default_branch = _remote_ref_branch_name(
            await self._get_remote_default_branch(repo, parsed.name), parsed.name
        )
        diff = await self._get_diff(repo)

        return GitRepoInfo(
            remote_name=parsed.name,
            remote_url=self._to_https_url(owner, repo_name),
            owner=owner,
            repo=repo_name,
            branch=branch,
            commit=commit,
            diff=diff,
            default_branch=default_branch,
        )

    async def fetch(self, remote: str = "origin") -> None:
        repo = self._repo_or_raise()
        await self._fetch(repo, remote)

    async def is_commit_pushed(
        self, commit: str, remote: str = "origin", *, fetch: bool = True
    ) -> bool:
        repo = self._repo_or_raise()
        if fetch:
            await self._fetch(repo, remote)
        return await self._branch_contains(repo, commit, remote)

    async def is_branch_pushed(
        self, remote: str = "origin", *, fetch: bool = True
    ) -> bool:
        repo = self._repo_or_raise()
        if repo.head.is_detached:
            return True  # Detached HEAD doesn't have a branch to check
        branch = repo.active_branch.name
        if fetch:
            await self._fetch(repo, remote)
        return await self._ref_exists(repo, f"{remote}/{branch}")

    async def get_unpushed_commit_count(self, remote: str = "origin") -> int:
        repo = self._repo_or_raise()

        if repo.head.is_detached:
            raise ServiceTeleportError(
                "Cannot count unpushed commits: no current branch"
            )
        branch = repo.active_branch.name

        await self._fetch(repo, remote)

        result = await self._rev_list_count(repo, f"{remote}/{branch}..HEAD")
        if result is not None:
            return result

        # Fallback: branch not pushed yet, count commits from default branch
        default_branch = await self._get_remote_default_branch(repo, remote)
        if default_branch:
            result = await self._rev_list_count(repo, f"{default_branch}..HEAD")
            if result is not None:
                return result

        raise ServiceTeleportError(f"Failed to count unpushed commits for {branch}")

    async def push_current_branch(self, remote: str = "origin") -> bool:
        repo = self._repo_or_raise()
        if repo.head.is_detached:
            return False
        return await self._push(repo, repo.active_branch.name, remote)

    def _repo_or_raise(self) -> Repo:
        if self._repo is None:
            try:
                self._repo = Repo(self._workdir, search_parent_directories=True)
            except InvalidGitRepositoryError as e:
                raise ServiceTeleportNotSupportedError(
                    "Teleport requires a git repository. cd into a project with a .git directory and try again."
                ) from e
        return self._repo

    def _find_github_remote(self, repo: Repo) -> GitHubRemoteInfo | None:
        for remote in repo.remotes:
            for url in self._remote_urls(remote):
                if parsed := self._parse_github_url(url):
                    owner, repo_name = parsed
                    return GitHubRemoteInfo(
                        name=remote.name, owner=owner, repo=repo_name
                    )
        return None

    @staticmethod
    def _remote_urls(remote: object) -> list[str]:
        urls: list[str] = []
        config_reader = getattr(remote, "config_reader", None)
        try:
            raw_url = config_reader.get("url") if config_reader is not None else None
        except (AttributeError, NoOptionError, NoSectionError, TypeError, ValueError):
            raw_url = None
        if isinstance(raw_url, str):
            urls.append(raw_url)

        for url in getattr(remote, "urls", ()):
            if isinstance(url, str) and url not in urls:
                urls.append(url)
        return urls

    async def _fetch(self, repo: Repo, remote: str) -> None:
        try:
            await self._executor.run(lambda: repo.remote(remote).fetch())
        except (TimeoutError, ValueError, GitCommandError):
            pass

    async def _get_diff(self, repo: Repo) -> str:
        def get_full_diff() -> str:
            temporary_dir = Path(tempfile.mkdtemp(prefix="vibe-teleport-index-"))
            temporary_index = temporary_dir / "index"
            try:
                index_path = Path(repo.index.path)
                if index_path.exists():
                    shutil.copy2(index_path, temporary_index)
                with repo.git.custom_environment(GIT_INDEX_FILE=str(temporary_index)):
                    repo.git.add("-N", ".")
                    return repo.git.diff("HEAD", binary=True)
            finally:
                shutil.rmtree(temporary_dir, ignore_errors=True)

        try:
            return await self._executor.run(get_full_diff)
        except (TimeoutError, GitCommandError, OSError):
            return ""

    async def _branch_contains(self, repo: Repo, commit: str, remote: str) -> bool:
        try:
            out = await self._executor.run(
                lambda: repo.git.branch("-r", "--contains", commit)
            )
            return any(ln.strip().startswith(f"{remote}/") for ln in out.splitlines())
        except (TimeoutError, GitCommandError):
            return False

    async def _rev_list_count(self, repo: Repo, ref_range: str) -> int | None:
        try:
            out = await self._executor.run(
                lambda: repo.git.rev_list("--count", ref_range)
            )
            return int(out)
        except (TimeoutError, GitCommandError, ValueError):
            return None

    async def _ref_exists(self, repo: Repo, ref: str) -> bool:
        try:
            await self._executor.run(lambda: repo.git.rev_parse("--verify", ref))
            return True
        except (TimeoutError, GitCommandError):
            return False

    async def _get_remote_default_branch(self, repo: Repo, remote: str) -> str | None:
        try:
            ref = repo.remotes[remote].refs.HEAD.reference.name
            if await self._ref_exists(repo, ref):
                return ref
        except (KeyError, IndexError, TypeError, AttributeError):
            pass
        return None

    async def _push(self, repo: Repo, branch: str, remote: str) -> bool:
        try:
            result = await self._executor.run(
                lambda: repo.remote(remote).push(branch, set_upstream=True)
            )
            # Check if any push info indicates an error
            for info in result:
                if info.flags & info.ERROR:
                    return False
            return True
        except (TimeoutError, ValueError, GitCommandError):
            return False

    @staticmethod
    def _parse_github_url(url: str) -> tuple[str, str] | None:
        p = parse_git_url(url)
        if p.github and p.owner and p.repo:
            return p.owner, p.repo
        return None

    @staticmethod
    def _to_https_url(owner: str, repo: str) -> str:
        return f"https://github.com/{owner}/{repo}.git"


def _remote_ref_branch_name(ref: str | None, remote: str) -> str | None:
    if ref is None:
        return None
    prefix = f"{remote}/"
    if ref.startswith(prefix):
        return ref.removeprefix(prefix)
    return ref
