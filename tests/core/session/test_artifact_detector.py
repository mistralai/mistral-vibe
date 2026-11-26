"""Tests for the session artifact detector (vibe.core.session.artifact_detector)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.session.artifact_detector import (
    cli_binary_source,
    extract_draft_from_command,
    extract_prs_from_output,
    find_cli_binary,
    is_pr_creation_command,
    pr_creation_limit,
    pr_creation_output_first,
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

    def test_creation_behind_shell_reserved_words_matches(self):
        assert (
            is_pr_creation_command("for b in x y; do gh pr create --head $b; done")
            == "github"
        )
        assert is_pr_creation_command("if true; then gh pr create; fi") == "github"
        assert is_pr_creation_command("if x; then :; else gh pr create; fi") == "github"
        assert is_pr_creation_command("while read b; do glab mr create; done") == (
            "gitlab"
        )
        assert is_pr_creation_command("{ gh pr create; }") == "github"
        assert is_pr_creation_command("echo do gh pr create") is None

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

    @pytest.mark.parametrize(
        "command",
        [
            "perl -e 'alarm 90; exec @ARGV' gh pr create --draft --title 'T' 2>&1 | tail -3",
            "cd ~/dev/other && perl -e 'alarm 90; exec @ARGV' gh pr create --fill",
            "timeout 90 gh pr create --fill",
            "env GH_HOST=github.com gh pr create --fill",
            "GH_TOKEN=x gh pr create --fill",
            "nohup gh pr create --fill",
            "command gh pr create --fill",
            "exec gh pr create --fill",
            "sudo -u me /usr/local/bin/gh pr create --fill",
            "timeout 60 glab mr create -t 'Fix'",
            "timeout 60 gh pr reopen 5",
        ],
    )
    def test_wrapped_creation_matches(self, command: str):
        """A creation run through a wrapper (``perl -e 'alarm'``, ``timeout``,
        ``env``...) is still one.
        """
        assert is_pr_creation_command(command) is not None

    def test_creation_after_a_heredoc_with_an_apostrophe_matches(self):
        command = (
            "cat > /tmp/pr.md <<'EOF'\n"
            "Leave a running operation's receipt to it.\n"
            "EOF\n"
            "cd /tmp/wt && perl -e 'alarm 90; exec @ARGV' gh pr create "
            '--title "fix: keep an operation\'s receipt" --body-file /tmp/pr.md'
        )
        assert is_pr_creation_command(command) == "github"
        assert (
            is_pr_creation_command(
                "gh pr create --body-file - <<'EOF'\nIt's done.\nEOF"
            )
            == "github"
        )

    @pytest.mark.parametrize(
        "command",
        [
            "cat > pr.md <<'PR-BODY'\nit's\nPR-BODY\ngh pr create --title 'x'",
            "cat > pr.md <<'END.md'\nit's\nEND.md\ngh pr create --title 'x'",
            "cat > pr.md <<\\EOF\nit's\nEOF\ngh pr create --title 'x'",
            "gh pr create --body \"$(cat <<'EOF'\nIt's done.\nEOF\n)\"",
            "rg 'a <<EOF'\ngh pr create --fill\nEOF",
            "echo $((1<<2))\ngh pr create --fill\n2",
            "cat > pr.md <<'EOF'\nIt's done.\nEOF\nls **/*(.) ; gh pr create --title 'x'",
            "cat <<EOF\n$(gh pr create --fill)\nEOF",
            "cat <<EOF\n${x:-$(gh pr create --fill)}\nEOF",
            pytest.param(
                "cat > pr.md <<EOF\nRun `gh pr create` later\nEOF",
                marks=pytest.mark.xfail(
                    strict=True,
                    reason="tree-sitter-bash does not parse backticks in a"
                    " heredoc body, so the body is blanked whole",
                ),
            ),
        ],
    )
    def test_creation_around_any_heredoc_matches(self, command: str):
        assert is_pr_creation_command(command) == "github"

    @pytest.mark.parametrize(
        "command",
        [
            "echo gh pr create",
            "cat > notes.md <<EOFX\nEOF\nthen run gh pr create\nEOFX",
            "cat > notes.md <<EOF\nthen run gh pr create\nEOF",
            "cat > notes.md <<-'EOF'\n\tthen run gh pr create\n\tEOF\ngit status",
            "printf '%s' gh pr create",
            "git commit -m 'use gh pr create'",
            "foo-gh pr create",
            "my_gh pr create",
            "gh.sh pr create",
            "ssh pr create",
            "echo done # gh pr create",
        ],
    )
    def test_text_mentioning_a_creation_does_not_match(self, command: str):
        assert is_pr_creation_command(command) is None

    @pytest.mark.parametrize(
        "command",
        [
            "cat > notes.md <<EOF\n  EOF\nthen run gh pr create\nEOF",
            "cat > notes.md <<-EOF\n  EOF\nthen run gh pr create\nEOF",
            "cat > notes.md <<EOF\nEOF \nthen run gh pr create\nEOF",
        ],
    )
    @pytest.mark.xfail(
        strict=True,
        reason="tree-sitter-bash ends a heredoc on an indented or padded"
        " delimiter line; bash does not",
    )
    def test_heredoc_ended_only_by_the_exact_delimiter_line(self, command: str):
        assert is_pr_creation_command(command) is None

    @pytest.mark.parametrize(
        "command",
        [
            "cat <<A <<B\na\nA\nit's\nB\ngh pr create --title 'x'",
            "cat > pr.md <<EOF\nIt's ${x\nEOF\ngh pr create --title 'x'",
        ],
    )
    @pytest.mark.xfail(
        strict=True,
        reason="a heredoc body inside or after an ERROR node is kept as code,"
        " as before parsing",
    )
    def test_creation_after_a_heredoc_the_grammar_cannot_read(self, command: str):
        assert is_pr_creation_command(command) == "github"


class TestPrCreationLimit:
    def test_counts_creation_and_reopen_segments(self):
        assert pr_creation_limit("gh pr create --fill") == 1
        assert pr_creation_limit("gh pr list && gh pr create --fill") == 1
        assert pr_creation_limit("gh pr create -H a; gh pr reopen 5") == 2
        assert pr_creation_limit("glab mr create && glab mr create") == 2
        assert pr_creation_limit("gh pr list") == 0

    def test_a_creation_in_a_loop_is_unbounded(self):
        assert (
            pr_creation_limit("for b in x y; do gh pr create --head $b; done") is None
        )
        assert (
            pr_creation_limit("while read b; do git push; glab mr create; done") is None
        )
        assert pr_creation_limit("for b in x\ndo\n  gh pr create\ndone") is None
        assert pr_creation_limit("git branch | xargs -I{} gh pr create -H {}") is None

    def test_a_creation_after_a_loop_is_counted(self):
        assert (
            pr_creation_limit("for b in x y; do git push $b; done; gh pr create") == 1
        )


class TestExtractPrFromOutput:
    def test_github_url_in_stdout(self):
        stdout = "https://github.com/mistralai/vibe/pull/123"
        [result] = extract_prs_from_output(stdout, "")
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
        [result] = extract_prs_from_output("", stderr)
        assert result.provider == "github"
        assert result.number == 42

    def test_github_enterprise(self):
        """GitHub Enterprise — host captured from the URL."""
        stdout = "https://github.mycompany.com/mistralai/vibe/pull/456"
        [result] = extract_prs_from_output(stdout, "")
        assert result.provider == "github"
        assert result.host == "github.mycompany.com"
        assert result.owner == "mistralai"
        assert result.repo == "vibe"
        assert result.number == 456

    def test_gitlab_mr_url(self):
        stdout = "https://gitlab.com/group/project/-/merge_requests/7"
        [result] = extract_prs_from_output(stdout, "")
        assert result.provider == "gitlab"
        assert result.host == "gitlab.com"
        assert result.project_path == "group/project"
        assert result.iid == 7
        assert result.repo_id is None

    def test_gitlab_self_hosted(self):
        stdout = "https://gitlab.mycompany.com/team/subgroup/proj/-/merge_requests/99"
        [result] = extract_prs_from_output(stdout, "")
        assert result.provider == "gitlab"
        assert result.host == "gitlab.mycompany.com"
        assert result.project_path == "team/subgroup/proj"
        assert result.iid == 99

    def test_every_distinct_url_is_returned(self):
        stdout = (
            "https://github.com/org/repo/pull/1\n"
            "https://github.com/org/repo/pull/2\n"
            "https://github.com/org/repo/pull/1\n"
        )
        assert [pr.number for pr in extract_prs_from_output(stdout, "")] == [1, 2]

    def test_listed_urls_before_a_creation_are_not_returned(self):
        command = "gh pr list --json url --jq '.[].url' && gh pr create --fill"
        stdout = (
            "https://github.com/org/repo/pull/1\n"
            "https://github.com/org/repo/pull/2\n"
            "https://github.com/org/repo/pull/3\n"
        )
        [result] = extract_prs_from_output(stdout, "", limit=pr_creation_limit(command))
        assert result.number == 3

    def test_two_creations_return_the_last_two_urls(self):
        command = "gh pr list && gh pr create --head a && gh pr create --head b"
        stdout = (
            "https://github.com/org/repo/pull/1\n"
            "https://github.com/org/repo/pull/2\n"
            "https://github.com/org/repo/pull/3\n"
        )
        detected = extract_prs_from_output(stdout, "", limit=pr_creation_limit(command))
        assert [pr.number for pr in detected] == [2, 3]

    def test_a_url_printed_after_a_creation_is_not_returned(self):
        command = "gh pr create --fill && gh pr view 999 --json url --jq .url"
        stdout = (
            "https://github.com/org/repo/pull/5\nhttps://github.com/org/repo/pull/999\n"
        )
        [result] = extract_prs_from_output(
            stdout,
            "",
            limit=pr_creation_limit(command),
            from_start=pr_creation_output_first(command),
        )
        assert result.number == 5

    def test_creations_print_first_only_when_gh_runs_only_after_them(self):
        assert pr_creation_output_first("gh pr create && gh pr view 9") is True
        assert (
            pr_creation_output_first("git push && gh pr create && gh pr view") is True
        )
        assert pr_creation_output_first("gh pr list && gh pr create") is False
        assert pr_creation_output_first("gh pr list; gh pr create; gh pr view") is False
        assert pr_creation_output_first("gh pr create --fill") is False

    def test_a_gh_mention_that_prints_no_pr_url_is_not_output(self):
        assert pr_creation_output_first("which gh && gh pr create && gh pr view")
        assert pr_creation_output_first("cd ~/gh && gh pr create && gh pr view")
        assert pr_creation_output_first("echo gh pr list; gh pr create; gh pr view")

    def test_a_creation_in_a_loop_returns_every_url(self):
        command = "gh pr list; for b in a b; do git push; gh pr create --head $b; done"
        stdout = (
            "https://github.com/org/repo/pull/1\n"
            "https://github.com/org/repo/pull/2\n"
            "https://github.com/org/repo/pull/3\n"
        )
        detected = extract_prs_from_output(stdout, "", limit=pr_creation_limit(command))
        assert [pr.number for pr in detected] == [1, 2, 3]

    def test_the_last_printed_url_wins_under_a_limit(self):
        stdout = (
            "https://github.com/org/repo/pull/1\n"
            "https://github.com/org/repo/pull/2\n"
            "https://github.com/org/repo/pull/1\n"
        )
        [result] = extract_prs_from_output(stdout, "", limit=1)
        assert result.number == 1

    def test_no_url(self):
        assert extract_prs_from_output("Creating pull request...", "") == []
        assert extract_prs_from_output("", "") == []

    def test_github_reopen_full_output(self):
        """gh pr reopen prints owner/repo#N without a URL; the caller fills the host."""
        [detected] = extract_prs_from_output(
            "\u2713 Reopened pull request mistralai/dashboard#67176 (Fix bug)\n", ""
        )
        assert detected.provider == "github"
        assert detected.owner == "mistralai"
        assert detected.repo == "dashboard"
        assert detected.number == 67176
        assert detected.url == ""
        assert detected.host == ""

    def test_github_reopen_number_only_output(self):
        [detected] = extract_prs_from_output("reopened #67176\n", "")
        assert detected.provider == "github"
        assert detected.number == 67176
        assert detected.owner is None
        assert detected.url == ""

    def test_every_reopened_pr_is_returned(self):
        stdout = (
            "\u2713 Reopened pull request org/repo#1 (A)\n"
            "\u2713 Reopened pull request org/repo#2 (B)\n"
        )
        assert [pr.number for pr in extract_prs_from_output(stdout, "")] == [1, 2]
        detected = extract_prs_from_output("reopened #1\nreopened #2\n", "")
        assert [pr.number for pr in detected] == [1, 2]
        detected = extract_prs_from_output(
            "Reopened merge request !3\nReopened merge request !4\n", ""
        )
        assert [pr.iid for pr in detected] == [3, 4]

    def test_github_reopen_does_not_match_close_output(self):
        """A close confirmation alone is not a reopen detection."""
        assert extract_prs_from_output("closed #65834\n", "") == []

    def test_gitlab_reopen_iid_output(self):
        [detected] = extract_prs_from_output("\u2713 Reopened merge request !7\n", "")
        assert detected.provider == "gitlab"
        assert detected.iid == 7
        assert detected.url == ""

    def test_github_preferred_over_gitlab(self):
        """If both URLs appear, GitHub is checked first."""
        text = (
            "https://github.com/org/repo/pull/1\n"
            "https://gitlab.com/org/repo/-/merge_requests/2"
        )
        [result] = extract_prs_from_output(text, "")
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
        [result] = extract_prs_from_output(text, "", provider="gitlab")
        assert result.provider == "gitlab"
        assert result.iid == 2

        [result] = extract_prs_from_output(text, "", provider="github")
        assert result.provider == "github"
        assert result.number == 1

    def test_provider_falls_back_to_other_provider_url(self):
        """A provider whose own URL is absent still records the other one."""
        text = "https://github.com/org/repo/pull/1"
        [result] = extract_prs_from_output(text, "", provider="gitlab")
        assert result.provider == "github"
        assert result.number == 1

    def test_provider_preferred_for_reopen_output(self):
        """A reopen confirmation is matched for the detected provider."""
        [detected] = extract_prs_from_output(
            "https://github.com/org/repo/pull/1\n\u2713 Reopened merge request !7\n",
            "",
            provider="gitlab",
        )
        assert detected.provider == "gitlab"
        assert detected.iid == 7


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

    def test_several_creations_are_drafts_only_when_all_are(self):
        assert (
            extract_draft_from_command(
                "gh pr create -H a --draft && gh pr create -H b", "github"
            )
            is False
        )
        assert (
            extract_draft_from_command(
                "gh pr create -H a --draft && gh pr create -H b -d", "github"
            )
            is True
        )

    def test_heredoc_body_does_not_change_the_draft_flag(self):
        body = "cat > pr.md <<'EOF'\nIt's run with --draft first.\nEOF\n"
        assert (
            extract_draft_from_command(
                body + "perl -e 'alarm 90; exec @ARGV' gh pr create --draft", "github"
            )
            is True
        )
        assert (
            extract_draft_from_command(
                body + "gh pr create --body-file pr.md", "github"
            )
            is False
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
