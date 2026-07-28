from __future__ import annotations

from dataclasses import dataclass
import enum
from functools import lru_cache
import os
from pathlib import Path, PureWindowsPath
import platform
import shutil
import sys
from typing import Final

_PLATFORM_IDS: Final[dict[str, str]] = {
    "win32": "windows",
    "darwin": "darwin",
    "linux": "linux",
    "freebsd": "freebsd",
    "openbsd": "openbsd",
    "netbsd": "netbsd",
}

_PLATFORM_DISPLAY_NAMES: Final[dict[str, str]] = {
    "windows": "Windows",
    "darwin": "macOS",
    "linux": "Linux",
    "freebsd": "FreeBSD",
    "openbsd": "OpenBSD",
    "netbsd": "NetBSD",
}


def is_windows() -> bool:
    return sys.platform == "win32"


def get_platform_id() -> str:
    """Canonical lowercase platform identifier (e.g. ``windows``, ``darwin``, ``linux``).

    Matches the values expected by ``ExperimentAttributes.os`` and is suitable for
    machine-readable contexts (telemetry, experiment targeting). Falls back to the
    raw ``sys.platform`` value for unknown platforms.
    """
    return _PLATFORM_IDS.get(sys.platform, sys.platform)


def get_platform_version() -> str | None:
    match get_platform_id():
        case "darwin":
            version = platform.mac_ver()[0] or platform.release()
        case "windows":
            version = platform.version() or platform.release()
        case "linux":
            version = _linux_os_version() or platform.release()
        case _:
            version = platform.release() or platform.version()
    return version or None


def _linux_os_version() -> str | None:
    try:
        os_release = platform.freedesktop_os_release()
    except OSError:
        return None
    return os_release.get("VERSION_ID") or os_release.get("VERSION")


def get_platform_display_name() -> str:
    """Human-readable platform name (e.g. ``Windows``, ``macOS``, ``Linux``).

    Suitable for surfacing in system prompts. Falls back to ``Unix-like`` for
    unknown platforms.
    """
    return _PLATFORM_DISPLAY_NAMES.get(get_platform_id(), "Unix-like")


class WindowsShellKind(enum.Enum):
    """Which shell the bash tool actually drives on Windows."""

    BASH = "bash"
    CMD = "cmd"


@dataclass(frozen=True)
class WindowsShell:
    """Resolved Windows shell shared by the executor and the system prompt.

    ``executable`` is the path to invoke for the resolved shell.
    """

    kind: WindowsShellKind
    executable: str | None


def _is_wsl_launcher(path: str) -> bool:
    """The WSL bash stubs forward into a Linux VM with its own
    filesystem, so it is not a drop-in shell for the current working directory.
    """
    norm = path.replace("\\", "/").lower()
    return (
        norm.endswith("/system32/bash.exe")
        or norm.endswith("/system32/bash")
        or norm.endswith("/microsoft/windowsapps/bash.exe")
        or norm.endswith("/microsoft/windowsapps/bash")
    )


def get_windows_bash_path() -> str | None:
    """Best-effort path to a usable ``bash.exe`` on Windows, or ``None``.

    Search order: ``PATH`` (scanning every entry, not just the first match),
    then a bash shipped alongside ``git.exe`` (Git for Windows), then common
    install locations. The WSL launcher at ``System32\\bash.exe`` is
    intentionally ignored — ``shutil.which`` reports only the first hit, so a
    real Git Bash / MSYS2 bash listed after the WSL stub must still win.
    """
    if not is_windows():
        return None

    for path_entry in os.get_exec_path():
        candidate = shutil.which("bash", path=path_entry)
        if candidate and not _is_wsl_launcher(candidate):
            return candidate

    git = shutil.which("git")
    if git:
        # Git for Windows layout: <git>\cmd\git.exe with bash under <git>\bin.
        git_root = Path(git).resolve().parent.parent
        for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
            path = git_root / rel
            if path.is_file():
                return str(path)

    bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for base in bases:
        if not base:
            continue
        for rel in ("Git/bin/bash.exe", "Programs/Git/bin/bash.exe"):
            path = Path(base) / rel
            if path.is_file():
                return str(path)

    return None


def _is_cmd_executable(path: str) -> bool:
    name = PureWindowsPath(path.strip('"')).name.lower()
    return name in {"cmd", "cmd.exe"}


def _get_windows_cmd_path() -> str:
    comspec = os.environ.get("COMSPEC")
    if comspec and _is_cmd_executable(comspec):
        return comspec

    system_root = os.environ.get("SystemRoot")
    if system_root:
        return str(PureWindowsPath(system_root.strip('"')) / "System32" / "cmd.exe")

    return "cmd.exe"


@lru_cache(maxsize=1)
def resolve_windows_shell() -> WindowsShell:
    """Resolve the shell the bash tool will drive on Windows.

    An auto-detected bash is preferred, otherwise an explicit cmd.exe path. This
    is the single source of truth consumed by both the command executor and the
    system prompt so the two never disagree.
    """
    bash = get_windows_bash_path()
    if bash:
        return WindowsShell(WindowsShellKind.BASH, bash)

    return WindowsShell(WindowsShellKind.CMD, _get_windows_cmd_path())
