from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from git import Repo
import pytest

from vibe.core.teleport.errors import (
    ServiceTeleportError,
    ServiceTeleportNotSupportedError,
)
from vibe.core.teleport.git import GitRepoInfo, GitRepository


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
    diff: str = "",
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
    mock.head.commit.hexsha = commit
    mock.head.is_detached = is_detached
    mock.active_branch.name = branch
    mock.git.diff.return_value = diff
    mock.git.branch.return_value = ""
    mock.git.rev_list.return_value = "0"
    mock.git.rev_parse.return_value = "abc123"
    mock.index.path = str(Path("/tmp/mock-index"))
    mock.remote.return_value = make_mock_remote(urls[0] if urls else "")
    return mock


def make_real_repo(workdir: Path) -> Repo:
    repo = Repo.init(workdir, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (workdir / "tracked.txt").write_text("base\n")
    repo.index.add(["tracked.txt"])
    repo.index.commit("initial")
    return repo


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


class TestGitRepositoryIsSupported:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> GitRepository:
        return GitRepository(tmp_path)

    @pytest.mark.asyncio
    async def test_returns_false_when_not_git_repo(self, tmp_path: Path) -> None:
        repo = GitRepository(tmp_path)
        assert await repo.is_supported() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_remote(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=None)
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.is_supported() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_non_github_remote(
        self, repo: GitRepository
    ) -> None:
        mock = make_mock_repo(urls=["git@gitlab.com:owner/repo.git"])
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.is_supported() is False

    @pytest.mark.asyncio
    async def test_returns_true_when_github_repo(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"])
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.is_supported() is True

    @pytest.mark.asyncio
    async def test_finds_github_among_multiple_remotes(
        self, repo: GitRepository
    ) -> None:
        mock = make_mock_repo(
            urls=["git@gitlab.com:owner/repo.git", "git@github.com:owner/repo.git"]
        )
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.is_supported() is True


class TestGitRepositoryGetInfo:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> GitRepository:
        return GitRepository(tmp_path)

    @pytest.mark.asyncio
    async def test_raises_when_not_git_repo(self, tmp_path: Path) -> None:
        repo = GitRepository(tmp_path)
        with pytest.raises(
            ServiceTeleportNotSupportedError, match="Teleport requires a git repository"
        ):
            await repo.get_info()

    @pytest.mark.asyncio
    async def test_raises_when_no_remote(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=None)
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            with pytest.raises(
                ServiceTeleportNotSupportedError, match="No GitHub remote"
            ):
                await repo.get_info()

    @pytest.mark.asyncio
    async def test_raises_when_non_github_remote(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@gitlab.com:owner/repo.git"])
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            with pytest.raises(
                ServiceTeleportNotSupportedError, match="No GitHub remote"
            ):
                await repo.get_info()

    @pytest.mark.asyncio
    async def test_raises_when_no_commit(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], commit=None)
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            with pytest.raises(
                ServiceTeleportNotSupportedError,
                match="Could not determine current commit",
            ):
                await repo.get_info()

    @pytest.mark.asyncio
    async def test_returns_info_on_success(self, repo: GitRepository) -> None:
        mock = make_mock_repo(
            urls=["git@github.com:owner/repo.git"],
            commit="abc123def456",
            branch="main",
            diff="diff content",
        )
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            info = await repo.get_info()
            assert info == GitRepoInfo(
                remote_name="origin",
                remote_url="https://github.com/owner/repo.git",
                owner="owner",
                repo="repo",
                branch="main",
                commit="abc123def456",
                diff="diff content",
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
            info = await repo.get_info()

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
            info = await repo.get_info()
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
            info = await repo.get_info()
            assert info.branch is None


class TestGitRepositoryGetDiff:
    @pytest.mark.asyncio
    async def test_includes_untracked_files_without_mutating_index(
        self, tmp_path: Path
    ) -> None:
        source_repo = make_real_repo(tmp_path)
        (tmp_path / "new.txt").write_text("hello\n")
        status_before = source_repo.git.status("--short")

        async with GitRepository(tmp_path) as git_repo:
            diff = await git_repo._get_diff(source_repo)

        assert "diff --git a/new.txt b/new.txt" in diff
        assert source_repo.git.status("--short") == status_before


class TestGitRepositoryIsCommitPushed:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> GitRepository:
        return GitRepository(tmp_path)

    @pytest.mark.asyncio
    async def test_returns_false_when_not_on_remote(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"])
        mock.git.branch.return_value = ""
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.is_commit_pushed("abc123") is False

    @pytest.mark.asyncio
    async def test_returns_true_when_on_remote(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"])
        mock.git.branch.return_value = "  origin/main\n  origin/feature"
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.is_commit_pushed("abc123") is True

    @pytest.mark.asyncio
    async def test_checks_correct_remote(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"])
        mock.git.branch.return_value = "  upstream/main\n"
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.is_commit_pushed("abc123", remote="upstream") is True
            assert await repo.is_commit_pushed("abc123", remote="origin") is False


class TestGitRepositoryGetUnpushedCommitCount:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> GitRepository:
        return GitRepository(tmp_path)

    @pytest.mark.asyncio
    async def test_raises_when_no_branch(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], is_detached=True)
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            with pytest.raises(ServiceTeleportError, match="no current branch"):
                await repo.get_unpushed_commit_count()

    @pytest.mark.asyncio
    async def test_returns_count(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], branch="main")
        mock.git.rev_list.return_value = "5"
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.get_unpushed_commit_count() == 5

    @pytest.mark.asyncio
    async def test_fallback_to_default_branch(self, repo: GitRepository) -> None:
        from git.exc import GitCommandError

        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], branch="feature")

        def rev_list_side_effect(*args: object, **kwargs: object) -> str:
            if "origin/feature..HEAD" in args:
                raise GitCommandError("git rev-list", 128)
            if "origin/main..HEAD" in args:
                return "3"
            return "0"

        mock.git.rev_list.side_effect = rev_list_side_effect
        with (
            patch.object(repo, "_repo_or_raise", return_value=mock),
            patch.object(
                repo,
                "_get_remote_default_branch",
                AsyncMock(return_value="origin/main"),
            ),
        ):
            assert await repo.get_unpushed_commit_count() == 3


class TestGitRepositoryPushCurrentBranch:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> GitRepository:
        return GitRepository(tmp_path)

    @pytest.mark.asyncio
    async def test_returns_false_when_no_branch(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], is_detached=True)
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.push_current_branch() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_push_fails(self, repo: GitRepository) -> None:
        from git.exc import GitCommandError

        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], branch="main")
        mock.remote.return_value.push.side_effect = GitCommandError("git push", 1)
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.push_current_branch() is False

    @pytest.mark.asyncio
    async def test_returns_true_when_push_succeeds(self, repo: GitRepository) -> None:
        mock = make_mock_repo(urls=["git@github.com:owner/repo.git"], branch="main")
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            assert await repo.push_current_branch() is True


class TestGitRepositoryGetRemoteDefaultBranch:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> GitRepository:
        return GitRepository(tmp_path)

    @pytest.mark.asyncio
    async def test_returns_default_branch_when_head_exists(
        self, repo: GitRepository
    ) -> None:
        mock = MagicMock()
        mock_head_ref = MagicMock()
        mock_head_ref.reference.name = "origin/main"
        mock.remotes.__getitem__.return_value.refs.HEAD = mock_head_ref
        mock.git.rev_parse.return_value = "abc123"
        result = await repo._get_remote_default_branch(mock, "origin")
        assert result == "origin/main"

    @pytest.mark.asyncio
    async def test_returns_none_when_remote_not_found(
        self, repo: GitRepository
    ) -> None:
        mock = MagicMock()
        mock.remotes.__getitem__.side_effect = IndexError("No item found")
        result = await repo._get_remote_default_branch(mock, "upstream")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_head_ref_missing(
        self, repo: GitRepository
    ) -> None:
        mock = MagicMock()
        mock.remotes.__getitem__.return_value.refs.HEAD = None
        result = await repo._get_remote_default_branch(mock, "origin")
        assert result is None
