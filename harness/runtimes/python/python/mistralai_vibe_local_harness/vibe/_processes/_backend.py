"""Terminal backend contract shared by the Session process manager."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

type ProcessCommandEnvironment = Literal["unix", "git_bash", "powershell"]
type PtyBackend = Literal["posix", "ConPTY", "WinPTY"]


class TerminalBackendError(Exception):
    pass


class ManagedTerminal(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def pty_backend(self) -> PtyBackend: ...

    @property
    def returncode(self) -> int | None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int | None: ...

    def wait_readable(self, timeout_seconds: float) -> bool: ...

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


class TerminalBackend(Protocol):
    command_environment: ProcessCommandEnvironment

    def resolve_shell(self, configured: str | None, env: dict[str, str]) -> str: ...

    def start_terminal(
        self, *, shell: str, command: str, cwd: Path, env: dict[str, str]
    ) -> ManagedTerminal: ...

    def request_termination(self, terminal: ManagedTerminal) -> None: ...

    def force_termination(self, terminal: ManagedTerminal) -> None: ...


__all__ = [
    "ManagedTerminal",
    "ProcessCommandEnvironment",
    "PtyBackend",
    "TerminalBackend",
    "TerminalBackendError",
]
