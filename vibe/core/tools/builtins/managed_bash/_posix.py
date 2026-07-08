from __future__ import annotations

import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import termios

from vibe.core.tools.builtins.managed_bash.backend import (
    ManagedBashBackendCapabilities,
    ManagedBashBackendError,
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
    # setsid() is handled safely by CPython via start_new_session=True; the
    # remaining post-fork work is a single async-signal-safe ioctl that makes
    # the pty slave the child's controlling terminal.
    fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)


class PosixManagedBashBackend:
    capabilities = ManagedBashBackendCapabilities(
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
            raise ManagedBashBackendError(
                f"requested shell is not executable: {requested}"
            )

        if configured:
            resolved = _resolve_executable(configured)
            if resolved:
                return resolved
            raise ManagedBashBackendError(
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

        raise ManagedBashBackendError("no POSIX shell found; expected zsh, bash, or sh")

    def open_terminal(self) -> tuple[int, int]:
        return pty.openpty()

    def spawn(
        self, *, shell: str, command: str, cwd: Path, env: dict[str, str], slave_fd: int
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
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

    def wait_readable(self, master_fd: int, timeout_seconds: float) -> bool:
        readable, _, _ = select.select([master_fd], [], [], timeout_seconds)
        return bool(readable)

    def read(self, master_fd: int, size: int) -> bytes:
        return os.read(master_fd, size)

    def write(self, master_fd: int, data: bytes) -> int:
        return os.write(master_fd, data)

    def close_fd(self, fd: int) -> None:
        os.close(fd)

    def terminate_process_group(
        self, process: subprocess.Popen[bytes], *, force: bool, grace_seconds: float
    ) -> None:
        if process.poll() is not None:
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except ProcessLookupError:
            return
        except OSError:
            try:
                process.kill()
            except OSError:
                return

        if force:
            return
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            self.terminate_process_group(
                process, force=True, grace_seconds=grace_seconds
            )
