"""Background processes that run in a Sandbox Environment.

A process server in the sandbox owns the processes, so they outlive the helper
call that started them. Every operation here is one helper call through the
Sandbox Adapter, and a pump thread polls the server for output and state
changes while any process of this backend runs, so the Session process manager
reads these terminals as it reads local ones.

That pump is the feature's steady cost. Each poll is a helper call: a fresh
interpreter in the sandbox, then two loopback connections to the server, one
to check it answers and one for the poll. The server holds a poll for up to
``_POLL_WAIT_MS`` and answers early on output or a state change. So the pump
makes about two calls a second while its processes are quiet, and calls back
to back while they print. The interpreter start dominates each call; the
check connection adds well under a millisecond. A longer hold would cut the
quiet rate, but a sandbox that runs one call at a time would make the
Session's other tools wait behind it.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
from pathlib import Path
import secrets
import subprocess
import threading
import time
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)
from pydantic.alias_generators import to_camel

from mistralai_vibe_local_harness.vibe._processes._backend import (
    ManagedTerminal,
    ProcessCommandEnvironment,
    PtyBackend,
    TerminalBackendError,
    TerminalStartError,
    WorkingDirectoryError,
)
from mistralai_vibe_local_harness.vibe._sandbox import (
    SandboxAdapter,
    SandboxToolError,
    SandboxToolTimeoutError,
    SandboxUnavailableError,
    run_helper,
)

_CALL_TIMEOUT_SECONDS = 60.0
_POLL_WAIT_MS = 500
_POLL_MAX_BYTES = 256 * 1_024
_UNREACHABLE_SECONDS = 60.0
_RETRY_SECONDS = 0.5
_MAX_RETRY_SECONDS = 5.0


class _Message(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, frozen=True)


class _Succeeded(_Message):
    outcome: Literal["succeeded"]
    value: JsonValue


class _Failed(_Message):
    outcome: Literal["failed"]
    code: str
    message: str


_OUTCOME: TypeAdapter[_Succeeded | _Failed] = TypeAdapter(
    Annotated[_Succeeded | _Failed, Field(discriminator="outcome")]
)


class _Started(_Message):
    pid: int


class _Written(_Message):
    bytes_written: int


class _LiveProcess(_Message):
    state: Literal["live"]
    output_base64: str
    cursor: int
    version: int
    root_exit_code: int | None
    group_alive: bool
    ended: bool


class _MissingProcess(_Message):
    state: Literal["missing"]


class _Polled(_Message):
    processes: dict[
        str, Annotated[_LiveProcess | _MissingProcess, Field(discriminator="state")]
    ]


class _StoppedProcess(_Message):
    state: Literal["live"]
    version: int
    root_exit_code: int | None
    group_alive: bool
    stopped: bool


class _Stopped(_Message):
    processes: dict[
        str, Annotated[_StoppedProcess | _MissingProcess, Field(discriminator="state")]
    ]


class SandboxTerminal:
    """A process in the sandbox, as last reported by its server."""

    def __init__(
        self, backend: SandboxTerminalBackend, terminal_id: str, pid: int
    ) -> None:
        self._backend = backend
        self.terminal_id = terminal_id
        self._pid = pid
        self._condition = threading.Condition()
        self._unread = bytearray()
        self._cursor = 0
        self._version = 0
        self._root_exit_code: int | None = None
        self._group_alive = True
        self._ended = False
        self._failure: str | None = None
        self._closed = False

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def pty_backend(self) -> PtyBackend:
        return "posix"

    @property
    def returncode(self) -> int | None:
        with self._condition:
            return self._root_exit_code

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        with self._condition:
            if not self._condition.wait_for(
                lambda: self._root_exit_code is not None or self._failure is not None,
                timeout,
            ):
                raise subprocess.TimeoutExpired("sandbox process", timeout or 0)
            return self._root_exit_code

    def group_is_alive(self) -> bool:
        with self._condition:
            self._raise_failure()
            return self._group_alive

    def wait_for_group_exit(self, timeout: float) -> bool:
        with self._condition:
            exited = self._condition.wait_for(
                lambda: not self._group_alive or self._failure is not None, timeout
            )
            self._raise_failure()
            return exited

    def wait_readable(self, timeout_seconds: float) -> bool:
        with self._condition:
            return self._condition.wait_for(self._readable, timeout_seconds)

    def read(self, size: int) -> bytes:
        with self._condition:
            if not self._unread and self._failure is not None:
                raise OSError(self._failure)
            chunk = bytes(self._unread[:size])
            del self._unread[:size]
            return chunk

    def write(self, data: bytes) -> int:
        try:
            written = self._backend.call(
                "write",
                {
                    "terminalId": self.terminal_id,
                    "dataBase64": base64.b64encode(data).decode("ascii"),
                },
            )
        except TerminalBackendError as error:
            raise OSError(str(error)) from error
        match written:
            case _Succeeded(value=value):
                return _Written.model_validate(value).bytes_written
            case _Failed(message=message):
                raise OSError(message)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def polled(self) -> bool:
        """Whether the pump still has something to learn about this process."""
        with self._condition:
            return not (self._ended or self._closed or self._failure is not None)

    @property
    def position(self) -> dict[str, int]:
        with self._condition:
            return {"cursor": self._cursor, "version": self._version}

    def absorb(self, state: _LiveProcess) -> None:
        with self._condition:
            self._unread.extend(base64.b64decode(state.output_base64))
            self._cursor = state.cursor
            self._ended = state.ended
            self._settle(state.version, state.root_exit_code, state.group_alive)
            self._condition.notify_all()

    def settle(self, state: _StoppedProcess) -> None:
        """Take the state a stop reported, unless a poll reported a later one."""
        with self._condition:
            self._settle(state.version, state.root_exit_code, state.group_alive)
            self._condition.notify_all()

    def _settle(
        self, version: int, root_exit_code: int | None, group_alive: bool
    ) -> None:
        # A poll answered before a stop must not bring the stopped process back.
        if version < self._version:
            return
        self._version = version
        self._root_exit_code = root_exit_code
        self._group_alive = group_alive

    def fail(self, reason: str) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = reason
            self._condition.notify_all()

    def _readable(self) -> bool:
        return bool(self._unread or self._ended or self._failure is not None)

    def _raise_failure(self) -> None:
        if self._failure is not None:
            raise TerminalBackendError(self._failure)


class SandboxTerminalBackend:
    """Runs a Session's background processes through its sandbox's process server.

    ``loop`` is the event loop the sandbox adapter runs on; every call blocks a
    worker thread, never the loop.
    """

    command_environment: ProcessCommandEnvironment = "unix"

    def __init__(
        self,
        sandbox: SandboxAdapter,
        *,
        session_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._sandbox = sandbox
        self._session_id = session_id
        self._loop = loop
        # A server that hears from a new owner stops what earlier ones started.
        self._owner = secrets.token_hex(16)
        self._lock = threading.Lock()
        self._terminals: dict[str, SandboxTerminal] = {}
        self._pump: threading.Thread | None = None
        self._closing = False

    def resolve_shell(self, configured: str | None, env: dict[str, str]) -> str | None:
        # The sandbox resolves the shell against its own file system.
        return configured

    def start_terminal(
        self, *, shell: str | None, command: str, cwd: Path, env: dict[str, str]
    ) -> SandboxTerminal:
        terminal_id = secrets.token_hex(12)
        started = self.call(
            "start",
            {
                "terminalId": terminal_id,
                "command": command,
                "cwd": str(cwd),
                "env": dict[str, JsonValue](env),
                "shell": shell,
            },
        )
        match started:
            case _Failed(code="invalid_working_directory", message=message):
                raise WorkingDirectoryError(message)
            case _Failed(code="shell_not_found", message=message):
                raise TerminalStartError("shell_resolution", message)
            case _Failed(code="unsupported_platform", message=message):
                raise TerminalStartError("unsupported_platform", message)
            case _Failed(message=message):
                raise TerminalBackendError(message)
            case _Succeeded(value=value):
                pid = _Started.model_validate(value).pid
        terminal = SandboxTerminal(self, terminal_id, pid)
        with self._lock:
            self._terminals[terminal_id] = terminal
            if self._pump is None:
                self._pump = threading.Thread(
                    target=self._run_pump, name="sandbox-process-pump", daemon=True
                )
                self._pump.start()
        return terminal

    def stop_terminals(
        self, terminals: Sequence[ManagedTerminal], grace_seconds: float
    ) -> list[bool]:
        """Stop the processes in one helper call, which the server sees through.

        Its signals and grace do not wait on further calls, however slow the
        sandbox is to answer them.
        """
        sandboxed = [_require_sandbox_terminal(terminal) for terminal in terminals]
        if not sandboxed:
            return []
        answer = self.call(
            "stop",
            {
                "terminalIds": [terminal.terminal_id for terminal in sandboxed],
                "graceMs": round(grace_seconds * 1_000),
            },
            timeout=_CALL_TIMEOUT_SECONDS + 2 * grace_seconds,
        )
        match answer:
            case _Failed(message=message):
                raise TerminalBackendError(message)
            case _Succeeded(value=value):
                try:
                    processes = _Stopped.model_validate(value).processes
                except ValidationError as error:
                    raise TerminalBackendError("Malformed stop response") from error
        contained: list[bool] = []
        for terminal in sandboxed:
            match processes.get(terminal.terminal_id):
                case _StoppedProcess() as state:
                    terminal.settle(state)
                    contained.append(state.stopped)
                case _:
                    terminal.fail("Sandbox process server lost the process")
                    contained.append(False)
        return contained

    def close(self) -> None:
        """Stop polling, and stop the sandbox's server with what it still runs.

        Calls the server even when this backend started nothing: a run that
        resumes a Session must stop what the run before it left running.
        """
        with self._lock:
            self._closing = True
            pump = self._pump
        if pump is not None:
            pump.join(_CALL_TIMEOUT_SECONDS)
        try:
            self.call("shutdown", {})
        except TerminalBackendError:
            return

    def call(
        self,
        operation: str,
        payload: dict[str, JsonValue],
        *,
        timeout: float = _CALL_TIMEOUT_SECONDS,
    ) -> _Succeeded | _Failed:
        """Run one process-server operation through the sandbox helper."""
        if _running_loop() is self._loop:
            raise TerminalBackendError("Sandbox process calls cannot block the loop")
        request: dict[str, JsonValue] = {
            "sessionId": self._session_id,
            "owner": self._owner,
            "operation": operation,
            "payload": payload,
        }
        try:
            future = asyncio.run_coroutine_threadsafe(
                run_helper(self._sandbox, "process", request, timeout=timeout),
                self._loop,
            )
        except RuntimeError as error:
            raise TerminalBackendError(f"Sandbox is closed: {error}") from error
        try:
            result = future.result(timeout + 5)
        except TimeoutError as error:
            future.cancel()
            raise _CallTimedOut("Sandbox process call timed out") from error
        except SandboxToolTimeoutError as error:
            raise _CallTimedOut(str(error)) from error
        except SandboxUnavailableError as error:
            # The adapter gave up on the call, or the sandbox stopped the
            # helper's install or request upload at its time limit.
            if isinstance(error.__cause__, (TimeoutError, SandboxToolTimeoutError)):
                raise _CallTimedOut(str(error)) from error
            raise TerminalBackendError(str(error)) from error
        except SandboxToolError as error:
            raise TerminalBackendError(str(error)) from error
        try:
            return _OUTCOME.validate_python(result)
        except ValidationError as error:
            raise TerminalBackendError("Malformed process server response") from error

    def _run_pump(self) -> None:
        # The sandbox may stop answering for a while, as a remote one does
        # when its connection drops: polls are retried, ever less often,
        # until it was unreachable for a minute. A poll that times out is not
        # one: a sandbox that runs one call at a time holds it behind a long
        # command, and a sandbox that hangs fails the Session's other tools.
        unreachable_since: float | None = None
        retry_seconds = _RETRY_SECONDS
        while True:
            with self._lock:
                polled = {
                    terminal_id: terminal
                    for terminal_id, terminal in self._terminals.items()
                    if terminal.polled
                }
                self._terminals = dict(polled)
                if not polled or self._closing:
                    self._pump = None
                    return
            try:
                states = self._poll(polled)
            except _CallTimedOut:
                time.sleep(_RETRY_SECONDS)
                continue
            except TerminalBackendError as error:
                now = time.monotonic()
                if unreachable_since is None:
                    unreachable_since = now
                if now - unreachable_since >= _UNREACHABLE_SECONDS:
                    for terminal in polled.values():
                        terminal.fail(f"Sandbox process server is unreachable: {error}")
                    unreachable_since = None
                    retry_seconds = _RETRY_SECONDS
                    continue
                time.sleep(retry_seconds)
                retry_seconds = min(retry_seconds * 2, _MAX_RETRY_SECONDS)
                continue
            unreachable_since = None
            retry_seconds = _RETRY_SECONDS
            for terminal_id, terminal in polled.items():
                match states.get(terminal_id):
                    case _LiveProcess() as state:
                        terminal.absorb(state)
                    case _:
                        terminal.fail("Sandbox process server lost the process")

    def _poll(
        self, terminals: dict[str, SandboxTerminal]
    ) -> dict[str, _LiveProcess | _MissingProcess]:
        polled = self.call(
            "poll",
            {
                "processes": {
                    terminal_id: dict(terminal.position)
                    for terminal_id, terminal in terminals.items()
                },
                "waitMs": _POLL_WAIT_MS,
                "maxBytes": _POLL_MAX_BYTES,
            },
        )
        match polled:
            case _Failed(message=message):
                raise TerminalBackendError(message)
            case _Succeeded(value=value):
                try:
                    return _Polled.model_validate(value).processes
                except ValidationError as error:
                    raise TerminalBackendError("Malformed poll response") from error


class _CallTimedOut(TerminalBackendError):
    """The sandbox did not answer a process call in time."""


def _require_sandbox_terminal(terminal: ManagedTerminal) -> SandboxTerminal:
    if not isinstance(terminal, SandboxTerminal):
        raise TerminalBackendError("Sandbox backend received a foreign terminal")
    return terminal


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


__all__ = ["SandboxTerminal", "SandboxTerminalBackend"]
