from __future__ import annotations

import pytest

from vibe.utils import paths
from vibe.utils.paths import (
    UnanchoredWindowsPathError,
    normalize_windows_input_path,
    normalize_windows_path,
)


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/c/Users/acmedev/test", "C:/Users/acmedev/test"),
        ("/c/", "C:/"),
        ("/c", "C:/"),
        ("/D/repo", "D:/repo"),
        ("/c\\Users\\acmedev", "C:/Users/acmedev"),
        ("/c/notes\n", "C:/notes"),
        ("/c/two\nlines", "C:/two\nlines"),
        (" /c/Users/acmedev", "C:/Users/acmedev"),
        ("/c/space \n", "C:/space"),
        ("\t/c/Users/acmedev\t", "C:/Users/acmedev"),
    ],
)
@pytest.mark.usefixtures("on_windows")
def test_normalize_converts_msys_drive_paths(raw: str, expected: str) -> None:
    assert normalize_windows_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "/Users/acmedev",
        "/cc/repo",
        "/1/repo",
        "relative/path",
        "C:/already/windows",
        "",
        "/",
        "//server/share",
    ],
)
@pytest.mark.usefixtures("on_windows")
def test_normalize_leaves_everything_else_alone(raw: str) -> None:
    assert normalize_windows_path(raw) == raw


@pytest.mark.parametrize("raw", ["/c/Users/acmedev", "/Users/acmedev"])
def test_normalize_is_a_no_op_off_windows(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: False)

    assert normalize_windows_path(raw) == raw
    assert normalize_windows_input_path(raw) == raw


@pytest.mark.usefixtures("on_windows")
def test_input_path_converts_msys_drive_paths() -> None:
    assert normalize_windows_input_path("/c/Users/acmedev/test") == (
        "C:/Users/acmedev/test"
    )


@pytest.mark.parametrize(
    "raw",
    ["/Users/acmedev/test", "\\Users\\acmedev", "/etc", "C:notes.md", "D:sub/dir"],
)
@pytest.mark.usefixtures("on_windows")
def test_input_path_rejects_unanchored_paths(raw: str) -> None:
    with pytest.raises(UnanchoredWindowsPathError) as excinfo:
        normalize_windows_input_path(raw)

    assert raw in str(excinfo.value)


@pytest.mark.parametrize(
    "raw",
    ["C:\\Users\\acmedev", "src/main.py", "\\\\server\\share\\file.txt", "~/notes.md"],
)
@pytest.mark.usefixtures("on_windows")
def test_input_path_passes_through_resolvable_windows_inputs(raw: str) -> None:
    assert normalize_windows_input_path(raw) == raw
