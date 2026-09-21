"""Drive a command in a PTY and capture settled terminal frames."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
import fcntl
import os
import pty
import select
import signal
import struct
import termios
import time

from e2e.pty.keys import split_keys
from e2e.pty.screen import Snapshot, Terminal


@dataclass(frozen=True)
class CaptureSpec:
    """Everything needed to drive one terminal command."""

    command: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    rows: int
    columns: int
    idle_marker: bytes
    steps: tuple[str, ...]
    resizes: tuple[tuple[int, int] | None, ...] = ()
    failsafe: float = 30.0
    batch_keys: tuple[str, str] | None = None
    """Hold and release keys bracketing a step, or None to settle per keypress."""
    exit_after_last_step: bool = False
    terminal_responses: tuple[tuple[bytes, bytes], ...] = ()
    """Terminal output queries and the input replies they trigger."""


# Keys whose effect depends on what the UI already shows (a submit acts on the
# open completion popup, a tab on its selection), so they are never batched with
# the keys that produced that state.
_COMMIT_KEYS = frozenset({"\r", "\n", "\t", "\x1b"})

# A lone Escape is ambiguous, and the two clients disagree on how to resolve it:
# crossterm merges `ESC ESC` into one Escape, eating the release marker, while
# Textual holds an Escape until more bytes arrive. Writing the Escape as the last
# byte of its own read and the release marker after the client consumed it
# satisfies both. The wait ends as soon as the client repaints, so only a client
# that needs the release (Textual) pays it in full.
_ESCAPE_DRAIN = 0.1
_OSC52 = b"\x1b]52;c;"


class _ClipboardCapture:
    def __init__(self) -> None:
        self.buffer = b""
        self.text: str | None = None

    def feed(self, data: bytes) -> None:
        self.buffer += data
        while (start := self.buffer.find(_OSC52)) >= 0:
            end = self.buffer.find(b"\x07", start + len(_OSC52))
            if end < 0:
                self.buffer = self.buffer[start:]
                return
            payload = self.buffer[start + len(_OSC52) : end]
            try:
                self.text = base64.b64decode(payload, validate=True).decode()
            except (binascii.Error, UnicodeDecodeError):
                pass
            self.buffer = self.buffer[end + 1 :]
        self.buffer = self.buffer[-(len(_OSC52) - 1) :]


Launcher = Callable[[CaptureSpec], tuple[int, int]]
"""Start a client on its own PTY and return its pid and the master fd."""


def exec_launch(spec: CaptureSpec) -> tuple[int, int]:
    """Launch a client by forking and exec'ing its command."""
    pid, fd = pty.fork()
    if pid == 0:
        _exec_child(spec)
    _set_window_size(fd, spec.rows, spec.columns)
    return pid, fd


def capture(
    spec: CaptureSpec,
    *,
    releases: tuple[int, ...] = (),
    capture_startup: bool = True,
    capture_steps: Set[int] | None = None,
    step_fd: int | None = None,
    launch: Launcher = exec_launch,
) -> list[Snapshot]:
    """Capture startup and one settled frame after each input step."""
    pid, fd = launch(spec)
    terminal = Terminal(spec.rows, spec.columns)
    clipboard = _ClipboardCapture()
    try:
        buffered = _settle(fd, terminal, clipboard, bytearray(), spec)
        snapshots = (
            [terminal.snapshot("startup", clipboard.text)] if capture_startup else []
        )
        for index, step in enumerate(spec.steps):
            for _ in range(releases[index] if index < len(releases) else 0):
                if step_fd is None:
                    raise RuntimeError("replay release has no step FIFO")
                os.write(step_fd, b"\0")
                buffered = _settle(fd, terminal, clipboard, buffered, spec)
            if spec.exit_after_last_step and index == len(spec.steps) - 1:
                _write_all(fd, step.encode())
                _wait_for_exit(pid, fd, spec.failsafe)
                continue
            resize = spec.resizes[index] if index < len(spec.resizes) else None
            if resize is None:
                buffered = _send_step(fd, terminal, clipboard, buffered, spec, step)
            else:
                rows, columns = resize
                _set_window_size(fd, rows, columns)
                terminal.resize(rows, columns)
                buffered = _settle(fd, terminal, clipboard, buffered, spec)
            if capture_steps is None or index in capture_steps:
                snapshots.append(terminal.snapshot(f"step{index}", clipboard.text))
        return snapshots
    finally:
        _terminate(pid, fd)


def _send_step(
    fd: int,
    terminal: Terminal,
    clipboard: _ClipboardCapture,
    buffered: bytearray,
    spec: CaptureSpec,
    step: str,
) -> bytearray:
    """Send one input step and settle, batching its keys when the client allows."""
    if spec.batch_keys is None:
        for key in split_keys(step):
            _write_all(fd, key.encode())
            buffered = _settle(fd, terminal, clipboard, buffered, spec)
        return buffered
    hold, release = spec.batch_keys
    for batch in _batches(step):
        if batch == "\x1b":
            buffered = _write_escape(fd, clipboard, buffered, hold, release)
        else:
            _write_all(fd, f"{hold}{batch}{release}".encode())
        buffered = _settle(fd, terminal, clipboard, buffered, spec)
    return buffered


def _write_escape(
    fd: int, clipboard: _ClipboardCapture, buffered: bytearray, hold: str, release: str
) -> bytearray:
    """Write a lone Escape, then release once the client has consumed it."""
    _write_all(fd, f"{hold}\x1b".encode())
    ready, _, _ = select.select([fd], [], [], _ESCAPE_DRAIN)
    if fd in ready:
        data = os.read(fd, 65536)
        clipboard.feed(data)
        buffered += data
    _write_all(fd, release.encode())
    return buffered


def _batches(step: str) -> list[str]:
    """Group a step's keys into runs settled together, isolating commit keys."""
    batches: list[str] = []
    run = ""
    for key in split_keys(step):
        if key not in _COMMIT_KEYS:
            run += key
            continue
        if run:
            batches.append(run)
            run = ""
        batches.append(key)
    if run:
        batches.append(run)
    return batches


def _write_all(fd: int, data: bytes) -> None:
    while data:
        data = data[os.write(fd, data) :]


def _exec_child(spec: CaptureSpec) -> None:
    """Configure and replace the forked child process."""
    _set_window_size(1, spec.rows, spec.columns)
    os.chdir(spec.cwd)
    try:
        os.execvpe(spec.command[0], spec.command, dict(spec.env))
    finally:
        os._exit(127)


def _set_window_size(fd: int, rows: int, columns: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def _read(fd: int, failsafe: float) -> bytes:
    ready, _, _ = select.select([fd], [], [], failsafe)
    if fd not in ready:
        raise TimeoutError("client produced no output within the failsafe window")
    try:
        chunk = os.read(fd, 65536)
    except OSError as error:
        raise EOFError("client exited before emitting an idle marker") from error
    if not chunk:
        raise EOFError("client exited before emitting an idle marker")
    return chunk


def _settle(
    fd: int,
    terminal: Terminal,
    clipboard: _ClipboardCapture,
    buffered: bytearray,
    spec: CaptureSpec,
) -> bytearray:
    while True:
        _answer_terminal_queries(fd, buffered, spec.terminal_responses)
        if (marker_index := buffered.find(spec.idle_marker)) != -1:
            break
        data = _read(fd, spec.failsafe)
        clipboard.feed(data)
        buffered += data
    terminal.feed(bytes(buffered[:marker_index]))
    del buffered[: marker_index + len(spec.idle_marker)]
    return buffered


def _answer_terminal_queries(
    fd: int, buffered: bytearray, responses: tuple[tuple[bytes, bytes], ...]
) -> None:
    for query, response in responses:
        if not query:
            continue
        while (query_index := buffered.find(query)) != -1:
            del buffered[query_index : query_index + len(query)]
            _write_all(fd, response)


def _wait_for_exit(pid: int, fd: int, failsafe: float) -> None:
    """Drain terminal cleanup until the client exits, then verify the exit."""
    deadline = time.monotonic() + failsafe
    drained = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("client did not exit within the failsafe window")
        ready, _, _ = select.select([fd], [], [], remaining)
        if fd not in ready:
            raise TimeoutError("client did not exit within the failsafe window")
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        drained += chunk
    # A full-screen client must leave the alternate screen and give the cursor back.
    assert b"\x1b[?1049l" in drained, (
        "client did not leave the alternate screen on exit"
    )
    assert b"\x1b[?25h" in drained, "client did not show the cursor on exit"
    # The client may not be our direct child, so reap ours within a bounded wait.
    while True:
        try:
            reaped, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if reaped == pid:
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (
                f"client exited uncleanly: status {status}"
            )
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "client hit PTY EOF but was not reaped in the failsafe window"
            )
        time.sleep(0.01)


def _terminate(pid: int, fd: int) -> None:
    """Close the PTY and kill its process group without blocking."""
    try:
        os.close(fd)
    except OSError:
        pass
    for target in (
        lambda: os.killpg(os.getpgid(pid), signal.SIGKILL),
        lambda: os.kill(pid, signal.SIGKILL),
    ):
        try:
            target()
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass
