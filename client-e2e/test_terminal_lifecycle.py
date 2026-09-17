"""Exercise the Rust CLI terminal lifecycle through a real PTY."""

from __future__ import annotations

import os
from pathlib import Path
import pty
import select
import signal
import sys
import termios
import time

from e2e.app_server.capture import _environment, _isolated_home
from e2e.app_server.config import CLIENTS, IDLE_MARKER
from e2e.app_server.replay import replay_fixture
from e2e.app_server.scenario import Scenario
import pytest

_STOPPED = b"VIBE_JOB_STOPPED"
_WRAPPER = (
    "import subprocess,sys; raise SystemExit(subprocess.run(sys.argv[1:]).returncode)"
)


def _read_until(fd: int, marker: bytes, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    output = bytearray()
    while marker not in output and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if fd in ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
    return bytes(output)


def _is_raw(fd: int) -> bool:
    flags = termios.tcgetattr(fd)[3]
    return not flags & termios.ICANON and not flags & termios.ECHO


def _controller(command: tuple[str, ...], cwd: str, env: dict[str, str]) -> None:
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    job = os.fork()
    if job == 0:
        os.setpgid(0, 0)
        os.chdir(cwd)
        os.execve(sys.executable, [sys.executable, "-c", _WRAPPER, *command], env)
    os.setpgid(job, job)
    os.write(1, f"VIBE_JOB:{job}\n".encode())
    os.tcsetpgrp(0, job)
    _, status = os.waitpid(job, os.WUNTRACED)
    if not os.WIFSTOPPED(status):
        os._exit(2)
    os.tcsetpgrp(0, os.getpgrp())
    os.write(1, _STOPPED)
    while os.read(0, 1) != b"\n":
        pass
    os.tcsetpgrp(0, job)
    os.killpg(job, signal.SIGCONT)
    os.waitpid(job, 0)


def _terminate(controller_pid: int, job_pid: int | None, fd: int) -> None:
    if job_pid is not None:
        try:
            os.killpg(job_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        os.kill(controller_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(controller_pid, os.WNOHANG)
    os.close(fd)


@pytest.mark.skipif(os.name != "posix", reason="Ctrl+Z is POSIX-only")
def test_ctrl_z_suspends_once_and_fg_restores_terminal(tmp_path: Path) -> None:
    scenario = Scenario(name="terminal_lifecycle", steps=[])
    with _isolated_home() as home, replay_fixture(scenario) as fixture:
        env = _environment(
            home, fixture, {}, tmp_path / "actions.jsonl", tmp_path / "requests.jsonl"
        )
        controller_pid, fd = pty.fork()
        if controller_pid == 0:
            _controller(CLIENTS["rust"], os.fspath(tmp_path), env)
            os._exit(0)

        job_pid = None
        try:
            launch = _read_until(fd, b"VIBE_JOB:", 3)
            job_pid = int(launch.split(b"VIBE_JOB:", 1)[1].splitlines()[0])
            assert IDLE_MARKER in _read_until(fd, IDLE_MARKER, 10)

            os.write(fd, b"\x1a")
            stopped = _read_until(fd, _STOPPED, 2)
            assert _STOPPED in stopped
            assert not _is_raw(fd)
            # The shell prompt must get its cursor back: ratatui hides it while drawing.
            assert b"\x1b[?25h" in stopped
            # Python-parity: the suspended hint tells the user how to return.
            assert b"suspended. Run fg" in stopped

            os.write(fd, b"fg\n")
            resumed = _read_until(fd, b"\x1b[2J", 2)
            assert b"\x1b[2J" in resumed
            # Resume re-hides the real cursor before the first redraw.
            assert b"\x1b[?25l" in resumed
            assert _is_raw(fd)
        finally:
            _terminate(controller_pid, job_pid, fd)
