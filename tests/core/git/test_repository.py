from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from git import Repo
from git.exc import GitCommandError
import pytest

from vibe.core.git.errors import GitRepositoryUnsupportedError
from vibe.core.git.repository import GitRepoInfo, GitRepository


def make_mock_remote(url: str, name: str = "origin") -> MagicMock:
    remote = MagicMock()
    remote.name = name
    remote.urls = [url]
    return remote


def make_mock_repo(
    urls: list[str] | None = None,
    remote_names: list[str] | None = None,
    commit: str | None = "abc123",
    branch: str | None = "main",
    is_detached: bool = False,
) -> MagicMock:
    mock = MagicMock()
    if urls:
        names = remote_names or [
            "origin" if index == 0 else f"remote-{index}"
            for index, _ in enumerate(urls)
        ]
        mock.remotes = [
            make_mock_remote(url, names[index]) for index, url in enumerate(urls)
        ]
    else:
        mock.remotes = []

    def symbolic_ref(*args: str) -> str:
        if args[-1] != "HEAD" or is_detached:
            raise GitCommandError("git symbolic-ref", 1)
        return f"refs/heads/{branch}"

    def rev_parse(*args: str) -> str:
        if args == ("--verify", "HEAD^{commit}") and commit is None:
            raise GitCommandError("git rev-parse", 128)
        return commit or "abc123"

    mock.git.symbolic_ref.side_effect = symbolic_ref
    mock.git.rev_parse.side_effect = rev_parse
    return mock


class TestGitRepositoryParseGithubUrl:
    def test_parse_ssh_url(self) -> None:
        result = GitRepository._parse_github_url("git@github.com:owner/repo.git")
        assert result == ("owner", "repo")

    def test_parse_ssh_url_without_git_suffix(self) -> None:
        result = GitRepository._parse_github_url("git@github.com:owner/repo")
        assert result == ("owner", "repo")

    def test_parse_https_url(self) -> None:
        result = GitRepository._parse_github_url("https://github.com/owner/repo.git")
        assert result == ("owner", "repo")

    def test_parse_https_url_without_git_suffix(self) -> None:
        result = GitRepository._parse_github_url("https://github.com/owner/repo")
        assert result == ("owner", "repo")

    def test_parse_https_url_with_credentials(self) -> None:
        result = GitRepository._parse_github_url(
            "https://x-access-token:gho_xxxx@github.com/owner/repo.git"
        )
        assert result == ("owner", "repo")

    def test_parse_non_github_url_returns_none(self) -> None:
        result = GitRepository._parse_github_url("git@gitlab.com:owner/repo.git")
        assert result is None

    def test_parse_invalid_url_returns_none(self) -> None:
        result = GitRepository._parse_github_url("not-a-valid-url")
        assert result is None


class TestGitRepositoryToHttpsUrl:
    def test_converts_to_https_url(self) -> None:
        result = GitRepository._to_https_url("owner", "repo")
        assert result == "https://github.com/owner/repo.git"


class TestGitRepositoryGetMetadata:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> GitRepository:
        return GitRepository(tmp_path)

    @pytest.mark.asyncio
    async def test_raises_when_not_git_repo(self, tmp_path: Path) -> None:
        repo = GitRepository(tmp_path)
        with pytest.raises(
            GitRepositoryUnsupportedError, match="Vibe Code requires a git repository"
        ):
            await repo.get_metadata()

    @pytest.mark.asyncio
    async def test_raises_when_no_remote(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=None)
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            with pytest.raises(GitRepositoryUnsupportedError, match="No GitHub remote"):
                await repo.get_metadata()

    @pytest.mark.asyncio
    async def test_raises_when_non_github_remote(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@gitlab.com:owner/repo.git"])
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            with pytest.raises(GitRepositoryUnsupportedError, match="No GitHub remote"):
                await repo.get_metadata()

    @pytest.mark.asyncio
    async def test_raises_when_no_commit(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], commit=None)
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            with pytest.raises(
                GitRepositoryUnsupportedError,
                match="Could not determine current commit",
            ):
                await repo.get_metadata()

    @pytest.mark.asyncio
    async def test_returns_info_on_success(self, repo: GitRepository) -> None:
        mock = make_mock_repo(
            urls=["git@github.com:owner/repo.git"], commit="abc123def456", branch="main"
        )
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            info = await repo.get_metadata()
            assert info == GitRepoInfo(
                remote_name="origin",
                remote_url="https://github.com/owner/repo.git",
                owner="owner",
                repo="repo",
                branch="main",
                commit="abc123def456",
            )

    @pytest.mark.asyncio
    async def test_returns_remote_default_branch_on_success(
        self, repo: GitRepository
    ) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], branch="feature")
        with (
            patch.object(repo, "_repo_or_raise", return_value=mock),
            patch.object(
                repo, "_get_remote_default_branch", AsyncMock(return_value="origin/dev")
            ),
        ):
            info = await repo.get_metadata()

        assert info.branch == "feature"
        assert info.default_branch == "dev"

    @pytest.mark.asyncio
    async def test_returns_matched_github_remote_name(
        self, repo: GitRepository
    ) -> None:
        mock = make_mock_repo(
            urls=["git@gitlab.com:owner/repo.git", "git@github.com:owner/repo.git"],
            remote_names=["origin", "hub"],
        )
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            info = await repo.get_metadata()
            assert info.remote_name == "hub"
            assert info.remote_url == "https://github.com/owner/repo.git"

    @pytest.mark.asyncio
    async def test_handles_detached_head(self, repo: GitRepository) -> None:
        mock = make_mock_repo(
            urls=["git@github.com:owner/repo.git"],
            commit="abc123def456",
            is_detached=True,
        )
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            info = await repo.get_metadata()
            assert info.branch is None

    @pytest.mark.asyncio
    async def test_raises_when_path_missing(self, tmp_path: Path) -> None:
        # Folder moved/deleted after selection: gitpython raises NoSuchPathError,
        # which must surface as an ineligible root, not a generic failure.
        repo = GitRepository(tmp_path / "gone")
        with pytest.raises(
            GitRepositoryUnsupportedError, match="Vibe Code requires a git repository"
        ):
            await repo.get_metadata()

    @pytest.mark.parametrize(
        ("git_cli_repo", "block_ref_files"),
        [("files", True), ("reftable", False)],
        indirect=["git_cli_repo"],
    )
    @pytest.mark.asyncio
    async def test_reads_branch_commit_and_default_branch(
        self,
        tmp_path: Path,
        git_cli_repo: Repo,
        block_ref_files: bool,
        request: pytest.FixtureRequest,
    ) -> None:
        # Reftable needs git 2.45 or later; blocking GitPython's ref file reads
        # checks the same code on any git.
        source_repo = git_cli_repo
        source_repo.git.commit("--allow-empty", "-m", "initial")
        source_repo.git.remote("add", "origin", "git@github.com:owner/repo.git")
        source_repo.git.update_ref("refs/remotes/origin/main", "HEAD")
        source_repo.git.symbolic_ref(
            "refs/remotes/origin/HEAD", "refs/remotes/origin/main"
        )
        source_repo.git.checkout("-b", "feature")
        if block_ref_files:
            request.getfixturevalue("no_gitpython_ref_files")

        async with GitRepository(tmp_path) as git_repo:
            info = await git_repo.get_metadata()

        assert info.branch == "feature"
        assert info.commit == source_repo.git.rev_parse("HEAD")
        assert info.default_branch == "main"
