from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import shutil

from vibe.core.tools.builtins.managed_shell.backend import ManagedShellBackendError
from vibe.utils.platform import get_windows_bash_path

WINDOWS_POWERSHELL_DEFAULT_SHELLS: tuple[str, ...] = ("pwsh.exe", "powershell.exe")


@dataclass(frozen=True)
class WindowsShellSpec:
    executable: str
    family: str


def pywinpty_available() -> bool:
    try:
        importlib.import_module("winpty")
    except ImportError:
        return False
    return True


def _resolve_executable(candidate: str) -> str | None:
    expanded = Path(candidate).expanduser()
    path_like = (
        os.sep in candidate
        or (os.altsep is not None and os.altsep in candidate)
        or ":" in candidate
    )
    if path_like:
        if expanded.is_file():
            return str(expanded)
        return None
    return shutil.which(candidate)


def resolve_git_bash_shell_spec(
    requested: str | None, configured: str | None
) -> WindowsShellSpec:
    source = requested or configured
    if source:
        resolved = _resolve_executable(source)
        if resolved:
            family = _shell_family_from_executable(resolved)
            if family != "bash":
                raise ManagedShellBackendError(
                    f"Git Bash shell override must resolve to bash.exe, got {source}"
                )
            return WindowsShellSpec(executable=resolved, family=family)
        kind = "requested" if requested else "configured"
        raise ManagedShellBackendError(f"{kind} shell is not executable: {source}")

    if bash := get_windows_bash_path():
        return WindowsShellSpec(executable=bash, family="bash")

    raise ManagedShellBackendError("no Git Bash shell found; expected bash.exe")


def resolve_git_bash_shell(requested: str | None, configured: str | None) -> str:
    return resolve_git_bash_shell_spec(requested, configured).executable


def git_bash_shell_available() -> bool:
    try:
        resolve_git_bash_shell(None, None)
    except ManagedShellBackendError:
        return False
    return True


def resolve_powershell_shell_spec(
    requested: str | None, configured: str | None
) -> WindowsShellSpec:
    source = requested or configured
    if source:
        resolved = _resolve_executable(source)
        if resolved:
            family = _shell_family_from_executable(resolved)
            if family != "powershell":
                raise ManagedShellBackendError(
                    "PowerShell shell override must resolve to pwsh.exe "
                    f"or powershell.exe, got {source}"
                )
            return WindowsShellSpec(executable=resolved, family=family)
        kind = "requested" if requested else "configured"
        raise ManagedShellBackendError(f"{kind} shell is not executable: {source}")

    for candidate in WINDOWS_POWERSHELL_DEFAULT_SHELLS:
        if resolved := _resolve_executable(candidate):
            return WindowsShellSpec(
                executable=resolved, family=_shell_family_from_executable(candidate)
            )

    raise ManagedShellBackendError(
        "no PowerShell shell found; expected pwsh.exe or powershell.exe"
    )


def resolve_windows_shell_spec(
    requested: str | None, configured: str | None
) -> WindowsShellSpec:
    return resolve_powershell_shell_spec(requested, configured)


def resolve_powershell_shell(requested: str | None, configured: str | None) -> str:
    return resolve_powershell_shell_spec(requested, configured).executable


def resolve_windows_shell(requested: str | None, configured: str | None) -> str:
    return resolve_powershell_shell(requested, configured)


def _shell_family_from_executable(executable: str) -> str:
    name = Path(executable).name.lower()
    if name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
        return "powershell"
    if name in {"cmd", "cmd.exe"}:
        return "cmd"
    if name in {"bash", "bash.exe"}:
        return "bash"
    return "custom"
