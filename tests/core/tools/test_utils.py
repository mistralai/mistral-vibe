from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.tools.utils import is_stream_device_path, names_a_link, resolve_tool_path
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


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/dev/null", True),
        ("/dev/stdout", True),
        ("/dev/fd/3", True),
        ("/dev/null/", True),
        ("/dev/fd/3/passwd", False),
        ("/dev/fd/3/../123", False),
        ("/dev/fd/3/../../null", False),
        ("/dev/fd/../../etc/passwd", False),
        ("/dev/./null", False),
        ("/dev/sda", False),
        ("dev/null", False),
    ],
)
def test_only_the_null_device_and_streams_count_as_stream_devices(
    path: str, expected: bool
) -> None:
    assert is_stream_device_path(path) is expected


def test_a_bare_name_counts_as_a_link_only_when_it_is_one(tmp_path: Path) -> None:
    (tmp_path / "plain.txt").write_text("x")
    try:
        (tmp_path / "lnk").symlink_to(tmp_path / "plain.txt")
    except OSError:
        pytest.skip("this host cannot create symlinks")

    assert names_a_link("lnk", tmp_path)
    assert not names_a_link("plain.txt", tmp_path)
    assert not names_a_link("missing", tmp_path)
    assert not names_a_link("bad\0name", tmp_path)


def test_a_bare_name_naming_a_junction_counts_as_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "jnk").mkdir()
    monkeypatch.setattr(Path, "is_junction", lambda self: self.name == "jnk")

    assert names_a_link("jnk", tmp_path)
