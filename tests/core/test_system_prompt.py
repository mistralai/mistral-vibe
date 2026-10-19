from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from vibe.core import system_prompt
from vibe.core.config import ProjectContextConfig
from vibe.core.system_prompt import ProjectContextProvider, get_agents_md_section
from vibe.utils.platform import WindowsShell, WindowsShellKind


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


@pytest.mark.parametrize(
    ("shell_kind", "expects_git_bash_path"),
    [(WindowsShellKind.BASH, True), (WindowsShellKind.CMD, False)],
)
def test_full_context_adds_git_bash_path_when_shell_is_git_bash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shell_kind: WindowsShellKind,
    expects_git_bash_path: bool,
) -> None:
    monkeypatch.setattr(system_prompt, "is_windows", lambda: True)
    monkeypatch.setattr(
        system_prompt,
        "resolve_windows_shell",
        lambda: WindowsShell(shell_kind, "C:\\Program Files\\Git\\bin\\bash.exe"),
    )
    provider = ProjectContextProvider(ProjectContextConfig(), root_path=tmp_path)
    provider.root_path = Path("C:\\Users\\acmedev\\repo")
    monkeypatch.setattr(provider, "get_git_status", lambda: "")

    context = provider.get_full_context()

    assert "Absolute path: C:\\Users\\acmedev\\repo" in context
    assert (
        "Git Bash path (use this form in shell commands): /c/Users/acmedev/repo"
        in context
    ) is expects_git_bash_path


def test_full_context_omits_git_bash_path_for_unc_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(system_prompt, "is_windows", lambda: True)
    monkeypatch.setattr(
        system_prompt,
        "resolve_windows_shell",
        lambda: WindowsShell(WindowsShellKind.BASH, "bash.exe"),
    )
    provider = ProjectContextProvider(ProjectContextConfig(), root_path=tmp_path)
    provider.root_path = Path("\\\\server\\share\\repo")
    monkeypatch.setattr(provider, "get_git_status", lambda: "")

    assert "Git Bash path" not in provider.get_full_context()


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
            (
                "for-each-ref",
                "--count=1",
                "--format=%(refname:short)",
                "refs/remotes/origin/master",
            ): "origin/master\n",
            ("log", "--oneline", "--no-decorate", "-5"): "abc123 message\n",
        }[tuple(args)]
        return subprocess.CompletedProcess(args, 0, stdout=output)

    monkeypatch.setattr(provider, "_run_git", fake_run_git)

    context = provider._fetch_git_status()

    assert not any(args and args[0] == "status" for args in calls)
    assert "Current branch: feature" in context
    assert "Main branch (you will usually use this for PRs): master" in context
    assert "abc123 message" in context
    assert "Status:" not in context


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_git_status_keeps_full_commit_messages_without_decorations(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "feature")
    _git(repo, "config", "log.decorate", "short")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat(scope): add thing (#12)")
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")

    status = ProjectContextProvider(
        ProjectContextConfig(), root_path=repo
    ).get_git_status()

    assert "Current branch: feature" in status
    assert "Main branch (you will usually use this for PRs): master" in status
    assert "feat(scope): add thing (#12)" in status
    assert "HEAD ->" not in status


def test_git_status_main_branch_ignores_refs_only_prefixed_by_master(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/master-old", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    status = ProjectContextProvider(
        ProjectContextConfig(), root_path=repo
    ).get_git_status()

    assert "Main branch (you will usually use this for PRs): main" in status


@pytest.mark.skipif(os.name == "nt", reason="fake git shell script is POSIX-only")
def test_run_git_timeout_returns_while_a_git_child_holds_the_output_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    child_pid = tmp_path / "child.pid"
    fake_git = tmp_path / "trusted-bin" / "git"
    fake_git.parent.mkdir()
    fake_git.write_text(f'#!/bin/sh\nsleep 30 &\necho $! > "{child_pid}"\nsleep 30\n')
    fake_git.chmod(0o755)
    monkeypatch.setenv("GIT_PYTHON_GIT_EXECUTABLE", str(fake_git))
    provider = ProjectContextProvider(ProjectContextConfig(), root_path=project)

    try:
        with pytest.raises(subprocess.TimeoutExpired):
            provider._run_git(["log"], timeout=0.5)
    finally:
        if child_pid.exists():
            os.kill(int(child_pid.read_text()), signal.SIGKILL)


def test_kill_process_tree_runs_system_taskkill_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_prompt, "is_windows", lambda: True)
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    taskkill_calls: list[list[str]] = []

    def record_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        taskkill_calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", record_run)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    system_prompt._kill_process_tree(process)

    assert taskkill_calls == [
        [
            str(Path("C:\\Windows") / "System32" / "taskkill.exe"),
            "/PID",
            str(process.pid),
            "/T",
            "/F",
        ]
    ]
    assert process.returncode is not None


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


def test_get_agents_md_section_puts_given_project_instructions_first() -> None:
    section = get_agents_md_section(
        "# User doc",
        [(Path("/repo"), "# Project doc")],
        project_instructions="  # Given instructions\n",
    )
    assert section is not None
    assert "(checked into the codebase)" not in section
    assert (
        section.index("## User instructions")
        < section.index("## Project instructions")
        < section.index("Instructions for this project:\n\n# Given instructions")
        < section.index("Contents of /repo/AGENTS.md")
    )


def test_get_agents_md_section_renders_given_project_instructions_alone() -> None:
    section = get_agents_md_section("", [], project_instructions="# Given")
    assert section is not None
    assert "## Project instructions\n\nInstructions for this project:" in section
    assert "## User instructions" not in section
    assert get_agents_md_section("", [], project_instructions=" \n ") is None
