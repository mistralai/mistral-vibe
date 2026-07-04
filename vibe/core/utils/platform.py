from __future__ import annotations

import logging
import os
from pathlib import Path
import platform
from shutil import which
import sys
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from vibe.core.tools.builtins.bash import BashToolConfig

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

logger = logging.getLogger("vibe")


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


def get_windows_bash_path() -> str | None:
    """Find a bash executable on Windows.

    Searches PATH for ``bash.exe``, derives it from ``git.exe``'s install
    directory, and falls back to common installation paths (Git Bash, Cygwin,
    MSYS2). Returns ``None`` on non-Windows or when no bash is found.
    """
    if not is_windows():
        return None

    # 1. Find bash.exe via PATH (direct) or derive from git.exe's parent
    if not (bash_path := which("bash.exe")):
        if git_path := which("git.exe"):
            parent = Path(git_path).parent.parent / "bin"  # Git\cmd -> Git\bin
            derived_bash = parent / "bash.exe"
            if derived_bash.exists():
                bash_path = str(derived_bash)
    if bash_path:
        return bash_path

    # 2. Fall back to common Bash installation paths
    for path in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\cygwin64\bin\bash.exe",
        r"C:\cygwin\bin\bash.exe",
        r"C:\msys64\usr\bin\bash.exe",
        r"C:\msys32\usr\bin\bash.exe",
    ):
        if Path(path).exists():
            return path

    return None


def get_shell_executable(config: BashToolConfig | None = None) -> str | None:
    """Get the preferred shell executable for running commands.

    On POSIX: Uses $SHELL if defined, sh otherwise.
    On Windows: Checks VIBE_SHELL env var first, then config's preferred_shell,
    then delegates to :func:`get_windows_bash_path` for automatic discovery.

    This is the centralized function for determining which shell to use,
    ensuring consistency between the bash tool and system prompt generation.
    """
    if not is_windows():
        return os.environ.get("SHELL", "sh")

    env_var = os.environ.get("VIBE_SHELL")
    if env_var:
        if Path(env_var).exists():
            return env_var
        else:
            logger.warning(
                f"VIBE_SHELL environment variable is set but this path does not exist: {env_var}."
            )

    if config and config.preferred_shell:
        if Path(config.preferred_shell).exists():
            return config.preferred_shell
        else:
            logger.warning(f"Preferred shell {config.preferred_shell} does not exist.")

    return get_windows_bash_path() or os.environ.get("COMSPEC", "cmd.exe")
