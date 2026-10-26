"""Tests for the session artifact detector (vibe.core.session.artifact_detector)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.session.artifact_detector import (
    cli_binary_source,
    extract_draft_from_command,
    extract_pr_from_output,
    extract_title_from_command,
    find_cli_binary,
    is_pr_creation_command,
    resolve_gitlab_repo_id,
)


class TestIsPrCreationCommand:
    def test_gh_pr_create(self):
        assert is_pr_creation_command("gh pr create --title 'Fix bug'") == "github"
        assert is_pr_creation_command("gh pr create --fill") == "github"

    def test_glab_mr_create(self):
        assert is_pr_creation_command("glab mr create -t 'Fix'") == "gitlab"

    def test_not_pr_creation(self):
        assert is_pr_creation_command("gh pr list") is None
        assert is_pr_creation_command("glab mr list") is None
        assert is_pr_creation_command("git push origin main") is None
        assert is_pr_creation_command("echo hello") is None

    def test_case_insensitive(self):
        assert is_pr_creation_command("GH PR CREATE") == "github"
        assert is_pr_creation_command("Glab Mr Create") == "gitlab"

    def test_search_commands_do_not_match(self):
        """A creation command quoted or echoed by another command is not one."""
        assert is_pr_creation_command("rg 'gh pr create'") is None
        assert is_pr_creation_command('rg "gh pr create"') is None
        assert is_pr_creation_command("rg 'glab mr create'") is None
        assert is_pr_creation_command("echo gh pr create") is None
        assert is_pr_creation_command('echo "a && gh pr create"') is None

    def test_compound_commands_match(self):
        """A creation starting a command position still matches."""
        assert is_pr_creation_command("git push && gh pr create --fill") == "github"
        assert is_pr_creation_command("git push; glab mr create -t 'Fix'") == "gitlab"
        assert is_pr_creation_command("git push\ngh pr create") == "github"
        assert is_pr_creation_command("(gh pr create --fill)") == "github"
        assert is_pr_creation_command("cat x | glab mr create") == "gitlab"
        assert is_pr_creation_command("  gh pr create --fill") == "github"

    def test_compound_close_then_create_matches(self):
        """The real-world compound command: cd && close && create."""
        cmd = (
            'cd "/tmp/worktree" && gh pr close 66122 --comment "Closing." '
            "&& gh pr create --base main --draft "
            '--title "docs: tweak" --body "Body." --head artifact-autoopen-test-1'
        )
        assert is_pr_creation_command(cmd) == "github"

    def test_reopen_matches(self):
        assert is_pr_creation_command("gh pr reopen 123") == "github"
        assert is_pr_creation_command("git push && gh pr reopen 123") == "github"
        assert is_pr_creation_command("glab mr reopen 7") == "gitlab"
        assert is_pr_creation_command("gh pr close 123") is None

    def test_global_flags_between_cli_and_subcommand(self):
        """``gh -R o/r pr create`` / ``glab --hostname h mr create`` match."""
        assert is_pr_creation_command("gh -R org/repo pr create") == "github"
        assert is_pr_creation_command("gh --repo org/repo pr create") == "github"
        assert is_pr_creation_command("gh --repo=org/repo pr create") == "github"
        assert (
            is_pr_creation_command("gh --hostname github.example.com pr create")
            == "github"
        )
        assert is_pr_creation_command("glab -R group/proj mr create") == "gitlab"
        assert (
            is_pr_creation_command("glab --hostname gitlab.example.com mr create")
            == "gitlab"
        )
        assert is_pr_creation_command("gh -R org/repo pr reopen 5") == "github"

    def test_path_qualified_binary(self):
        """``/usr/local/bin/gh pr create`` matches; a bare lookalike does not."""
        assert is_pr_creation_command("/usr/local/bin/gh pr create") == "github"
        assert is_pr_creation_command("/usr/bin/glab mr create") == "gitlab"
        assert is_pr_creation_command("echo /usr/local/bin/gh pr create") is None
        assert is_pr_creation_command("notgh pr create") is None


class TestExtractPrFromOutput:
    def test_github_url_in_stdout(self):
        stdout = "https://github.com/mistralai/vibe/pull/123"
        result = extract_pr_from_output(stdout, "")
        assert result is not None
        assert result.provider == "github"
        assert result.host == "github.com"
        assert result.owner == "mistralai"
        assert result.repo == "vibe"
        assert result.number == 123

    def test_github_url_in_stderr(self):
        stderr = (
            "remote: Create a pull request for 'fix' on GitHub by visiting:\n"
            "remote:      https://github.com/org/repo/pull/42\n"
        )
        result = extract_pr_from_output("", stderr)
        assert result is not None
        assert result.provider == "github"
        assert result.number == 42

    def test_github_enterprise(self):
        """GitHub Enterprise — host captured from the URL."""
        stdout = "https://github.mycompany.com/mistralai/vibe/pull/456"
        result = extract_pr_from_output(stdout, "")
        assert result is not None
        assert result.provider == "github"
        assert result.host == "github.mycompany.com"
        assert result.owner == "mistralai"
        assert result.repo == "vibe"
        assert result.number == 456

    def test_gitlab_mr_url(self):
        stdout = "https://gitlab.com/group/project/-/merge_requests/7"
        result = extract_pr_from_output(stdout, "")
        assert result is not None
        assert result.provider == "gitlab"
        assert result.host == "gitlab.com"
        assert result.project_path == "group/project"
        assert result.iid == 7
        assert result.repo_id is None

    def test_gitlab_self_hosted(self):
        stdout = "https://gitlab.mycompany.com/team/subgroup/proj/-/merge_requests/99"
        result = extract_pr_from_output(stdout, "")
        assert result is not None
        assert result.provider == "gitlab"
        assert result.host == "gitlab.mycompany.com"
        assert result.project_path == "team/subgroup/proj"
        assert result.iid == 99

    def test_no_url(self):
        assert extract_pr_from_output("Creating pull request...", "") is None
        assert extract_pr_from_output("", "") is None

    def test_github_reopen_full_output(self):
        """gh pr reopen prints owner/repo#N without a URL; the caller fills the host."""
        detected = extract_pr_from_output(
            "\u2713 Reopened pull request mistralai/dashboard#67176 (Fix bug)\n", ""
        )
        assert detected is not None
        assert detected.provider == "github"
        assert detected.owner == "mistralai"
        assert detected.repo == "dashboard"
        assert detected.number == 67176
        assert detected.url == ""
        assert detected.host == ""

    def test_github_reopen_number_only_output(self):
        detected = extract_pr_from_output("reopened #67176\n", "")
        assert detected is not None
        assert detected.provider == "github"
        assert detected.number == 67176
        assert detected.owner is None
        assert detected.url == ""

    def test_github_reopen_does_not_match_close_output(self):
        """A close confirmation alone is not a reopen detection."""
        assert extract_pr_from_output("closed #65834\n", "") is None

    def test_gitlab_reopen_iid_output(self):
        detected = extract_pr_from_output("\u2713 Reopened merge request !7\n", "")
        assert detected is not None
        assert detected.provider == "gitlab"
        assert detected.iid == 7
        assert detected.url == ""

    def test_github_preferred_over_gitlab(self):
        """If both URLs appear, GitHub is checked first."""
        text = (
            "https://github.com/org/repo/pull/1\n"
            "https://gitlab.com/org/repo/-/merge_requests/2"
        )
        result = extract_pr_from_output(text, "")
        assert result is not None
        assert result.provider == "github"

    def test_provider_preferred_over_other_provider_url(self):
        """The provider detected from the command wins over a URL of the other
        provider mentioned in the output (e.g. an MR description linking a
        GitHub PR).
        """
        text = (
            "https://github.com/org/repo/pull/1\n"
            "https://gitlab.com/org/repo/-/merge_requests/2"
        )
        result = extract_pr_from_output(text, "", provider="gitlab")
        assert result is not None
        assert result.provider == "gitlab"
        assert result.iid == 2

        result = extract_pr_from_output(text, "", provider="github")
        assert result is not None
        assert result.provider == "github"
        assert result.number == 1

    def test_provider_falls_back_to_other_provider_url(self):
        """A provider whose own URL is absent still records the other one."""
        text = "https://github.com/org/repo/pull/1"
        result = extract_pr_from_output(text, "", provider="gitlab")
        assert result is not None
        assert result.provider == "github"
        assert result.number == 1

    def test_provider_preferred_for_reopen_output(self):
        """A reopen confirmation is matched for the detected provider."""
        detected = extract_pr_from_output(
            "https://github.com/org/repo/pull/1\n\u2713 Reopened merge request !7\n",
            "",
            provider="gitlab",
        )
        assert detected is not None
        assert detected.provider == "gitlab"
        assert detected.iid == 7


class TestExtractTitleFromCommand:
    def test_long_flag_quoted(self):
        assert (
            extract_title_from_command("gh pr create --title 'Fix login'")
            == "Fix login"
        )

    def test_long_flag_unquoted(self):
        assert extract_title_from_command("gh pr create --title Fix") == "Fix"

    def test_long_flag_equals(self):
        assert extract_title_from_command("gh pr create --title=Fix") == "Fix"

    def test_short_flag(self):
        assert extract_title_from_command("glab mr create -t 'My MR'") == "My MR"

    def test_no_title(self):
        assert extract_title_from_command("gh pr create --fill") is None

    def test_unquoted_title_stops_at_next_flag(self):
        """An unquoted title is one shell token; later flags are not swallowed."""
        assert (
            extract_title_from_command("gh pr create --title Fix login --body x")
            == "Fix"
        )
        assert extract_title_from_command("gh pr create --title Fix --draft") == "Fix"

    def test_quoted_title_with_equals(self):
        assert (
            extract_title_from_command('gh pr create --title="Fix login"')
            == "Fix login"
        )

    def test_short_flag_unquoted(self):
        assert extract_title_from_command("glab mr create -t Fix --draft") == "Fix"

    def test_short_flag_must_start_a_token(self):
        """``-t`` inside a longer flag is not the title flag."""
        assert extract_title_from_command("gh pr create --not-t x") is None

    def test_quoted_args_do_not_poison_title(self):
        """A ``--title`` inside a quoted body is text; the real flag still wins."""
        assert (
            extract_title_from_command(
                "gh pr create --body 'set --title fake' --title Real"
            )
            == "Real"
        )
        assert extract_title_from_command("gh pr create --body 'use -t here'") is None
        assert (
            extract_title_from_command("gh pr create --title 'Add --draft support'")
            == "Add --draft support"
        )


class TestExtractDraftFromCommand:
    def test_gh_draft_long(self):
        assert extract_draft_from_command("gh pr create --draft", "github") is True

    def test_gh_draft_short(self):
        assert extract_draft_from_command("gh pr create -d", "github") is True

    def test_glab_draft_long(self):
        assert extract_draft_from_command("glab mr create --draft", "gitlab") is True

    def test_glab_d_is_description_not_draft(self):
        """For glab, -d means --description, NOT --draft."""
        assert (
            extract_draft_from_command('glab mr create -d "my description"', "gitlab")
            is False
        )

    def test_not_draft(self):
        assert (
            extract_draft_from_command("gh pr create --title 'Fix'", "github") is False
        )
        assert extract_draft_from_command("glab mr create -t 'Fix'", "gitlab") is False

    def test_quoted_args_do_not_poison_draft(self):
        """A ``--draft``/``-d`` inside a quoted title or body is text."""
        assert (
            extract_draft_from_command(
                "gh pr create --title 'Add --draft support'", "github"
            )
            is False
        )
        assert (
            extract_draft_from_command(
                "gh pr create --body 'fix -d the thing'", "github"
            )
            is False
        )
        # The real flag is still detected alongside quoted text.
        assert (
            extract_draft_from_command(
                "gh pr create --draft --title 'Add --draft support'", "github"
            )
            is True
        )


class TestFindCliBinary:
    def test_system_binary_found(self, monkeypatch):
        """A — system binary found via shutil.which."""
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/gh")
        assert find_cli_binary("gh") == "/usr/local/bin/gh"

    def test_bundled_binary_fallback(self, monkeypatch, tmp_path):
        """C — fallback to bundled binary."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        bundled = tmp_path / "vibe" / "bin" / "gh"
        bundled.parent.mkdir(parents=True)
        bundled.touch()
        monkeypatch.setattr("vibe.VIBE_ROOT", tmp_path / "vibe")
        # Re-import to pick up the patched VIBE_ROOT
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.VIBE_ROOT", tmp_path / "vibe"
        )
        assert find_cli_binary("gh") == str(bundled)

    def test_not_found(self, monkeypatch):
        """Neither system nor bundled → None."""
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.VIBE_ROOT", Path("/nonexistent")
        )
        assert find_cli_binary("gh") is None

    def test_prefers_system_over_bundled(self, monkeypatch, tmp_path):
        """A is prioritized over C."""
        bundled = tmp_path / "vibe" / "bin" / "gh"
        bundled.parent.mkdir(parents=True)
        bundled.touch()
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.VIBE_ROOT", tmp_path / "vibe"
        )
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/gh")
        assert find_cli_binary("gh") == "/usr/local/bin/gh"


class TestCliBinarySource:
    def test_system(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gh")
        assert cli_binary_source("gh") == "system"

    def test_bundled(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: None)
        bundled = tmp_path / "vibe" / "bin" / "glab"
        bundled.parent.mkdir(parents=True)
        bundled.touch()
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.VIBE_ROOT", tmp_path / "vibe"
        )
        assert cli_binary_source("glab") == "bundled"

    def test_none(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.VIBE_ROOT", Path("/nonexistent")
        )
        assert cli_binary_source("gh") == "none"


class TestResolveGitlabRepoId:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        """glab api returns a project with an id."""
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.find_cli_binary",
            lambda name: "/usr/bin/glab",
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'{"id": 12345}', b""))

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            result = await resolve_gitlab_repo_id("gitlab.com", "org/repo")
            assert result == 12345
            # Verify the command
            call_args = mock_exec.call_args
            assert call_args.args[0] == "/usr/bin/glab"
            assert "projects/org%2Frepo" in call_args.args[2]

    @pytest.mark.asyncio
    async def test_glab_not_installed(self, monkeypatch):
        """glab absent → None."""
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.find_cli_binary", lambda name: None
        )
        result = await resolve_gitlab_repo_id("gitlab.com", "org/repo")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure(self, monkeypatch):
        """glab api returns non-zero → None."""
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.find_cli_binary",
            lambda name: "/usr/bin/glab",
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await resolve_gitlab_repo_id("gitlab.com", "org/repo")
            assert result is None

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self, monkeypatch):
        """On timeout, the process is killed."""
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.find_cli_binary",
            lambda name: "/usr/bin/glab",
        )

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await resolve_gitlab_repo_id("gitlab.com", "org/repo", timeout=0.1)
            assert result is None
            mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancellation_kills_process(self, monkeypatch):
        """A cancelled drain kills the child instead of leaking it."""
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.find_cli_binary",
            lambda name: "/usr/bin/glab",
        )

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(asyncio.CancelledError):
                await resolve_gitlab_repo_id("gitlab.com", "org/repo")
            mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_json(self, monkeypatch):
        """Invalid JSON response → None."""
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.find_cli_binary",
            lambda name: "/usr/bin/glab",
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"not json", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await resolve_gitlab_repo_id("gitlab.com", "org/repo")
            assert result is None

    @pytest.mark.asyncio
    async def test_self_hosted_passes_gitlab_host(self, monkeypatch):
        """Non-gitlab.com host is passed via GITLAB_HOST env var."""
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.find_cli_binary",
            lambda name: "/usr/bin/glab",
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'{"id": 42}', b""))

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            result = await resolve_gitlab_repo_id("gitlab.mycompany.com", "org/repo")
            assert result == 42
            env = mock_exec.call_args.kwargs["env"]
            assert env["GITLAB_HOST"] == "gitlab.mycompany.com"

    @pytest.mark.asyncio
    async def test_stdin_is_devnull(self, monkeypatch):
        """The glab child never inherits the app-server's stdin (stdio RPC)."""
        monkeypatch.setattr(
            "vibe.core.session.artifact_detector.find_cli_binary",
            lambda name: "/usr/bin/glab",
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'{"id": 1}', b""))

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            await resolve_gitlab_repo_id("gitlab.com", "org/repo")
            assert mock_exec.call_args.kwargs["stdin"] is asyncio.subprocess.DEVNULL
