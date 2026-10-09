"""An agent socket server over a local directory, for ``--agent-socket``."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading

from pydantic import JsonValue
import pytest

from tests.stubs.local_sandbox import RecordingSandbox

TIMEOUT_ERROR_CODE = -32001
ABORT_ERROR_CODE = -32002
_METHOD_NOT_FOUND_CODE = -32601
_LINE_LIMIT_BYTES = 64 * 1024 * 1024
# What a POSIX shell reports for a process killed by SIGSEGV.
_SEGFAULT_EXIT_CODE = 139

type Request = dict[str, JsonValue]
# Replaces the server's answer: the raw bytes to send back for a request, or
# None to close the connection without answering.
type Reply = Callable[[Request], Awaitable[bytes | None]]
# Answers a `tools/call` request's params with a `CallbackResult`.
type ToolHandler = Callable[[dict[str, JsonValue]], Awaitable[dict[str, JsonValue]]]


class AbortRun(Exception):
    """Raised by a tool handler to answer its call by aborting the run."""


async def echo_tool(params: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Answer a tool call with its own name and input."""
    return {"output": {"tool": params["name"], "input": params["input"]}}


def encode_line(message: JsonValue) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def error_line(
    request: Request, code: int, message: str, *, data: JsonValue = None
) -> bytes:
    error: dict[str, JsonValue] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return encode_line({"jsonrpc": "2.0", "id": request["id"], "error": error})


@contextmanager
def private_socket_path() -> Iterator[Path]:
    """A socket path in a fresh 0700 directory.

    Under ``/tmp`` rather than pytest's temporary directory: a Unix socket path
    is limited to 104 bytes on macOS. Skips the test on Windows, which has no
    asyncio Unix sockets (``os.name``, since tests run with ``sys.platform``
    patched to Linux).
    """
    if os.name == "nt":
        pytest.skip("needs Unix domain sockets")
    directory = Path(tempfile.mkdtemp(prefix="vibe-sb-", dir="/tmp"))
    directory.chmod(0o700)
    try:
        yield directory / "sandbox.sock"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class FakeAgentServer:
    """Answers the agent socket protocol.

    It serves a sandbox that runs commands in ``workspace``, when there is
    one, and ``tools``, whose calls ``on_tool_call`` answers. Each connection
    carries one request line and gets one reply line, as the protocol
    requires. ``reply`` replaces the answer to every request; ``aborts`` maps
    a method to the message with which the server aborts the run instead of
    answering it; a ``sandbox/execute`` whose command ``times_out`` matches
    is answered as a command that outlived its timeout, without running it,
    naming ``time_limit`` as the limit applied when it is set. With
    ``kills_after`` set, the sandbox stops any command at that limit, as
    swerex does, and the answer names it. ``instructions``, when set, are
    the project instructions the handshake gives. A ``sandbox/execute`` whose
    command ``crashes`` matches is answered, without running it, as a process
    killed by a segmentation fault.
    """

    def __init__(
        self,
        socket_path: Path,
        workspace: Path | None,
        *,
        reply: Reply | None = None,
        tools: Sequence[dict[str, JsonValue]] = (),
        on_tool_call: ToolHandler = echo_tool,
        aborts: Mapping[str, str] | None = None,
        times_out: Callable[[str], bool] | None = None,
        time_limit: float | None = None,
        kills_after: float | None = None,
        instructions: str | None = None,
        crashes: Callable[[str], bool] | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.sandbox = (
            None
            if workspace is None
            else RecordingSandbox(workspace, limit=kills_after)
        )
        self.tools: list[JsonValue] = list(tools)
        self._on_tool_call = on_tool_call
        self._aborts = dict(aborts or {})
        self._times_out = times_out
        self._time_limit = time_limit if kills_after is None else kills_after
        self._instructions = instructions
        self._crashes = crashes
        self.requests: list[Request] = []
        self.peak_active_requests = 0
        self._active_requests = 0
        self._reply = reply
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> FakeAgentServer:
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(self.socket_path), limit=_LINE_LIMIT_BYTES
        )
        os.chmod(self.socket_path, 0o600)
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        assert self._server is not None
        self._server.close()
        # A reply that never comes would hold the server open.
        for handler in self._handlers:
            handler.cancel()
        await self._server.wait_closed()

    @property
    def methods(self) -> list[JsonValue]:
        return [request["method"] for request in self.requests]

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if (handler := asyncio.current_task()) is not None:
            self._handlers.add(handler)
        self._active_requests += 1
        self.peak_active_requests = max(
            self.peak_active_requests, self._active_requests
        )
        try:
            line = await reader.readline()
            if not line:
                return
            request = json.loads(line)
            self.requests.append(request)
            reply = await (self._reply or self._answer)(request)
            if reply is not None:
                writer.write(reply)
                await writer.drain()
        finally:
            self._active_requests -= 1
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def _answer(self, request: Request) -> bytes:
        params = request["params"]
        assert isinstance(params, dict)
        method = request["method"]
        if isinstance(method, str) and method in self._aborts:
            return error_line(request, ABORT_ERROR_CODE, self._aborts[method])
        try:
            result = await self._result(request["method"], params)
        except TimeoutError:
            return error_line(
                request,
                TIMEOUT_ERROR_CODE,
                "The command timed out",
                data=None
                if self._time_limit is None
                else {"timeoutSeconds": self._time_limit},
            )
        except AbortRun as e:
            return error_line(request, ABORT_ERROR_CODE, str(e))
        except LookupError as e:
            return error_line(request, _METHOD_NOT_FOUND_CODE, str(e))
        return encode_line({"jsonrpc": "2.0", "id": request["id"], "result": result})

    async def _result(
        self, method: JsonValue, params: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        match method:
            case "agent/initialize":
                sandbox: dict[str, JsonValue] = (
                    {}
                    if self.sandbox is None
                    else {
                        "workspace": self.sandbox.workspace,
                        "python": self.sandbox.python,
                    }
                )
                given: dict[str, JsonValue] = (
                    {}
                    if self._instructions is None
                    else {"instructions": self._instructions}
                )
                return {"protocolVersion": 2, **sandbox, "tools": self.tools, **given}
            case "tools/call":
                return await self._on_tool_call(params)
            case "sandbox/execute" | "sandbox/readFile" if self.sandbox is None:
                raise LookupError("This server has no sandbox")
            case "sandbox/execute":
                command, cwd, timeout = (
                    params["command"],
                    params["cwd"],
                    params["timeout"],
                )
                assert isinstance(command, str) and isinstance(cwd, str)
                assert timeout is None or isinstance(timeout, int | float)
                assert self.sandbox is not None
                if self._times_out is not None and self._times_out(command):
                    raise TimeoutError(command)
                if self._crashes is not None and self._crashes(command):
                    return {
                        "exitCode": _SEGFAULT_EXIT_CODE,
                        "stdout": "",
                        "stderr": "Segmentation fault (core dumped)",
                    }
                result = await self.sandbox.execute(command, cwd, timeout)
                return {
                    "exitCode": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            case "sandbox/readFile":
                path, max_bytes = params["path"], params["maxBytes"]
                assert isinstance(path, str) and isinstance(max_bytes, int)
                assert self.sandbox is not None
                content = await self.sandbox.read_file(path, max_bytes)
                return {
                    "contentBase64": None
                    if content is None
                    else base64.b64encode(content).decode("ascii")
                }
            case _:
                raise LookupError(f"Unknown method {method}")


@contextmanager
def serving_in_background(server: FakeAgentServer) -> Iterator[FakeAgentServer]:
    """Serve ``server`` on its own event loop thread, for a blocking caller."""
    loop = asyncio.new_event_loop()
    started = threading.Event()
    stop = asyncio.Event()

    async def serve() -> None:
        async with server:
            started.set()
            await stop.wait()

    thread = threading.Thread(target=loop.run_until_complete, args=(serve(),))
    thread.start()
    try:
        assert started.wait(10), "the fake agent server did not start"
        yield server
    finally:
        loop.call_soon_threadsafe(stop.set)
        thread.join(10)
        loop.close()
