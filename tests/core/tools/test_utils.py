from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.tools.utils import resolve_tool_path
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
