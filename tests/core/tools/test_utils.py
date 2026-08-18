from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config.harness_files import (
    HarnessFilesManager,
    reset_harness_files_manager,
)
from vibe.core.tools.builtins.read_file import ReadFile, ReadFileArgs
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.tools.utils import display_file_path, resolve_tool_path
from vibe.core.types import ToolCallEvent
from vibe.utils import paths


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: True)


def test_absent_path_resolves_to_the_working_directory(tmp_path: Path) -> None:
    assert resolve_tool_path(None, tmp_path) == tmp_path


def test_empty_path_resolves_to_the_working_directory(tmp_path: Path) -> None:
    assert resolve_tool_path("", tmp_path) == tmp_path


def test_relative_path_resolves_against_the_working_directory(tmp_path: Path) -> None:
    assert resolve_tool_path("sub/dir", tmp_path) == tmp_path.resolve() / "sub" / "dir"


def test_absolute_path_is_kept(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()

    assert resolve_tool_path(str(other), tmp_path / "unused") == other.resolve()


@pytest.mark.parametrize(
    "raw", ["/c/Users/acmedev/notes.md", "C:/Users/acmedev/notes.md"]
)
@pytest.mark.usefixtures("on_windows")
def test_windows_absolute_path_is_never_joined_onto_the_working_directory(
    raw: str, tmp_path: Path
) -> None:
    assert resolve_tool_path(raw, tmp_path) == Path("C:/Users/acmedev/notes.md")


@pytest.mark.parametrize(
    "raw",
    [
        " /c/Users/acmedev/notes.md",
        "\t/c/Users/acmedev/notes.md",
        "/c/Users/acmedev/notes.md\n",
        " /c/Users/acmedev/notes.md \n",
    ],
)
@pytest.mark.usefixtures("on_windows")
def test_windows_msys_path_with_surrounding_whitespace_resolves_consistently(
    raw: str, tmp_path: Path
) -> None:
    assert resolve_tool_path(raw, tmp_path) == Path("C:/Users/acmedev/notes.md")


def test_display_file_path_uses_cwd_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    target = workdir / "pkg" / "config.py"
    workdir.mkdir()
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    monkeypatch.chdir(workdir)
    reset_harness_files_manager()

    assert display_file_path(str(target)) == "pkg/config.py"


def test_display_file_path_keeps_outside_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    outside = tmp_path / "outside" / "config.py"
    workdir.mkdir()
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    monkeypatch.chdir(workdir)
    reset_harness_files_manager()

    assert display_file_path(str(outside)) == str(outside.resolve())


def test_display_file_path_keeps_repo_sibling_absolute_when_outside_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    workdir = repo / "subdir"
    target = repo / "pkg" / "config.py"
    workdir.mkdir(parents=True)
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    monkeypatch.chdir(workdir)
    reset_harness_files_manager()

    assert display_file_path(str(target)) == str(target.resolve())


def test_display_file_path_resolves_relative_input_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "project"
    target = workdir / "pkg" / "config.py"
    workdir.mkdir()
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    monkeypatch.chdir(workdir)
    reset_harness_files_manager()

    assert display_file_path("pkg/config.py") == "pkg/config.py"


def test_tool_display_uses_session_harness_when_process_cwd_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    workdir = repo / "subdir"
    target = workdir / "pkg" / "config.py"
    home.mkdir()
    workdir.mkdir(parents=True)
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    monkeypatch.chdir(home)
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        workdir
    )
    event = ToolCallEvent(
        tool_call_id="test",
        tool_name="read_file",
        tool_class=ReadFile,
        args=ReadFileArgs(file_path=str(target)),
    )

    presentation = ToolUIDataAdapter(
        ReadFile, harness_files=harness_files
    ).get_call_presentation(event)

    assert presentation.display.summary == "Reading pkg/config.py"
    assert presentation.display.message == "pkg/config.py"
    assert presentation.display.settled_message == "pkg/config.py"


def test_tool_display_keeps_absolute_path_outside_session_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo" / "subdir"
    target = tmp_path / "repo" / "pkg" / "config.py"
    home.mkdir()
    workdir.mkdir(parents=True)
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    monkeypatch.chdir(home)
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        workdir
    )
    event = ToolCallEvent(
        tool_call_id="test",
        tool_name="read_file",
        tool_class=ReadFile,
        args=ReadFileArgs(file_path=str(target)),
    )

    presentation = ToolUIDataAdapter(
        ReadFile, harness_files=harness_files
    ).get_call_presentation(event)

    assert presentation.display.summary == f"Reading {target.resolve()}"
    assert presentation.display.message == str(target.resolve())
    assert presentation.display.settled_message == str(target.resolve())
