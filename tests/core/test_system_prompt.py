from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import MappingProxyType
from unittest.mock import MagicMock

import pytest

from vibe.core.config import ProjectContextConfig
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import SkillInfo
from vibe.core.system_prompt import (
    ProjectContextProvider,
    _get_available_skills_section,
    get_agents_md_section,
)


def test_project_local_git_is_not_executed_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    marker = tmp_path / "executed"
    git_name = "git.exe" if os.name == "nt" else "git"
    fake_git = project / git_name
    fake_git.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    fake_git.chmod(0o755)
    monkeypatch.delenv("GIT_PYTHON_GIT_EXECUTABLE", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("PATH", str(project))

    status = ProjectContextProvider(
        ProjectContextConfig(), root_path=project
    ).get_git_status()

    assert not marker.exists()
    assert "No trusted Git executable" in status


def test_fetch_git_context_does_not_inspect_worktree_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ProjectContextProvider(ProjectContextConfig(), root_path=tmp_path)
    calls: list[list[str]] = []

    def fake_run_git(
        args: list[str], _timeout: float
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        output = {
            ("branch", "--show-current"): "feature\n",
            ("branch", "-r"): "  origin/master\n",
            ("log", "--oneline", "-5", "--decorate"): "abc123 message\n",
        }[tuple(args)]
        return subprocess.CompletedProcess(args, 0, stdout=output)

    monkeypatch.setattr(provider, "_run_git", fake_run_git)

    context = provider._fetch_git_status()

    assert not any(args and args[0] == "status" for args in calls)
    assert "Current branch: feature" in context
    assert "Main branch (you will usually use this for PRs): master" in context
    assert "abc123 message" in context
    assert "Status:" not in context


@pytest.mark.skipif(os.name == "nt", reason="fake git shell script is POSIX-only")
def test_run_git_survives_non_utf8_output(tmp_path: Path, monkeypatch) -> None:
    # Fake git that prints bytes 0x80 0x81 (invalid UTF-8, and invalid gbk here)
    project = tmp_path / "project"
    project.mkdir()
    fake_git = tmp_path / "trusted-bin" / "git"
    fake_git.parent.mkdir()
    fake_git.write_text('#!/bin/sh\nprintf "commit \\200\\201 msg\\n"\n')
    fake_git.chmod(0o755)
    # Put the fake first on PATH so _run_git executes it instead of real git
    monkeypatch.setenv("PATH", f"{fake_git.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_PYTHON_GIT_EXECUTABLE", str(fake_git))

    provider = ProjectContextProvider(ProjectContextConfig(), root_path=project)

    # Without encoding="utf-8", errors="replace" this raises UnicodeDecodeError
    result = provider._run_git(["log"], timeout=5.0)

    # The bad bytes are replaced with U+FFFD instead of crashing
    assert "\ufffd" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="fake git shell script is POSIX-only")
def test_run_git_disables_fsmonitor_hook(tmp_path: Path, monkeypatch) -> None:
    # Fake git that records the argv it was invoked with, one arg per line.
    project = tmp_path / "project"
    project.mkdir()
    fake_git = tmp_path / "trusted-bin" / "git"
    fake_git.parent.mkdir()
    fake_git.write_text('#!/bin/sh\nfor a in "$@"; do echo "$a"; done\n')
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_git.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_PYTHON_GIT_EXECUTABLE", str(fake_git))

    provider = ProjectContextProvider(ProjectContextConfig(), root_path=project)
    result = provider._run_git(["status", "--porcelain"], timeout=5.0)

    argv = result.stdout.splitlines()
    # -c core.fsmonitor= must come before any positional git subcommand so it
    # actually overrides the repo's own config, and must not be overridable by
    # anything the invoked repo could inject via its own .git/config.
    assert "-c" in argv
    assert argv[argv.index("-c") + 1] == "core.fsmonitor="
    assert argv.index("-c") < argv.index("status")


@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX shell payload")
def test_fetch_git_status_does_not_execute_malicious_fsmonitor_hook(
    tmp_path: Path,
) -> None:
    # Regression test for the RCE reported in #942: a repo's own .git/config
    # can declare core.fsmonitor as an arbitrary command, which git runs on
    # `status` (and other worktree-refreshing commands) with the invoking
    # user's full privileges -- and this runs on every session start, before
    # any trust dialog is shown to the user.
    repo = tmp_path / "malicious_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# README\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    payload_marker = tmp_path / "PWNED"
    subprocess.run(
        ["git", "config", "core.fsmonitor", f"touch {payload_marker}"],
        cwd=repo,
        check=True,
    )

    provider = ProjectContextProvider(ProjectContextConfig(), root_path=repo)
    status = provider.get_git_status()

    assert not payload_marker.exists()
    # The fix must not break normal status reporting.
    assert "Current branch:" in status
    assert "Git operations timed out" not in status
    assert "Not a git repository" not in status


def test_get_agents_md_section_returns_none_without_docs() -> None:
    assert get_agents_md_section("", []) is None
    assert get_agents_md_section("   \n   ", []) is None


def test_get_agents_md_section_renders_user_and_project_docs() -> None:
    section = get_agents_md_section("# User doc", [(Path("/repo"), "# Project doc")])
    assert section is not None
    assert section.startswith("Codebase and user instructions are shown below.")
    assert "## User instructions" in section
    assert "# User doc" in section
    assert "## Project instructions (checked into the codebase)" in section
    assert "Contents of /repo/AGENTS.md" in section
    assert "# Project doc" in section
    # Legacy ordering: user doc first, project docs after, wrapper text last.
    assert (
        section.index("## User instructions")
        < section.index("## Project instructions (checked into the codebase)")
        < section.index("IMPORTANT: this context may or may not be relevant")
    )


def test_get_agents_md_section_renders_project_docs_only() -> None:
    section = get_agents_md_section("", [(Path("/repo"), "# Project doc")])
    assert section is not None
    assert "## User instructions" not in section
    assert "## Project instructions (checked into the codebase)" in section
    assert "Contents of /repo/AGENTS.md" in section


def test_available_skills_excludes_skills_not_invocable_by_model() -> None:
    manager = MagicMock(spec=SkillManager)
    manager.available_skills = MappingProxyType({
        "automatic": SkillInfo(
            name="automatic", description="Visible description", prompt="Visible body"
        ),
        "explicit-only": SkillInfo(
            name="explicit-only",
            description="Hidden description",
            prompt="Hidden body",
            model_invocable=False,
        ),
    })

    section = _get_available_skills_section(manager)

    assert "automatic" in section
    assert "Visible description" in section
    assert "explicit-only" not in section
    assert "Hidden description" not in section
    assert "When a user message is exactly `/skill-name`" in section


def test_available_skills_keeps_slash_instructions_for_explicit_only_skills() -> None:
    manager = MagicMock(spec=SkillManager)
    manager.available_skills = MappingProxyType({
        "explicit-only": SkillInfo(
            name="explicit-only",
            description="Hidden description",
            prompt="Hidden body",
            model_invocable=False,
        )
    })

    section = _get_available_skills_section(manager)

    assert section.startswith("# Available Skills")
    assert "When a user message is exactly `/skill-name`" in section
    assert "explicit-only" not in section
    assert "Hidden description" not in section
    assert "<available_skills>" not in section
