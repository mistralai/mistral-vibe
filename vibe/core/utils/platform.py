from __future__ import annotations

import platform
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
