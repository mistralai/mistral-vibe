from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Protocol

from vibe.core.utils import is_windows


class ManagedBashBackendError(Exception):
    pass


@dataclass(frozen=True)
class ManagedBashBackendCapabilities:
    interactive_tty: bool
    control_sequences: bool
    process_group_kill: bool
    shell_family: str


class ManagedBashBackend(Protocol):
    capabilities: ManagedBashBackendCapabilities

    def resolve_shell(self, requested: str | None, configured: str | None) -> str: ...

    def open_terminal(self) -> tuple[int, int]: ...

    def spawn(
        self, *, shell: str, command: str, cwd: Path, env: dict[str, str], slave_fd: int
    ) -> subprocess.Popen[bytes]: ...

    def wait_readable(self, master_fd: int, timeout_seconds: float) -> bool: ...

    def read(self, master_fd: int, size: int) -> bytes: ...

    def write(self, master_fd: int, data: bytes) -> int: ...

    def close_fd(self, fd: int) -> None: ...

    def terminate_process_group(
        self, process: subprocess.Popen[bytes], *, force: bool, grace_seconds: float
    ) -> None: ...


def managed_bash_supported() -> bool:
    return not is_windows()


def create_managed_bash_backend() -> ManagedBashBackend:
    if is_windows():
        raise ManagedBashBackendError("managed bash requires a POSIX-like platform")

    from vibe.core.tools.builtins.managed_bash._posix import PosixManagedBashBackend

    return PosixManagedBashBackend()
