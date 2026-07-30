from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from vibe.core.config import ProjectContextConfig
from vibe.core.system_prompt import ProjectContextProvider


@pytest.mark.skipif(os.name == "nt", reason="fake git shell script is POSIX-only")
def test_run_git_survives_non_utf8_output(tmp_path: Path, monkeypatch) -> None:
    # Fake git that prints bytes 0x80 0x81 (invalid UTF-8, and invalid gbk here)
    fake_git = tmp_path / "git"
    fake_git.write_text('#!/bin/sh\nprintf "commit \\200\\201 msg\\n"\n')
    fake_git.chmod(0o755)
    # Put the fake first on PATH so _run_git executes it instead of real git
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    provider = ProjectContextProvider(ProjectContextConfig(), root_path=tmp_path)

    # Without encoding="utf-8", errors="replace" this raises UnicodeDecodeError
    result = provider._run_git(["log"], timeout=5.0)

    # The bad bytes are replaced with U+FFFD instead of crashing
    assert "\ufffd" in result.stdout


def test_project_context_includes_git_file_overview(
    tmp_path: Path, monkeypatch
) -> None:
    def fake_run_git(
        self: ProjectContextProvider, args: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        stdout = ""
        if args == ["ls-files"]:
            stdout = "src/main.py\nREADME.md\nsrc/main.py\n\n"
        elif args == ["branch", "--show-current"]:
            stdout = "main\n"
        elif args == ["branch", "-r"]:
            stdout = "origin/main\n"
        elif args[:2] == ["log", "--oneline"]:
            stdout = "abc123 init\n"
        elif args != ["status", "--porcelain"]:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(ProjectContextProvider, "_run_git", fake_run_git)

    provider = ProjectContextProvider(ProjectContextConfig(), root_path=tmp_path)

    context = provider.get_full_context()

    assert "Project file overview (snapshot at conversation start):" in context
    assert "- README.md" in context
    assert "- src/main.py" in context
    assert context.count("- src/main.py") == 1


def test_project_context_file_overview_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run_git(
        self: ProjectContextProvider, args: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        stdout = ""
        if args == ["ls-files"]:
            stdout = "\n".join(f"file_{index}.py" for index in range(5))
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(ProjectContextProvider, "_run_git", fake_run_git)
    monkeypatch.setattr("vibe.core.system_prompt._MAX_FILE_OVERVIEW_ENTRIES", 2)
    provider = ProjectContextProvider(ProjectContextConfig(), root_path=tmp_path)

    overview = provider.get_file_overview()

    assert "- file_0.py" in overview
    assert "- file_1.py" in overview
    assert "- file_2.py" not in overview
    assert "... 3 more files omitted" in overview


def test_project_context_file_overview_falls_back_to_directory_scan(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("", encoding="utf-8")
    (tmp_path / ".hidden").write_text("", encoding="utf-8")

    monkeypatch.setattr(ProjectContextProvider, "_list_git_files", lambda self: [])

    provider = ProjectContextProvider(ProjectContextConfig(), root_path=tmp_path)

    overview = provider.get_file_overview()

    assert "- pkg/module.py" in overview
    assert "ignored.js" not in overview
    assert ".hidden" not in overview
