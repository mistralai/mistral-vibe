"""Tests for the forge discovery module (vibe.core.git.forge).

Phase 2: ``discover_pull_requests`` enumerates the open PRs/MRs linked to a
local checkout's current branch via ``gh pr list --head`` / ``glab mr list
--source-branch``. The git repository, the CLI binaries and the subprocess
are all replaced with fakes — no network, no real git repo, no real CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from vibe.core.git.errors import GitRepositoryNotFoundError
from vibe.core.git.forge import (
    DiscoveredPullRequest,
    ForgeError,
    _detect_provider,
    discover_pull_requests,
)

GH_JSON = json.dumps([
    {
        "number": 123,
        "title": "Fix login",
        "url": "https://github.com/mistralai/vibe/pull/123",
        "headRefName": "fix-login",
        "baseRefName": "main",
        "isDraft": False,
        "author": {"login": "pierre"},
    },
    {
        "number": 124,
        "title": "WIP: experiment",
        "url": "https://github.com/mistralai/vibe/pull/124",
        "headRefName": "experiment",
        "baseRefName": "main",
        "isDraft": True,
        "author": {"login": "bot"},
    },
])

GLAB_JSON = json.dumps([
    {
        "iid": 7,
        "title": "Add feature",
        "web_url": "https://gitlab.com/team/project/-/merge_requests/7",
        "source_branch": "feature",
        "target_branch": "main",
        "draft": False,
        "author": {"username": "pierre"},
    }
])


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fake_repo_cls(repo_url: str | None, branch: str | None = "main") -> Any:
    """A WorktreeRepository double: ``open`` yields an object whose status()
    reports *repo_url* and *branch* (None = detached HEAD).
    """

    class _FakeRepo:
        def status(self) -> Any:
            return SimpleNamespace(repo_url=repo_url, branch=branch)

    class _FakeWorktreeRepository:
        @classmethod
        def open(cls, base: Path, **_kwargs: Any) -> Any:
            import contextlib

            @contextlib.contextmanager
            def _cm() -> Any:
                yield _FakeRepo()

            return _cm()

    return _FakeWorktreeRepository


def _not_a_repo_cls() -> Any:
    class _FakeWorktreeRepository:
        @classmethod
        def open(cls, base: Path, **_kwargs: Any) -> Any:
            raise GitRepositoryNotFoundError("Path is not inside a git repository.")

    return _FakeWorktreeRepository


class TestDetectProvider:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/mistralai/vibe.git",
            "git@github.com:mistralai/vibe.git",
            "https://github.com/mistralai/vibe",
        ],
    )
    def test_github(self, url: str):
        assert _detect_provider(url) == "github"

    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.com/team/project.git",
            "https://gitlab.com/team/subgroup/project.git",
            "git@gitlab.mycompany.com:team/project.git",
        ],
    )
    def test_gitlab(self, url: str):
        assert _detect_provider(url) == "gitlab"

    @pytest.mark.parametrize(
        "url",
        [
            # GitHub Enterprise: the host alone does not prove GitHub — skipped
            # in Phase 2 (documented limitation, local-only scope).
            "https://github.mycompany.com/org/repo.git",
            "https://bitbucket.org/org/repo.git",
            "https://example.com/org/repo.git",
        ],
    )
    def test_unsupported(self, url: str):
        assert _detect_provider(url) is None


class TestRunGhPrList:
    def test_parses_prs(self):
        from vibe.core.git.forge import _run_gh_pr_list

        with patch(
            "vibe.core.git.forge.subprocess.run", return_value=_completed(GH_JSON)
        ):
            prs = _run_gh_pr_list(
                "/usr/bin/gh", "mistralai", "vibe", "github.com", "main"
            )
        assert len(prs) == 2
        first, second = prs
        assert first == DiscoveredPullRequest(
            provider="github",
            number=123,
            title="Fix login",
            url="https://github.com/mistralai/vibe/pull/123",
            source_branch="fix-login",
            target_branch="main",
            state="open",
            author="pierre",
            repo_host="github.com",
            repo_owner="mistralai",
            repo_name="vibe",
        )
        assert second.state == "draft"
        assert second.author == "bot"

    def test_empty_list(self):
        from vibe.core.git.forge import _run_gh_pr_list

        with patch("vibe.core.git.forge.subprocess.run", return_value=_completed("[]")):
            assert (
                _run_gh_pr_list(
                    "/usr/bin/gh", "mistralai", "vibe", "github.com", "main"
                )
                == []
            )

    def test_command_failure_raises_forge_error(self):
        from vibe.core.git.forge import _run_gh_pr_list

        with (
            patch(
                "vibe.core.git.forge.subprocess.run",
                return_value=_completed("", "gh: Not authenticated", returncode=1),
            ),
            pytest.raises(ForgeError, match="gh pr list failed"),
        ):
            _run_gh_pr_list("/usr/bin/gh", "mistralai", "vibe", "github.com", "main")

    def test_timeout_raises_forge_error(self):
        """A slow forge answer is a ForgeError, not an escaped TimeoutExpired."""
        from vibe.core.git.forge import _run_gh_pr_list

        with (
            patch(
                "vibe.core.git.forge.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["gh"], timeout=10),
            ),
            pytest.raises(ForgeError, match="timed out"),
        ):
            _run_gh_pr_list("/usr/bin/gh", "mistralai", "vibe", "github.com", "main")

    def test_oserror_raises_forge_error(self):
        """An unexecutable CLI is a ForgeError, not an escaped OSError."""
        from vibe.core.git.forge import _run_gh_pr_list

        with (
            patch(
                "vibe.core.git.forge.subprocess.run",
                side_effect=OSError("Permission denied"),
            ),
            pytest.raises(ForgeError, match="could not be executed"),
        ):
            _run_gh_pr_list("/usr/bin/gh", "mistralai", "vibe", "github.com", "main")

    def test_output_decoded_as_utf8(self):
        """PR titles are not ASCII: decoding must not depend on the locale
        codepage (Windows ANSI would mangle or reject them).
        """
        from vibe.core.git.forge import _run_gh_pr_list

        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_gh_pr_list("/usr/bin/gh", "o", "r", "github.com", "main")
        assert captured["text"] is True
        assert captured["encoding"] == "utf-8"
        assert captured["errors"] == "replace"

    def test_invalid_json_raises_forge_error(self):
        from vibe.core.git.forge import _run_gh_pr_list

        with (
            patch(
                "vibe.core.git.forge.subprocess.run",
                return_value=_completed("not json"),
            ),
            pytest.raises(ForgeError, match="invalid JSON"),
        ):
            _run_gh_pr_list("/usr/bin/gh", "mistralai", "vibe", "github.com", "main")

    def test_skips_entries_missing_required_fields(self):
        from vibe.core.git.forge import _run_gh_pr_list

        payload = json.dumps([
            {"number": 1, "title": "ok"},  # missing url/head/base
            {
                "number": 2,
                "title": "full",
                "url": "https://github.com/o/r/pull/2",
                "headRefName": "b",
                "baseRefName": "main",
                "isDraft": False,
                "author": "no-dict",
            },
        ])
        with patch(
            "vibe.core.git.forge.subprocess.run", return_value=_completed(payload)
        ):
            prs = _run_gh_pr_list("/usr/bin/gh", "o", "r", "github.com", "main")
        assert len(prs) == 1
        assert prs[0].number == 2
        assert prs[0].author == ""  # non-dict author degrades to ""

    def test_full_environment_passed_to_subprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """gh reads its token and hosts config from the environment — the
        subprocess must inherit it, not run with a stripped env. GH_HOST is
        the one addition: pinned to the checkout's remote host.
        """
        from vibe.core.git.forge import _run_gh_pr_list

        monkeypatch.delenv("GH_HOST", raising=False)
        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_gh_pr_list("/usr/bin/gh", "o", "r", "github.com", "main")
        import os

        expected = dict(os.environ)
        expected["GH_HOST"] = "github.com"
        assert captured["env"] == expected

    def test_inherited_gh_host_is_overridden(self, monkeypatch: pytest.MonkeyPatch):
        """An inherited GH_HOST (e.g. a shell configured for a GHES instance)
        must not steer discovery at a different forge than the checkout's
        remote points to.
        """
        from vibe.core.git.forge import _run_gh_pr_list

        monkeypatch.setenv("GH_HOST", "ghes.example.com")
        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_gh_pr_list("/usr/bin/gh", "o", "r", "github.com", "main")
        assert captured["env"]["GH_HOST"] == "github.com"

    def test_head_branch_is_passed(self):
        """Discovery is branch-scoped: the session's branch reaches --head."""
        from vibe.core.git.forge import _run_gh_pr_list

        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_gh_pr_list("/usr/bin/gh", "o", "r", "github.com", "feature/x")
        cmd = captured["cmd"]
        assert cmd[cmd.index("--head") + 1] == "feature/x"


class TestRunGlabMrList:
    def test_parses_mrs(self):
        from vibe.core.git.forge import _run_glab_mr_list

        with patch(
            "vibe.core.git.forge.subprocess.run", return_value=_completed(GLAB_JSON)
        ):
            mrs = _run_glab_mr_list(
                "/usr/bin/glab", "team/project", "gitlab.com", "main"
            )
        assert len(mrs) == 1
        mr = mrs[0]
        assert mr.provider == "gitlab"
        assert mr.number == 7
        assert mr.title == "Add feature"
        assert mr.url == "https://gitlab.com/team/project/-/merge_requests/7"
        assert mr.source_branch == "feature"
        assert mr.target_branch == "main"
        assert mr.state == "open"
        assert mr.author == "pierre"
        assert mr.repo_host == "gitlab.com"
        assert mr.repo_owner == "team/project"
        assert mr.repo_name == "project"

    def test_self_hosted_sets_gitlab_host_env(self):
        """glab reads the instance host from GITLAB_HOST (same as Phase 1)."""
        from vibe.core.git.forge import _run_glab_mr_list

        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_glab_mr_list(
                "/usr/bin/glab", "team/project", "gitlab.mycompany.com", "main"
            )
        assert captured["env"]["GITLAB_HOST"] == "gitlab.mycompany.com"

    def test_gitlab_host_is_pinned_over_inherited_env(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """GITLAB_HOST is pinned to the checkout's remote host even for
        gitlab.com: a leftover value from the user's shell (e.g. pointing at
        a self-hosted instance) must not steer gitlab.com discovery elsewhere.
        """
        from vibe.core.git.forge import _run_glab_mr_list

        monkeypatch.setenv("GITLAB_HOST", "gitlab.mycompany.com")
        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_glab_mr_list("/usr/bin/glab", "team/project", "gitlab.com", "main")
        assert captured["env"]["GITLAB_HOST"] == "gitlab.com"

    def test_opened_flag_not_passed(self):
        """``--opened`` is deprecated — ``glab mr list`` defaults to opened
        MRs — so it must not appear in the command.
        """
        from vibe.core.git.forge import _run_glab_mr_list

        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_glab_mr_list("/usr/bin/glab", "team/project", "gitlab.com", "main")
        assert "--opened" not in captured["cmd"]

    def test_command_failure_raises_forge_error(self):
        from vibe.core.git.forge import _run_glab_mr_list

        with (
            patch(
                "vibe.core.git.forge.subprocess.run",
                return_value=_completed("", "glab: auth required", returncode=1),
            ),
            pytest.raises(ForgeError, match="glab mr list failed"),
        ):
            _run_glab_mr_list("/usr/bin/glab", "team/project", "gitlab.com", "main")

    def test_timeout_raises_forge_error(self):
        """A slow forge answer is a ForgeError, not an escaped TimeoutExpired."""
        from vibe.core.git.forge import _run_glab_mr_list

        with (
            patch(
                "vibe.core.git.forge.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["glab"], timeout=10),
            ),
            pytest.raises(ForgeError, match="timed out"),
        ):
            _run_glab_mr_list("/usr/bin/glab", "team/project", "gitlab.com", "main")

    def test_oserror_raises_forge_error(self):
        """An unexecutable CLI is a ForgeError, not an escaped OSError."""
        from vibe.core.git.forge import _run_glab_mr_list

        with (
            patch(
                "vibe.core.git.forge.subprocess.run",
                side_effect=OSError("Permission denied"),
            ),
            pytest.raises(ForgeError, match="could not be executed"),
        ):
            _run_glab_mr_list("/usr/bin/glab", "team/project", "gitlab.com", "main")

    def test_output_decoded_as_utf8(self):
        """MR titles are not ASCII: decoding must not depend on the locale
        codepage (Windows ANSI would mangle or reject them).
        """
        from vibe.core.git.forge import _run_glab_mr_list

        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_glab_mr_list("/usr/bin/glab", "team/project", "gitlab.com", "main")
        assert captured["text"] is True
        assert captured["encoding"] == "utf-8"
        assert captured["errors"] == "replace"

    def test_source_branch_is_passed(self):
        """Discovery is branch-scoped: the session's branch reaches
        --source-branch.
        """
        from vibe.core.git.forge import _run_glab_mr_list

        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _completed("[]")

        with patch("vibe.core.git.forge.subprocess.run", side_effect=fake_run):
            _run_glab_mr_list(
                "/usr/bin/glab", "team/project", "gitlab.com", "feature/x"
            )
        cmd = captured["cmd"]
        assert cmd[cmd.index("--source-branch") + 1] == "feature/x"


class TestDiscoverPullRequests:
    def test_github_repo_discovers_via_gh(self, tmp_path: Path):
        with (
            patch(
                "vibe.core.git.forge.WorktreeRepository",
                _fake_repo_cls(
                    "https://github.com/mistralai/vibe.git",
                    branch="vibe/session-branch",
                ),
            ),
            patch("vibe.core.git.forge.find_cli_binary", return_value="/usr/bin/gh"),
            patch(
                "vibe.core.git.forge.subprocess.run", return_value=_completed(GH_JSON)
            ) as run_mock,
        ):
            prs = discover_pull_requests(tmp_path)
        assert len(prs) == 2
        cmd = run_mock.call_args.args[0]
        assert cmd[:2] == ["/usr/bin/gh", "pr"]
        assert "--repo" in cmd
        assert "mistralai/vibe" in cmd
        assert "--limit" in cmd and "100" in cmd
        # Branch-scoped: only PRs whose head is the session's branch.
        assert cmd[cmd.index("--head") + 1] == "vibe/session-branch"

    def test_gitlab_repo_discovers_via_glab(self, tmp_path: Path):
        with (
            patch(
                "vibe.core.git.forge.WorktreeRepository",
                _fake_repo_cls(
                    "https://gitlab.com/team/subgroup/project.git",
                    branch="vibe/session-branch",
                ),
            ),
            patch("vibe.core.git.forge.find_cli_binary", return_value="/usr/bin/glab"),
            patch(
                "vibe.core.git.forge.subprocess.run", return_value=_completed(GLAB_JSON)
            ) as run_mock,
        ):
            mrs = discover_pull_requests(tmp_path)
        assert len(mrs) == 1
        cmd = run_mock.call_args.args[0]
        assert cmd[:2] == ["/usr/bin/glab", "mr"]
        # GitLab subgroups survive: the full project path reaches --repo.
        assert "team/subgroup/project" in cmd
        # Same 100-item cap as gh pr list --limit 100.
        assert "--per-page" in cmd and "100" in cmd
        # Branch-scoped: only MRs whose source branch is the session's branch.
        assert cmd[cmd.index("--source-branch") + 1] == "vibe/session-branch"

    def test_no_remote_raises(self, tmp_path: Path):
        with (
            patch("vibe.core.git.forge.WorktreeRepository", _fake_repo_cls(None)),
            pytest.raises(ForgeError, match="No git remote"),
        ):
            discover_pull_requests(tmp_path)

    def test_unsupported_forge_raises(self, tmp_path: Path):
        with (
            patch(
                "vibe.core.git.forge.WorktreeRepository",
                _fake_repo_cls("https://bitbucket.org/org/repo.git"),
            ),
            pytest.raises(ForgeError, match="Unsupported forge"),
        ):
            discover_pull_requests(tmp_path)

    def test_not_a_git_repo_raises(self, tmp_path: Path):
        with (
            patch("vibe.core.git.forge.WorktreeRepository", _not_a_repo_cls()),
            pytest.raises(ForgeError, match="Not a git repository"),
        ):
            discover_pull_requests(tmp_path)

    def test_missing_gh_cli_raises(self, tmp_path: Path):
        with (
            patch(
                "vibe.core.git.forge.WorktreeRepository",
                _fake_repo_cls("https://github.com/mistralai/vibe.git"),
            ),
            patch("vibe.core.git.forge.find_cli_binary", return_value=None),
            pytest.raises(ForgeError, match="gh CLI not found"),
        ):
            discover_pull_requests(tmp_path)

    def test_missing_glab_cli_raises(self, tmp_path: Path):
        with (
            patch(
                "vibe.core.git.forge.WorktreeRepository",
                _fake_repo_cls("https://gitlab.com/team/project.git"),
            ),
            patch("vibe.core.git.forge.find_cli_binary", return_value=None),
            pytest.raises(ForgeError, match="glab CLI not found"),
        ):
            discover_pull_requests(tmp_path)

    def test_empty_open_prs_returns_empty(self, tmp_path: Path):
        with (
            patch(
                "vibe.core.git.forge.WorktreeRepository",
                _fake_repo_cls("https://github.com/mistralai/vibe.git"),
            ),
            patch("vibe.core.git.forge.find_cli_binary", return_value="/usr/bin/gh"),
            patch("vibe.core.git.forge.subprocess.run", return_value=_completed("[]")),
        ):
            assert discover_pull_requests(tmp_path) == []

    def test_detached_head_returns_empty_without_calling_cli(self, tmp_path: Path):
        """No current branch → no PR/MR can be linked to it → no CLI call."""
        with (
            patch(
                "vibe.core.git.forge.WorktreeRepository",
                _fake_repo_cls("https://github.com/mistralai/vibe.git", branch=None),
            ),
            patch("vibe.core.git.forge.find_cli_binary", return_value="/usr/bin/gh"),
            patch("vibe.core.git.forge.subprocess.run") as run_mock,
        ):
            assert discover_pull_requests(tmp_path) == []
        run_mock.assert_not_called()
