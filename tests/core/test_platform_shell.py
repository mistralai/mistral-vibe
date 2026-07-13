from __future__ import annotations

import sys

import pytest

from vibe.core.utils.platform import (
    WindowsShellKind,
    get_windows_bash_path,
    resolve_windows_shell,
)


def _hide_standard_git_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def test_get_windows_bash_path_returns_none_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert get_windows_bash_path() is None


def test_get_windows_bash_path_prefers_path_bash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "C:\\tools")
    monkeypatch.setattr(
        "vibe.core.utils.platform.shutil.which",
        lambda name, path=None: "C:\\tools\\bash.exe" if name == "bash" else None,
    )
    assert get_windows_bash_path() == "C:\\tools\\bash.exe"


def test_get_windows_bash_path_ignores_wsl_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setenv("PATH", "C:\\Windows\\System32")
    # Only the WSL stub is on PATH and no git is found -> no usable bash.
    monkeypatch.setattr(
        "vibe.core.utils.platform.shutil.which",
        lambda name, path=None: (
            "C:\\Windows\\System32\\bash.exe" if name == "bash" else None
        ),
    )
    assert get_windows_bash_path() is None


def test_get_windows_bash_path_ignores_windowsapps_wsl_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setenv("PATH", "C:\\Users\\me\\AppData\\Local\\Microsoft\\WindowsApps")
    monkeypatch.setattr(
        "vibe.core.utils.platform.shutil.which",
        lambda name, path=None: (
            "C:\\Users\\me\\AppData\\Local\\Microsoft\\WindowsApps\\bash.exe"
            if name == "bash"
            else None
        ),
    )
    assert get_windows_bash_path() is None


def test_get_windows_bash_path_scans_past_wsl_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "vibe.core.utils.platform.os.get_exec_path",
        lambda: [
            "C:\\Windows\\System32",
            "C:\\Users\\me\\AppData\\Local\\Microsoft\\WindowsApps",
            "C:\\Program Files\\Git\\bin",
        ],
    )

    def fake_which(name: str, path: str | None = None) -> str | None:
        if name != "bash":
            return None
        if path == "C:\\Windows\\System32":
            return "C:\\Windows\\System32\\bash.exe"
        if path == "C:\\Users\\me\\AppData\\Local\\Microsoft\\WindowsApps":
            return "C:\\Users\\me\\AppData\\Local\\Microsoft\\WindowsApps\\bash.exe"
        if path == "C:\\Program Files\\Git\\bin":
            return "C:\\Program Files\\Git\\bin\\bash.exe"
        return None

    monkeypatch.setattr("vibe.core.utils.platform.shutil.which", fake_which)
    assert get_windows_bash_path() == "C:\\Program Files\\Git\\bin\\bash.exe"


def test_resolve_windows_shell_falls_back_to_cmd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
    monkeypatch.setattr(
        "vibe.core.utils.platform.shutil.which", lambda name, path=None: None
    )
    shell = resolve_windows_shell()
    assert shell.kind is WindowsShellKind.CMD
    assert shell.executable == "C:\\Windows\\System32\\cmd.exe"


def test_resolve_windows_shell_ignores_non_cmd_comspec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setenv(
        "COMSPEC", "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    )
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setattr(
        "vibe.core.utils.platform.shutil.which", lambda name, path=None: None
    )

    shell = resolve_windows_shell()

    assert shell.kind is WindowsShellKind.CMD
    assert shell.executable == "C:\\Windows\\System32\\cmd.exe"


def test_resolve_windows_shell_auto_detects_bash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "C:\\tools")
    monkeypatch.setattr(
        "vibe.core.utils.platform.shutil.which",
        lambda name, path=None: "C:\\tools\\bash.exe" if name == "bash" else None,
    )
    shell = resolve_windows_shell()
    assert shell.kind is WindowsShellKind.BASH
    assert shell.executable == "C:\\tools\\bash.exe"


def test_resolve_windows_shell_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    scans = 0

    def counting_which(name: str, path: str | None = None) -> str | None:
        nonlocal scans
        scans += 1
        return None

    monkeypatch.setattr("vibe.core.utils.platform.shutil.which", counting_which)

    first = resolve_windows_shell()
    after_first = scans
    second = resolve_windows_shell()

    assert second is first
    assert scans == after_first
