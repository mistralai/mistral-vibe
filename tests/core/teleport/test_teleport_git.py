from __future__ import annotations

from pathlib import Path
import shlex
import shutil
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from git import Repo
from git.exc import GitCommandError
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
    mock.git.diff.return_value = diff
    mock.git.return_value.diff.return_value = diff
    mock.git.return_value.rev_parse.return_value = ".git/index"
    mock.git.branch.return_value = ""
    mock.git.rev_list.return_value = "0"
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

    @pytest.mark.asyncio
    async def test_raises_when_path_missing(self, tmp_path: Path) -> None:
        # Folder moved/deleted after selection: gitpython raises NoSuchPathError,
        # which must surface as an ineligible root, not a generic failure.
        repo = GitRepository(tmp_path / "gone")
        with pytest.raises(
            ServiceTeleportNotSupportedError, match="Teleport requires a git repository"
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

    @pytest.mark.asyncio
    async def test_get_metadata_skips_diff(self, repo: GitRepository) -> None:
        mock = make_mock_repo(
            urls=["git@github.com:owner/repo.git"],
            commit="abc123def456",
            branch="main",
            diff="diff content",
        )
        with patch.object(repo, "_repo_or_raise", return_value=mock):
            info = await repo.get_metadata()
        # Metadata lookup returns the same fields as get_info but never computes
        # the (potentially huge) working-tree diff.
        assert info.diff == ""
        assert info.repo == "repo"
        assert info.branch == "main"
        mock.git.diff.assert_not_called()


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

    @pytest.mark.parametrize(
        ("config_section", "config_option", "attributes"),
        [
            ("core", "fsmonitor", None),
            ("diff", "external", None),
            ('diff "malicious"', "textconv", "tracked.txt diff=malicious\n"),
        ],
    )
    @pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX marker command")
    @pytest.mark.asyncio
    async def test_disables_repository_diff_helpers(
        self,
        tmp_path: Path,
        config_section: str,
        config_option: str,
        attributes: str | None,
    ) -> None:
        source_repo = make_real_repo(tmp_path)
        if attributes is not None:
            (tmp_path / ".gitattributes").write_text(attributes)
            source_repo.index.add([".gitattributes"])
            source_repo.index.commit("add diff attributes")

        marker = tmp_path / "diff-helper-ran"
        source_repo.config_writer().set_value(
            config_section, config_option, f"touch {marker}"
        ).release()
        (tmp_path / "tracked.txt").write_text("changed\n")
        assert not marker.exists()

        async with GitRepository(tmp_path) as git_repo:
            diff = await git_repo._get_diff(source_repo)

        assert "diff --git a/tracked.txt b/tracked.txt" in diff
        assert not marker.exists()


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

    @pytest.mark.skipif(
        sys.platform == "win32" or shutil.which("ssh") is None,
        reason="uses a POSIX marker command and the system SSH client",
    )
    @pytest.mark.asyncio
    async def test_does_not_execute_repository_ssh_command(
        self, tmp_path: Path
    ) -> None:
        source_repo = make_real_repo(tmp_path)
        source_repo.create_remote("origin", "ssh://127.0.0.1:1/repo.git")
        marker = tmp_path / "ssh-command-ran"
        source_repo.config_writer().set_value(
            "core", "sshCommand", f"touch {shlex.quote(str(marker))}"
        ).release()

        async with GitRepository(tmp_path) as git_repo:
            pushed = await git_repo.is_commit_pushed(source_repo.head.commit.hexsha)

        assert pushed is False
        assert not marker.exists()


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
