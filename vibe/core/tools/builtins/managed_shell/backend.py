from __future__ import annotations

from vibe.core.utils import is_windows


class ManagedShellBackendError(Exception):
    pass


def posix_managed_shell_supported() -> bool:
    return not is_windows()


def windows_managed_shell_supported() -> bool:
    if not is_windows():
        return False
    try:
        from vibe.core.tools.builtins.managed_shell._windows import pywinpty_available
    except Exception:
        return False
    return pywinpty_available()


def git_bash_managed_shell_supported() -> bool:
    if not is_windows():
        return False
    try:
        from vibe.core.tools.builtins.managed_shell._windows import (
            git_bash_shell_available,
            pywinpty_available,
        )
    except Exception:
        return False
    return pywinpty_available() and git_bash_shell_available()


def powershell_managed_shell_supported() -> bool:
    if not is_windows():
        return False
    try:
        from vibe.core.tools.builtins.managed_shell._windows import (
            pywinpty_available,
            resolve_powershell_shell,
        )
    except Exception:
        return False
    if not pywinpty_available():
        return False
    try:
        resolve_powershell_shell(None, None)
    except ManagedShellBackendError:
        return False
    return True


def managed_shell_supported(shell_family: str | None = None) -> bool:
    match shell_family:
        case "posix":
            return posix_managed_shell_supported()
        case "git_bash":
            return git_bash_managed_shell_supported()
        case "powershell":
            return powershell_managed_shell_supported()
        case "windows":
            return windows_managed_shell_supported()
        case None:
            return windows_managed_shell_supported() if is_windows() else True
        case _:
            return False
