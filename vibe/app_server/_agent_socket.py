"""The agent socket of ``vibe -p --agent-socket``: a sandbox and tools served on
a Unix socket.

Experimental: the flag is hidden and this protocol may change or go away
without notice.

The CLI hands the path to ``vibe-app-server --agent-socket PATH``, which runs
the session's tools and is therefore the process that connects. It reports
how a failed handshake should end the run in the ``agentSocketOutcome`` field
of the session's open error, and an abort during the run as an
``agentSocket/aborted`` notification.

The caller runs a server on a Unix socket it created with mode 0600 inside a
0700 directory. Vibe connects once per request: it writes one request line,
reads one reply line, and closes the connection, so concurrent requests use
concurrent connections. The server should answer them concurrently too: a
request's timeout covers the time it waits behind others, such as a background
process's poll behind a long shell command. Lines are JSON-RPC 2.0 messages
with camelCase fields, framed like the app server's: compact JSON followed by
``\\n``.

The socket has two independent halves: a sandbox for the session's file and
shell tools, and tools of the caller's own. A server may serve either or both.

``agent/initialize``, called once before the run:
    ``{"protocolVersion": 2, "vibeVersion": str}`` ->
    ``{"protocolVersion": 2, "workspace": str | null, "python": str | null,
    "tools": [ToolDefinition], "toolTimeoutSeconds": float | null,
    "instructions": str | null}``. Vibe speaks version 2 only. ``workspace``
    and ``python`` come together: with them, the session's file and shell
    tools run in the sandbox and the session starts in ``workspace``; without
    them, those tools run on this host. ``tools`` defaults to none.
    ``toolTimeoutSeconds``, positive, is how long a ``tools/call`` may take,
    by default 600 seconds; the run's time limit still caps it.
    ``instructions`` are project instructions for the session: the system
    prompt carries them where it carries the AGENTS.md files of the project,
    ahead of those files.

The sandbox half:

``sandbox/execute``:
    ``{"command": str, "cwd": str, "timeout": float | null}`` ->
    ``{"exitCode": int, "stdout": str, "stderr": str}``; a non-zero exit is a
    result, not an error.
``sandbox/readFile``:
    ``{"path": str, "maxBytes": int}`` -> ``{"contentBase64": str | null}``;
    null when there is no such file.

The tools half. A ``ToolDefinition`` is ``{"namespace": str, "name": str,
"description": str, "inputSchema": object, "outputSchema": object | null,
"modelAccess": "direct" | "programmatic" | "both"}``; ``namespace`` defaults
to ``client`` and ``modelAccess`` to ``programmatic``, which lets the model
call the tool only from code it runs. A namespace cannot be Vibe's own nor
the name of one of the session's MCP servers, and a ``namespace.name`` is
declared once; otherwise the run cannot start, as a usage error.

``tools/call``, once per call of a tool, carrying its ``client_tool`` payload:
    ``{"callbackId": str, "name": str, "input": JSON, "toolCallId": str,
    "caller": Caller | null}`` -> a ``CallbackResult``: ``{"output": JSON,
    "annotations": object, "error": {"message": str, "code": str | null,
    "details": JSON} | null}``. ``callbackId`` is unique to the call and
    ``name`` is ``namespace.name``; ``output`` is the tool's result and
    ``error`` a tool error the model reads.
    ``Caller`` is the session that made the call: ``{"sessionId": str,
    "rootSessionId": str, "parentSessionId": str | null, "name": str | null,
    "agentType": str | null}``. The run's own session is the root, with no
    parent, name or agent; a subagent names its parent, its own name and the
    agent it runs as. ``caller`` is null when Vibe could not tell.

A request that outlives its timeout is answered with the JSON-RPC error code
-32001. For ``sandbox/execute``, that is the command's own timeout: the model
reads it as a timed-out command, as on the host, and the run goes on. A server
that stopped the command sooner than ``timeout``, at a limit of its own, says
so in the error's optional ``data``: ``{"timeoutSeconds": float}``, which the
model is then told instead.
For ``tools/call``, it is a tool error the model reads. Any other error means
the sandbox failed or, for ``tools/call``, that the call did, which the model
reads as a tool error. A connection Vibe closes before its reply means it no
longer waits for it: Vibe waits for a command's reply up to 30 seconds past
its ``timeout``, and a sandbox that has not answered by then has failed.
Unknown reply fields are ignored.

The JSON-RPC error code -32002, on the reply to any request, aborts the run:
``{"code": -32002, "message": str}``. Vibe stops the run as it does on
SIGTERM, writes the export with the outcome ``aborted`` and ``message`` as its
``error.message``, and exits with code 4. Vibe gives the abort no meaning of
its own; ``message`` is for whoever reads the export.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
import itertools
import json
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, ValidationError

from mistralai_vibe_local_harness.vibe import (
    SANDBOX_FAILURES,
    SandboxCommandTimeoutError,
    SandboxExecResult,
    SandboxUnavailableError,
)
from vibe import __version__
from vibe.app_server._client_provided_tools import (
    CLIENT_TOOL_TIMEOUT_SECONDS,
    DEFAULT_NAMESPACE,
    ClientToolCall,
    ClientToolDeclarationError,
    ClientToolDefinition,
    ClientToolError,
    ClientToolModelAccess,
    ClientToolResult,
    ClientTools,
    check_client_tools,
)
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.protocol import ProtocolErrorCode, SessionOptions
from vibe.app_server.run_export import RunOutcome

PROTOCOL_VERSION = 2
TIMEOUT_ERROR_CODE = -32001
"""The JSON-RPC error code of a request that outlived its timeout."""
ABORT_ERROR_CODE = -32002
"""The JSON-RPC error code with which the server aborts the run."""

# One reply line carries a whole tool result: a file read or a search.
REPLY_LIMIT_BYTES = 64 * 1024 * 1024
# How long past its own timeout a command's reply may take to arrive.
EXECUTE_GRACE_SECONDS = 30.0
# The bound on the handshake and on a file read.
REQUEST_TIMEOUT_SECONDS = 60.0

_REQUEST_IDS = itertools.count(1)

type Notifier = Callable[[str, ProtocolModel], Awaitable[None]]
"""Sends an app server notification to its client."""

type AbortHandler = Callable[[str], Awaitable[None] | None]
"""Called with the server's message when it aborts the run, and awaited before
the request raises when it returns an awaitable."""


class RunAbortedError(SandboxUnavailableError):
    """The server aborted the run. Its message is the exception's."""


class _ServerTimeoutError(TimeoutError):
    """The server answered that the request outlived its timeout.

    ``timeout`` is the limit the server applied, when its error said.
    """

    def __init__(self, message: str, *, timeout: float | None) -> None:
        super().__init__(message)
        self.timeout = timeout


def _ignore_abort(_message: str) -> None:
    pass


class _Params(ProtocolModel):
    pass


class _InitializeParams(_Params):
    protocol_version: int
    vibe_version: str


class _ExecuteParams(_Params):
    command: str
    cwd: str
    timeout: float | None


class _ReadFileParams(_Params):
    path: str
    max_bytes: int


class _ToolCaller(_Params):
    session_id: str
    root_session_id: str
    parent_session_id: str | None
    name: str | None
    agent_type: str | None


class _ToolCallParams(_Params):
    callback_id: str
    name: str
    input: JsonValue
    tool_call_id: str
    caller: _ToolCaller | None


class _Reply(ProtocolModel):
    # A server may run ahead of this Vibe and add fields.
    model_config = ConfigDict(extra="ignore")


class _ErrorBody(_Reply):
    code: int
    message: str
    data: JsonValue = None

    @property
    def timeout_seconds(self) -> float | None:
        """The limit a -32001 error says the server applied, if it says."""
        match self.data:
            case {"timeoutSeconds": int() | float() as seconds} if (
                not isinstance(seconds, bool) and seconds > 0
            ):
                return float(seconds)
            case _:
                return None


class _Response(_Reply):
    jsonrpc: Literal["2.0"]
    id: int | str | None = None
    result: JsonValue = None
    error: _ErrorBody | None = None


class _Tool(_Reply):
    namespace: str = DEFAULT_NAMESPACE
    name: str
    description: str = ""
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] | None = None
    model_access: ClientToolModelAccess = "programmatic"

    def definition(self) -> ClientToolDefinition:
        return ClientToolDefinition(
            namespace=self.namespace,
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            model_access=self.model_access,
        )


class _InitializeResult(_Reply):
    protocol_version: int
    workspace: str | None = None
    python: str | None = None
    tools: list[_Tool] = Field(default_factory=list)
    tool_timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    instructions: str | None = None


class _ExecuteResult(_Reply):
    exit_code: int
    stdout: str
    stderr: str


class _ReadFileResult(_Reply):
    content_base64: str | None


class _ToolError(_Reply):
    message: str
    code: str | None = None
    details: JsonValue = None


class _ToolCallResult(_Reply):
    output: JsonValue = None
    annotations: dict[str, JsonValue] = Field(default_factory=dict)
    error: _ToolError | None = None


@dataclass(frozen=True, slots=True)
class SocketSandbox:
    """A Sandbox Adapter whose sandbox answers on the Unix socket ``path``."""

    path: Path
    workspace: str
    python: str
    on_abort: AbortHandler = _ignore_abort

    async def execute(
        self, command: str, cwd: str, timeout: float | None
    ) -> SandboxExecResult:
        try:
            reply = await _call(
                self.path,
                "sandbox/execute",
                _ExecuteParams(command=command, cwd=cwd, timeout=timeout),
                _ExecuteResult,
                timeout=None if timeout is None else timeout + EXECUTE_GRACE_SECONDS,
                on_abort=self.on_abort,
            )
        except _ServerTimeoutError as e:
            limit = "" if e.timeout is None else f" after {e.timeout:g}s"
            raise SandboxCommandTimeoutError(
                f"The agent server stopped the command{limit}: {e}", timeout=e.timeout
            ) from e
        return SandboxExecResult(
            exit_code=reply.exit_code, stdout=reply.stdout, stderr=reply.stderr
        )

    async def read_file(self, path: str, max_bytes: int) -> bytes | None:
        reply = await _call(
            self.path,
            "sandbox/readFile",
            _ReadFileParams(path=path, max_bytes=max_bytes),
            _ReadFileResult,
            timeout=REQUEST_TIMEOUT_SECONDS,
            on_abort=self.on_abort,
        )
        if reply.content_base64 is None:
            return None
        try:
            content = base64.b64decode(reply.content_base64, validate=True)
        except binascii.Error as e:
            raise _malformed(self.path, f"contentBase64 is not base64: {e}") from e
        return content[:max_bytes]


@dataclass(frozen=True, slots=True)
class AgentSocket:
    """The agent server on the Unix socket ``path``: its sandbox, if it serves
    one, and its tools.
    """

    path: Path
    sandbox: SocketSandbox | None
    tools: tuple[ClientToolDefinition, ...] = ()
    # The server's wait for a tool call, if it set one.
    tool_timeout: float | None = None
    # Project instructions for the session, if the server gave any.
    instructions: str | None = None
    on_abort: AbortHandler = _ignore_abort

    def client_tools(self, time_limit: float | None = None) -> ClientTools | None:
        """The server's tools for the session, or None when it serves none.

        A call waits for its answer as long as the server asked, by default
        the host's own wait for a client tool, and never past ``time_limit``,
        the run's.
        """
        if not self.tools:
            return None
        timeout = self.tool_timeout or CLIENT_TOOL_TIMEOUT_SECONDS
        if time_limit is not None:
            timeout = min(timeout, time_limit)
        return ClientTools(definitions=self.tools, runner=self, timeout=timeout)

    async def call_tool(self, call: ClientToolCall) -> ClientToolResult:
        """Run ``call`` on the server and return its answer.

        Raises what a sandbox request raises when the server does not answer
        with a result. The caller bounds the wait.
        """
        reply = await _call(
            self.path,
            "tools/call",
            _ToolCallParams(
                callback_id=call.call_id,
                name=call.name,
                input=call.input,
                tool_call_id=call.tool_call_id,
                caller=None
                if call.caller is None
                else _ToolCaller(
                    session_id=call.caller.session_id,
                    root_session_id=call.caller.root_session_id,
                    parent_session_id=call.caller.parent_session_id,
                    name=call.caller.name,
                    agent_type=call.caller.agent_type,
                ),
            ),
            _ToolCallResult,
            timeout=None,
            on_abort=self.on_abort,
        )
        error = reply.error
        return ClientToolResult(
            output=reply.output,
            annotations=reply.annotations,
            error=None
            if error is None
            else ClientToolError(
                message=error.message, code=error.code, details=error.details
            ),
        )


async def connect_agent_socket(
    path: Path, *, on_abort: AbortHandler = _ignore_abort
) -> AgentSocket:
    """Handshake with the agent server on ``path``.

    ``on_abort`` is called with the server's message whenever it aborts the
    run, before the request raises :class:`RunAbortedError`, the handshake
    included.

    Raises :class:`ConnectionError` when nothing answers,
    :class:`ClientToolDeclarationError` when the server declares a tool Vibe
    cannot offer, and :class:`SandboxUnavailableError` when the server refuses
    the handshake or answers with a reply this Vibe cannot use.
    """
    reply = await _call(
        path,
        "agent/initialize",
        _InitializeParams(protocol_version=PROTOCOL_VERSION, vibe_version=__version__),
        _InitializeResult,
        timeout=REQUEST_TIMEOUT_SECONDS,
        on_abort=on_abort,
    )
    if reply.protocol_version != PROTOCOL_VERSION:
        raise SandboxUnavailableError(
            f"The agent socket at {path} speaks protocol version "
            f"{reply.protocol_version}; this Vibe speaks {PROTOCOL_VERSION}"
        )
    tools = tuple(tool.definition() for tool in reply.tools)
    # Refuses tools Vibe cannot offer before the run, not once it starts.
    check_client_tools(tools)
    return AgentSocket(
        path=path,
        sandbox=_sandbox(path, reply, on_abort),
        tools=tools,
        tool_timeout=reply.tool_timeout_seconds,
        instructions=reply.instructions
        if reply.instructions and reply.instructions.strip()
        else None,
        on_abort=on_abort,
    )


def _sandbox(
    path: Path, reply: _InitializeResult, on_abort: AbortHandler
) -> SocketSandbox | None:
    if reply.workspace is None and reply.python is None:
        return None
    if reply.workspace is None or reply.python is None:
        raise _malformed(path, "workspace and python come together")
    if not PurePosixPath(reply.workspace).is_absolute():
        raise SandboxUnavailableError(
            f"The sandbox workspace {reply.workspace!r} is not an absolute path"
        )
    return SocketSandbox(
        path=path, workspace=reply.workspace, python=reply.python, on_abort=on_abort
    )


async def _call[ResultT: _Reply](
    path: Path,
    method: str,
    params: _Params,
    result_type: type[ResultT],
    *,
    timeout: float | None,
    on_abort: AbortHandler,
) -> ResultT:
    request_id = next(_REQUEST_IDS)
    line = _encode({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params.model_dump(mode="json"),
    })
    try:
        async with asyncio.timeout(timeout):
            raw = await _exchange(path, line)
    except TimeoutError as e:
        raise TimeoutError(
            f"Vibe stopped waiting for the agent server's reply to {method} "
            f"after {timeout:g}s"
        ) from e
    try:
        response = validate_wire(_Response, json.loads(raw))
    except (ValueError, ValidationError) as e:
        raise _malformed(path, str(e)) from e
    if response.error is not None:
        if response.error.code == ABORT_ERROR_CODE:
            if (notified := on_abort(response.error.message)) is not None:
                await notified
            raise RunAbortedError(response.error.message)
        if response.error.code == TIMEOUT_ERROR_CODE:
            raise _ServerTimeoutError(
                response.error.message, timeout=response.error.timeout_seconds
            )
        raise SandboxUnavailableError(response.error.message)
    if response.id != request_id or response.result is None:
        raise _malformed(path, f"no result for request {request_id}")
    try:
        return validate_wire(result_type, response.result)
    except ValidationError as e:
        raise _malformed(path, str(e)) from e


async def _exchange(path: Path, line: bytes) -> bytes:
    """Send one request line and read one reply line on a new connection."""
    try:
        reader, writer = await asyncio.open_unix_connection(
            path, limit=REPLY_LIMIT_BYTES
        )
    except OSError as e:
        raise ConnectionError(
            f"Cannot connect to the agent socket at {path}: {e}"
        ) from e
    try:
        writer.write(line)
        await writer.drain()
        reply = await reader.readline()
    except ValueError as e:
        # The reply line outgrew the reader's limit.
        raise _malformed(path, str(e)) from e
    except OSError as e:
        raise ConnectionError(f"Lost the agent socket at {path}: {e}") from e
    finally:
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()
    if not reply.endswith(b"\n"):
        raise ConnectionError(
            f"The agent socket at {path} closed the connection before replying"
        )
    return reply


def _encode(message: dict[str, JsonValue]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def _malformed(path: Path, detail: str) -> SandboxUnavailableError:
    return SandboxUnavailableError(
        f"The agent socket at {path} sent a malformed reply: {detail}"
    )


ABORTED_NOTIFICATION = "agentSocket/aborted"
OUTCOME_ERROR_FIELD = "agentSocketOutcome"
"""The field of a session open error that names the run's outcome."""


class AgentSocketAbortedParams(ProtocolModel):
    message: str


def session_open_error(outcome: RunOutcome, message: str) -> SessionBackendError:
    """The error of a session open the agent socket failed, naming the outcome
    the run ends with: an abort reaches no client as a notification before
    the session is attached.
    """
    code = (
        ProtocolErrorCode.INVALID_PARAMS
        if outcome is RunOutcome.USAGE_ERROR
        else ProtocolErrorCode.INTERNAL_ERROR
    )
    return SessionBackendError(code, message, {OUTCOME_ERROR_FIELD: outcome.value})


class AgentSocketMount:
    """The agent server ``vibe-app-server --agent-socket`` mounts into its
    sessions, or why the handshake failed, which then fails every session open.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.agent_socket: AgentSocket | None = None
        self._failure: tuple[RunOutcome, str] | None = None
        self._notifiers: list[Notifier] = []

    @classmethod
    async def connect(cls, path: Path) -> AgentSocketMount:
        from vibe.app_server._sandbox_workspace import warn_of_ignored_project_sources

        mount = cls(path)
        try:
            agent_socket = await connect_agent_socket(path, on_abort=mount._abort)
            if agent_socket.sandbox is not None:
                await warn_of_ignored_project_sources(agent_socket.sandbox)
        except RunAbortedError as e:
            mount._fail(RunOutcome.ABORTED, str(e))
        except ClientToolDeclarationError as e:
            mount._fail(
                RunOutcome.USAGE_ERROR,
                f"The agent socket at {path} declares a tool Vibe cannot offer: {e}",
            )
        except SANDBOX_FAILURES as e:
            mount._fail(
                RunOutcome.INFRASTRUCTURE_FAILURE,
                f"Cannot open the agent socket at {path}: {e}",
            )
        else:
            mount.agent_socket = agent_socket
        return mount

    @property
    def sandbox(self) -> SocketSandbox | None:
        return None if self.agent_socket is None else self.agent_socket.sandbox

    def check(self) -> None:
        """Raise why the handshake failed, or that the server aborted the run."""
        if self._failure is not None:
            raise session_open_error(*self._failure)

    def pin_session_options(self, options: SessionOptions) -> SessionOptions:
        """A sandboxed session starts in the sandbox's workspace: the cwd a
        client sends names a directory of this host, and so does its trust.
        """
        if (sandbox := self.sandbox) is None:
            return options
        return options.model_copy(
            update={"cwd": sandbox.workspace, "workdir": None, "trust_workspace": False}
        )

    def require_sandbox(self, why: str) -> None:
        """Fail every session open as a usage error when the server serves no
        sandbox; ``why`` says what needs one.
        """
        if self.agent_socket is not None and self.agent_socket.sandbox is None:
            self._fail(
                RunOutcome.USAGE_ERROR,
                f"The agent socket at {self.path} serves no sandbox; {why}",
            )

    def subscribe(self, notify: Notifier) -> None:
        self._notifiers.append(notify)

    def close(self) -> None:
        """Stop notifying: the app servers subscribed are done with the mount."""
        self._notifiers.clear()

    async def _abort(self, message: str) -> None:
        # Kept, so an abort a caller swallows still fails the next session open.
        if self._failure is None:
            self._fail(RunOutcome.ABORTED, message)
        params = AgentSocketAbortedParams(message=message)
        for notify in self._notifiers:
            await notify(ABORTED_NOTIFICATION, params)

    def _fail(self, outcome: RunOutcome, message: str) -> None:
        self._failure = (outcome, message)


__all__ = [
    "ABORTED_NOTIFICATION",
    "ABORT_ERROR_CODE",
    "EXECUTE_GRACE_SECONDS",
    "OUTCOME_ERROR_FIELD",
    "PROTOCOL_VERSION",
    "TIMEOUT_ERROR_CODE",
    "AbortHandler",
    "AgentSocket",
    "AgentSocketAbortedParams",
    "AgentSocketMount",
    "RunAbortedError",
    "SocketSandbox",
    "connect_agent_socket",
    "session_open_error",
]
