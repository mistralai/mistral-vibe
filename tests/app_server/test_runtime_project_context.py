from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server._runtime import _with_extra_roots
from vibe.core import system_prompt
from vibe.utils.platform import WindowsShell, WindowsShellKind


@pytest.mark.parametrize(
    ("shell_kind", "expects_git_bash_path"),
    [(WindowsShellKind.BASH, True), (WindowsShellKind.CMD, False)],
)
def test_extra_roots_add_git_bash_path_when_shell_is_git_bash(
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

    context = _with_extra_roots(
        "context", [Path("D:\\work\\lib"), Path("\\\\server\\share\\repo")]
    )

    assert (" - D:\\work\\lib (Git Bash path: /d/work/lib)" in context) is (
        expects_git_bash_path
    )
    assert " - \\\\server\\share\\repo\n" in f"{context}\n"


def test_extra_roots_are_listed_unchanged_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_prompt, "is_windows", lambda: False)

    context = _with_extra_roots("context", [Path("/work/lib")])

    assert context.endswith("\n - /work/lib")
