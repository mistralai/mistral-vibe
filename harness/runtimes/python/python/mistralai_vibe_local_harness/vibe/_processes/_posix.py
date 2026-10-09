"""POSIX pseudo-terminal backend for local background processes."""

from __future__ import annotations

from collections.abc import Sequence
import errno
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import time
from typing import BinaryIO

from mistralai_vibe_local_harness.vibe._processes._backend import (
    ManagedTerminal,
    ProcessCommandEnvironment,
    PtyBackend,
    TerminalBackendError,
)
from mistralai_vibe_local_harness.vibe._sandbox_helper import (
    ProcessTree,
    ShellNotFoundError,
    resolve_posix_shell,
    stop_process_trees,
)

_HELPER_FAILURE_STAGES = {
    "request",
    "setsid",
    "controlling_terminal",
    "chdir",
    "stdio",
    "exec",
}
_WRITE_TIMEOUT_SECONDS = 1.0
_ERROR_FRAME_HEADER = 5


class PosixTerminal:
    def __init__(self, process: subprocess.Popen[bytes], master_fd: int) -> None:
        self._process = process
        self.tree = ProcessTree(process)
        self._master_fd = master_fd
        self._closed = False

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def pty_backend(self) -> PtyBackend:
        return "posix"

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def group_is_alive(self) -> bool:
        return self.tree.group_is_alive()

    def wait_for_group_exit(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self.group_is_alive():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
        return True

    def wait_readable(self, timeout_seconds: float) -> bool:
        if self._closed:
            return False
        readable, _, _ = select.select([self._master_fd], [], [], timeout_seconds)
        return bool(readable)

    def read(self, size: int) -> bytes:
        if self._closed:
            return b""
        try:
            return os.read(self._master_fd, size)
        except OSError as error:
            if error.errno == errno.EIO:
                return b""
            raise

    def write(self, data: bytes) -> int:
        if self._closed:
            raise EOFError("PTY is closed")
        remaining = memoryview(data)
        total = 0
        deadline = time.monotonic() + _WRITE_TIMEOUT_SECONDS
        while remaining:
            try:
                written = os.write(self._master_fd, remaining)
            except BlockingIOError:
                written = 0
            if written:
                total += written
                remaining = remaining[written:]
            if not remaining:
                break
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            _, writable, _ = select.select([], [self._master_fd], [], timeout)
            if not writable:
                break
        return total

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self._master_fd)


class PosixTerminalBackend:
    """Runs background processes in pseudo-terminals on this POSIX host.

    A stop reaches the process's group and the descendants that left it while
    they still descend from it. One reparented away before the stop (its
    parent exited) is out of reach: this process does not adopt orphans, as
    that would change how the whole process reaps its children.
    """

    command_environment: ProcessCommandEnvironment = "unix"

    def resolve_shell(self, configured: str | None, env: dict[str, str]) -> str:
        try:
            return resolve_posix_shell(configured, env)
        except ShellNotFoundError as error:
            raise TerminalBackendError(str(error)) from error

    def start_terminal(
        self, *, shell: str | None, command: str, cwd: Path, env: dict[str, str]
    ) -> PosixTerminal:
        if shell is None:
            raise TerminalBackendError("no shell was resolved")
        master_fd, slave_fd = pty.openpty()
        request_read, request_write = os.pipe()
        status_read, status_write = os.pipe()
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                _helper_argv(request_read, status_write, slave_fd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(request_read, status_write, slave_fd),
            )
            os.close(request_read)
            request_read = -1
            os.close(status_write)
            status_write = -1
            request = json.dumps(
                {
                    "version": 1,
                    "shell": shell,
                    "argv": [shell, "-lc", command],
                    "cwd": str(cwd),
                    "env": env,
                },
                separators=(",", ":"),
            ).encode()
            _write_all(request_write, struct.pack(">I", len(request)) + request)
            os.close(request_write)
            request_write = -1
            os.close(slave_fd)
            slave_fd = -1
            status_file = os.fdopen(status_read, "rb", buffering=0)
            status_read = -1
            status = _read_all(status_file)
            if status != b"R":
                process.wait()
                raise TerminalBackendError(_launch_failure_stage(status))
            os.set_blocking(master_fd, False)
            return PosixTerminal(process, master_fd)
        except BaseException:
            os.close(master_fd)
            if process is not None:
                process.kill()
                process.wait()
            raise
        finally:
            for descriptor in (
                request_read,
                request_write,
                status_read,
                status_write,
                slave_fd,
            ):
                if descriptor >= 0:
                    os.close(descriptor)

    def stop_terminals(
        self, terminals: Sequence[ManagedTerminal], grace_seconds: float
    ) -> list[bool]:
        return stop_process_trees(
            [_require_posix_terminal(terminal).tree for terminal in terminals],
            grace_seconds,
        )

    def close(self) -> None:
        return


def _helper_argv(request_fd: int, status_fd: int, slave_fd: int) -> list[str]:
    descriptors = [str(request_fd), str(status_fd), str(slave_fd)]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--internal-posix-pty-helper", *descriptors]
    return [
        sys.executable,
        "-m",
        "mistralai_vibe_local_harness.vibe._processes._posix_helper",
        *descriptors,
    ]


def _require_posix_terminal(terminal: ManagedTerminal) -> PosixTerminal:
    if not isinstance(terminal, PosixTerminal):
        raise TerminalBackendError("POSIX backend received a foreign terminal")
    return terminal


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written == 0:
            raise OSError("launch request pipe closed")
        view = view[written:]


def _read_all(file: BinaryIO) -> bytes:
    with file:
        return file.read()


def _launch_failure_stage(frame: bytes) -> str:
    ready = frame.startswith(b"R")
    if ready:
        frame = frame[1:]
    if not frame.startswith(b"E") or len(frame) < _ERROR_FRAME_HEADER:
        return "POSIX helper failed during spawn"
    size = struct.unpack(">I", frame[1:_ERROR_FRAME_HEADER])[0]
    if len(frame) != size + _ERROR_FRAME_HEADER:
        return "POSIX helper returned an invalid spawn status"
    try:
        value = json.loads(frame[_ERROR_FRAME_HEADER:])
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "POSIX helper returned an invalid spawn status"
    stage = (
        value.get("stage")
        if isinstance(value, dict) and value.get("version") == 1
        else None
    )
    return (
        f"POSIX helper failed during {stage}"
        if stage in _HELPER_FAILURE_STAGES and (not ready or stage == "exec")
        else "POSIX helper failed during spawn"
    )
