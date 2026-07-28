from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vibe.core.utils import is_windows


class ManagedShellBackendError(Exception):
    pass


@dataclass(frozen=True)
class ManagedShellBackendCapabilities:
    interactive_tty: bool
    control_sequences: bool
    process_group_kill: bool
    shell_family: str


class ManagedTerminal(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def pty_backend(self) -> str | None: ...

    @property
    def returncode(self) -> int | None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int | None: ...

    def wait_readable(self, timeout_seconds: float) -> bool: ...

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


class ManagedShellBackend(Protocol):
    capabilities: ManagedShellBackendCapabilities

    def resolve_shell(self, requested: str | None, configured: str | None) -> str: ...

    def start_terminal(
        self, *, shell: str, command: str, cwd: Path, env: dict[str, str]
    ) -> ManagedTerminal: ...

    def request_termination(self, terminal: ManagedTerminal) -> None: ...

    def force_terminate_terminal(
        self, terminal: ManagedTerminal, *, timeout_seconds: float
    ) -> None: ...


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


def create_managed_shell_backend(
    shell_family: str | None = None,
) -> ManagedShellBackend:
    family = shell_family or ("windows" if is_windows() else "posix")
    match family:
        case "posix":
            if is_windows():
                raise ManagedShellBackendError(
                    "managed POSIX shell requires a POSIX-like platform"
                )
            from vibe.core.tools.builtins.managed_shell._posix import (
                PosixManagedShellBackend,
            )

            return PosixManagedShellBackend()
        case "git_bash" | "powershell" | "windows":
            if not is_windows():
                raise ManagedShellBackendError(
                    "managed Windows shell requires native Windows"
                )
            from vibe.core.tools.builtins.managed_shell._windows import (
                WindowsManagedShellBackend,
            )

            return WindowsManagedShellBackend(shell_family=family)
        case _:
            raise ManagedShellBackendError(f"unknown managed shell family: {family}")
