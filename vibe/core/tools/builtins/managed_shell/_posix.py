from __future__ import annotations

import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import termios

from vibe.core.tools.builtins.managed_shell.backend import (
    ManagedShellBackendCapabilities,
    ManagedShellBackendError,
    ManagedTerminal,
)


def _resolve_executable(candidate: str) -> str | None:
    from shutil import which

    expanded = Path(candidate).expanduser()
    if os.sep in candidate or (os.altsep and os.altsep in candidate):
        if expanded.is_file() and os.access(expanded, os.X_OK):
            return str(expanded)
        return None

    return which(candidate)


def _child_preexec(slave_fd: int) -> None:
    fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)


class PosixManagedTerminal:
    def __init__(self, process: subprocess.Popen[bytes], master_fd: int) -> None:
        self._process = process
        self._master_fd = master_fd
        self.pid = process.pid
        self.pty_backend = "posix"

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int | None:
        return self._process.wait(timeout=timeout)

    def wait_readable(self, timeout_seconds: float) -> bool:
        readable, _, _ = select.select([self._master_fd], [], [], timeout_seconds)
        return bool(readable)

    def read(self, size: int) -> bytes:
        return os.read(self._master_fd, size)

    def write(self, data: bytes) -> int:
        return os.write(self._master_fd, data)

    def close(self) -> None:
        os.close(self._master_fd)


class PosixManagedShellBackend:
    capabilities = ManagedShellBackendCapabilities(
        interactive_tty=True,
        control_sequences=True,
        process_group_kill=True,
        shell_family="posix",
    )

    def resolve_shell(self, requested: str | None, configured: str | None) -> str:
        if requested:
            resolved = _resolve_executable(requested)
            if resolved:
                return resolved
            raise ManagedShellBackendError(
                f"requested shell is not executable: {requested}"
            )

        if configured:
            resolved = _resolve_executable(configured)
            if resolved:
                return resolved
            raise ManagedShellBackendError(
                f"configured shell is not executable: {configured}"
            )

        for candidate in (
            "zsh",
            "/bin/zsh",
            "/usr/bin/zsh",
            "bash",
            "/bin/bash",
            "/usr/bin/bash",
            "sh",
            "/bin/sh",
            "/usr/bin/sh",
        ):
            if resolved := _resolve_executable(candidate):
                return resolved

        raise ManagedShellBackendError(
            "no POSIX shell found; expected zsh, bash, or sh"
        )

    def start_terminal(
        self, *, shell: str, command: str, cwd: Path, env: dict[str, str]
    ) -> PosixManagedTerminal:
        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                [shell, "-lc", command],
                cwd=str(cwd),
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
                preexec_fn=lambda: _child_preexec(slave_fd),
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass

        return PosixManagedTerminal(process=process, master_fd=master_fd)

    def request_termination(self, terminal: ManagedTerminal) -> None:
        self._signal_process_group(terminal, signal.SIGTERM)

    def force_terminate_terminal(
        self, terminal: ManagedTerminal, *, timeout_seconds: float
    ) -> None:
        _ = timeout_seconds
        self._signal_process_group(terminal, signal.SIGKILL)

    @staticmethod
    def _signal_process_group(terminal: ManagedTerminal, sig: int) -> None:
        if terminal.poll() is not None or terminal.pid is None:
            return
        try:
            os.killpg(os.getpgid(terminal.pid), sig)
        except ProcessLookupError:
            return
        except OSError:
            try:
                os.kill(terminal.pid, sig)
            except OSError:
                return
