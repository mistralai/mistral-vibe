"""Drive a command in a PTY and capture settled terminal frames."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass, field
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
    ack_prefix: bytes
    """Start of the client's fence ack; the acked fence count and BEL follow."""
    fence_key: str
    """Input sent after each key batch, on the same stream, for the client to ack."""
    steps: tuple[str, ...]
    resizes: tuple[tuple[int, int] | None, ...] = ()
    failsafe: float = 30.0
    settle_per_key: bool = False
    """Fence every keypress instead of each batch of plain keys."""
    exit_after_last_step: bool = False
    terminal_responses: tuple[tuple[bytes, bytes], ...] = ()
    """Terminal output queries and the input replies they trigger."""
    exit_responses: tuple[tuple[bytes, bytes], ...] = ()
    """Post-exit output triggers and the input replies they unlock, FIFO."""


# Keys whose effect depends on what the UI already shows (a submit acts on the
# open completion popup, a tab on its selection), so they are never batched with
# the keys that produced that state.
_COMMIT_KEYS = frozenset({"\r", "\n", "\t", "\x1b"})

# A raw Escape read together with the fence key parses as `ESC ESC`, eating the fence, so a lone Escape uses its CSI u form.
_CSI_U_ESCAPE = "\x1b[27u"
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
    releases_after: tuple[int, ...] = (),
    capture_startup: bool = True,
    capture_steps: Set[int] | None = None,
    step_fd: int | None = None,
    launch: Launcher = exec_launch,
) -> list[Snapshot]:
    """Capture startup and one settled frame after each input step."""
    pid, fd = launch(spec)
    client = _Client(
        fd, spec, Terminal(spec.rows, spec.columns), (spec.rows, spec.columns)
    )
    try:
        client.settle()
        snapshots = [client.snapshot("startup")] if capture_startup else []
        for index, step in enumerate(spec.steps):
            client.release(step_fd, releases[index] if index < len(releases) else 0)
            if spec.exit_after_last_step and index == len(spec.steps) - 1:
                _write_all(fd, step.encode())
                _wait_for_exit(pid, fd, spec.failsafe, spec.exit_responses)
                continue
            resize = spec.resizes[index] if index < len(spec.resizes) else None
            if resize is None:
                client.send_step(step)
            else:
                client.resize(*resize)
            after = releases_after[index] if index < len(releases_after) else 0
            client.release(step_fd, after)
            if capture_steps is None or index in capture_steps:
                snapshots.append(client.snapshot(f"step{index}"))
        return snapshots
    finally:
        _terminate(pid, fd)


@dataclass
class _Client:
    """One driven client: each stimulus carries a fence, and a settle waits for its ack."""

    fd: int
    spec: CaptureSpec
    terminal: Terminal
    size: tuple[int, int]
    clipboard: _ClipboardCapture = field(default_factory=_ClipboardCapture)
    buffered: bytearray = field(default_factory=bytearray)
    fences: int = 0
    """Fences sent so far; startup is fence 0."""

    def snapshot(self, label: str) -> Snapshot:
        return self.terminal.snapshot(label, self.clipboard.text)

    def send_step(self, step: str) -> None:
        """Send one input step, fencing each batch (or each key) of it."""
        keys = split_keys(step) if self.spec.settle_per_key else _batches(step)
        for batch in keys:
            batch = _CSI_U_ESCAPE if batch == "\x1b" else batch
            _write_all(self.fd, f"{batch}{self.spec.fence_key}".encode())
            self._fenced()

    def release(self, step_fd: int | None, count: int) -> None:
        """Release replay events one by one; the replay server fences each."""
        for _ in range(count):
            if step_fd is None:
                raise RuntimeError("replay release has no step FIFO")
            os.write(step_fd, b"\0")
            self._fenced()

    def resize(self, rows: int, columns: int) -> None:
        """Resize the PTY; the client's resize event is its own fence."""
        if (rows, columns) == self.size:
            raise ValueError("a resize step must change the terminal size")
        self.size = (rows, columns)
        _set_window_size(self.fd, rows, columns)
        self.terminal.resize(rows, columns)
        self._fenced()

    def _fenced(self) -> None:
        self.fences += 1
        self.settle()

    def settle(self) -> None:
        """Feed output until the client acks the latest fence."""
        while (ack := self._take_ack()) is None:
            data = _read(self.fd, self.spec.failsafe)
            self.clipboard.feed(data)
            self.buffered += data
        if ack != self.fences:
            raise RuntimeError(f"client acked fence {ack}, expected {self.fences}")

    def _take_ack(self) -> int | None:
        """Feed the terminal up to the next complete ack and return its count."""
        _answer_terminal_queries(self.fd, self.buffered, self.spec.terminal_responses)
        prefix = self.spec.ack_prefix
        if (start := self.buffered.find(prefix)) == -1:
            return None
        if (end := self.buffered.find(b"\x07", start + len(prefix))) == -1:
            return None
        count = int(self.buffered[start + len(prefix) : end])
        self.terminal.feed(bytes(self.buffered[:start]))
        del self.buffered[: end + 1]
        return count


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
        raise EOFError("client exited before acking its fence") from error
    if not chunk:
        raise EOFError("client exited before acking its fence")
    return chunk


def _answer_terminal_queries(
    fd: int, buffered: bytearray, responses: tuple[tuple[bytes, bytes], ...]
) -> None:
    for query, response in responses:
        if not query:
            continue
        while (query_index := buffered.find(query)) != -1:
            del buffered[query_index : query_index + len(query)]
            _write_all(fd, response)


def _wait_for_exit(
    pid: int,
    fd: int,
    failsafe: float,
    exit_responses: tuple[tuple[bytes, bytes], ...] = (),
) -> None:
    """Drain terminal cleanup until the client exits, then verify the exit."""
    deadline = time.monotonic() + failsafe
    drained = bytearray()
    pending = list(exit_responses)
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
        # A post-exit prompt (the client asks before it lets go) unlocks its
        # reply the moment its text lands in the drain, ordered FIFO. The
        # match spans the whole drain, pre-exit bytes included: triggers must
        # be text only the exit path can print.
        while pending and pending[0][0] in drained:
            _, reply = pending.pop(0)
            _write_all(fd, reply)
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
    """Kill the process group, then close the PTY, without blocking."""
    # Kill first: a PTY hangup lets the client run its exit path and send requests.
    for target in (
        lambda: os.killpg(os.getpgid(pid), signal.SIGKILL),
        lambda: os.kill(pid, signal.SIGKILL),
    ):
        try:
            target()
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass
