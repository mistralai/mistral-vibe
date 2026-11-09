from __future__ import annotations

import asyncio
from collections.abc import Iterator
import json
from pathlib import Path
import time

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.vibe import (
    SandboxCommandTimeoutError,
    SandboxUnavailableError,
)
from tests.stubs.app_server import FakeSessionBackendServices
from tests.stubs.fake_agent_server import (
    ABORT_ERROR_CODE,
    TIMEOUT_ERROR_CODE,
    AbortRun,
    FakeAgentServer,
    Reply,
    Request,
    encode_line,
    error_line,
    private_socket_path,
)
from vibe import __version__
from vibe.app_server import _agent_socket as agent_socket
from vibe.app_server._agent_socket import (
    ABORTED_NOTIFICATION,
    OUTCOME_ERROR_FIELD,
    AgentSocketAbortedParams,
    AgentSocketMount,
    RunAbortedError,
    SocketSandbox,
    connect_agent_socket,
)
from vibe.app_server._client_provided_tools import (
    CLIENT_TOOL_TIMEOUT_SECONDS,
    ClientToolCall,
    ClientToolCaller,
    ClientToolDeclarationError,
    ClientToolDefinition,
    ClientToolError,
    ClientToolResult,
)
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.protocol import ProtocolErrorCode, SessionOptions
from vibe.app_server.run_export import RunOutcome


@pytest.fixture
def socket_path() -> Iterator[Path]:
    with private_socket_path() as path:
        yield path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "sandbox-workspace"
    path.mkdir()
    return path.resolve()


def _sandbox(socket_path: Path) -> SocketSandbox:
    return SocketSandbox(path=socket_path, workspace="/workspace", python="python3")


def _result_line(request: Request, result: JsonValue) -> bytes:
    return encode_line({"jsonrpc": "2.0", "id": request["id"], "result": result})


async def _connect_sandbox(socket_path: Path) -> SocketSandbox:
    agent = await connect_agent_socket(socket_path)
    assert agent.sandbox is not None
    return agent.sandbox


_LOOKUP: dict[str, JsonValue] = {
    "namespace": "kb",
    "name": "lookup",
    "description": "Look a term up.",
    "inputSchema": {"type": "object", "properties": {"term": {"type": "string"}}},
    "modelAccess": "both",
}


@pytest.mark.asyncio
async def test_connecting_shakes_hands_and_takes_the_sandboxs_workspace(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: An agent server over a workspace directory.
    *Do*: Connect to it.
    *Assert*: The handshake carries the protocol and Vibe versions, and the
    adapter takes the server's workspace and interpreter.
    """
    # Prepare
    async with FakeAgentServer(socket_path, workspace) as server:
        # Do
        agent = await connect_agent_socket(socket_path)

    # Assert
    assert server.requests == [
        {
            "jsonrpc": "2.0",
            "id": server.requests[0]["id"],
            "method": "agent/initialize",
            "params": {"protocolVersion": 2, "vibeVersion": __version__},
        }
    ]
    assert agent.sandbox is not None and server.sandbox is not None
    assert agent.sandbox.workspace == str(workspace)
    assert agent.sandbox.python == server.sandbox.python
    assert agent.tools == ()


@pytest.mark.asyncio
async def test_the_handshake_lists_the_servers_tools_without_a_sandbox(
    socket_path: Path,
) -> None:
    """*Prepare*: An agent server with a tool and no workspace.
    *Do*: Connect to it.
    *Assert*: There is no sandbox, and the tool is listed with its namespace,
    schema and model access, and the defaults for what the server left out.
    """
    # Prepare
    bare: dict[str, JsonValue] = {"name": "ping"}
    async with FakeAgentServer(socket_path, None, tools=[_LOOKUP, bare]):
        # Do
        agent = await connect_agent_socket(socket_path)

    # Assert
    assert agent.sandbox is None
    lookup, ping = agent.tools
    assert lookup == ClientToolDefinition(
        namespace="kb",
        name="lookup",
        description="Look a term up.",
        input_schema={"type": "object", "properties": {"term": {"type": "string"}}},
        model_access="both",
    )
    assert ping == ClientToolDefinition(name="ping")
    assert ping.namespace == "client"
    assert ping.model_access == "programmatic"


@pytest.mark.asyncio
async def test_a_tool_call_carries_the_callback_and_returns_the_servers_answer(
    socket_path: Path,
) -> None:
    """*Prepare*: An agent server whose tool answers with an output and
    annotations, then with an error.
    *Do*: Call it twice.
    *Assert*: Each request carries the call's id, name, input and tool call
    id, and each answer comes back as the call's result.
    """
    # Prepare
    answers: list[dict[str, JsonValue]] = [
        {"output": {"hits": 1}, "annotations": {"cost": 2}},
        {"error": {"message": "no such term", "code": "not_found"}},
    ]

    async def answer(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return answers.pop(0)

    call = ClientToolCall(
        call_id="client_tool:1",
        name="kb.lookup",
        input={"term": "harness"},
        tool_call_id="call-1",
    )
    async with FakeAgentServer(
        socket_path, None, tools=[_LOOKUP], on_tool_call=answer
    ) as server:
        agent = await connect_agent_socket(socket_path)

        # Do
        found = await agent.call_tool(call)
        failed = await agent.call_tool(call)

    # Assert
    assert server.requests[1]["method"] == "tools/call"
    assert server.requests[1]["params"] == {
        "callbackId": "client_tool:1",
        "name": "kb.lookup",
        "input": {"term": "harness"},
        "toolCallId": "call-1",
        "caller": None,
    }
    assert found == ClientToolResult(output={"hits": 1}, annotations={"cost": 2})
    assert failed == ClientToolResult(
        error=ClientToolError(message="no such term", code="not_found")
    )


@pytest.mark.asyncio
async def test_a_subagents_tool_call_names_it_as_the_caller(socket_path: Path) -> None:
    """*Prepare*: An agent server with a tool.
    *Do*: Call it as a subagent.
    *Assert*: The request names the subagent, its parent, its root and its
    agent.
    """

    # Prepare
    async def answer(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {"output": "ok"}

    call = ClientToolCall(
        call_id="client_tool:1",
        name="kb.lookup",
        input={},
        tool_call_id="call-1",
        caller=ClientToolCaller(
            session_id="child",
            root_session_id="root",
            parent_session_id="root",
            name="reader",
            agent_type="explore",
        ),
    )
    async with FakeAgentServer(
        socket_path, None, tools=[_LOOKUP], on_tool_call=answer
    ) as server:
        agent = await connect_agent_socket(socket_path)

        # Do
        await agent.call_tool(call)

    # Assert
    params = server.requests[1]["params"]
    assert isinstance(params, dict)
    assert params["caller"] == {
        "sessionId": "child",
        "rootSessionId": "root",
        "parentSessionId": "root",
        "name": "reader",
        "agentType": "explore",
    }


@pytest.mark.asyncio
async def test_execute_runs_the_command_in_the_sandbox(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A connected sandbox.
    *Do*: Run a command that prints its directory, writes to stderr and fails.
    *Assert*: The non-zero exit is a result, run in the requested directory, and
    the request carries the timeout.
    """
    # Prepare
    async with FakeAgentServer(socket_path, workspace) as server:
        sandbox = await _connect_sandbox(socket_path)

        # Do
        result = await sandbox.execute("pwd; echo oops >&2; exit 3", str(workspace), 5)

    # Assert
    assert result.exit_code == 3
    assert result.stdout.strip() == str(workspace)
    assert result.stderr == "oops\n"
    assert server.requests[-1]["params"] == {
        "command": "pwd; echo oops >&2; exit 3",
        "cwd": str(workspace),
        "timeout": 5,
    }


@pytest.mark.asyncio
async def test_execute_carries_a_reply_larger_than_a_default_stream_buffer(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A connected sandbox.
    *Do*: Run a command that prints a megabyte, with no timeout.
    *Assert*: The whole output arrives.
    """
    # Prepare
    async with FakeAgentServer(socket_path, workspace) as server:
        sandbox = await _connect_sandbox(socket_path)

        # Do
        result = await sandbox.execute(
            "head -c 1000000 /dev/zero | tr '\\0' a", str(workspace), None
        )

    # Assert
    assert result.stdout == "a" * 1_000_000
    params = server.requests[-1]["params"]
    assert isinstance(params, dict) and params["timeout"] is None


@pytest.mark.asyncio
async def test_read_file_returns_the_bytes_or_none_for_no_such_file(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A sandbox workspace with a binary file.
    *Do*: Read it whole, read its first bytes, and read a missing file.
    *Assert*: The bytes arrive as they are, cut at the limit, and a missing
    file is None.
    """
    # Prepare
    content = bytes(range(256))
    (workspace / "data.bin").write_bytes(content)
    async with FakeAgentServer(socket_path, workspace) as server:
        sandbox = await _connect_sandbox(socket_path)

        # Do
        whole = await sandbox.read_file(str(workspace / "data.bin"), 1024)
        head = await sandbox.read_file(str(workspace / "data.bin"), 10)
        missing = await sandbox.read_file(str(workspace / "missing.txt"), 1024)

    # Assert
    assert whole == content
    assert head == content[:10]
    assert missing is None
    assert server.requests[-1]["params"] == {
        "path": str(workspace / "missing.txt"),
        "maxBytes": 1024,
    }


@pytest.mark.asyncio
async def test_concurrent_commands_run_at_the_same_time(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A connected sandbox.
    *Do*: Run four slow commands concurrently.
    *Assert*: Each gets its own answer, and the server had all four at once.
    """
    # Prepare
    async with FakeAgentServer(socket_path, workspace) as server:
        sandbox = await _connect_sandbox(socket_path)
        started = time.monotonic()

        # Do
        results = await asyncio.gather(
            *(
                sandbox.execute(f"sleep 0.5; echo {index}", str(workspace), 5)
                for index in range(4)
            )
        )

    # Assert
    assert [result.stdout.strip() for result in results] == ["0", "1", "2", "3"]
    assert server.peak_active_requests == 4
    assert time.monotonic() - started < 1.9


@pytest.mark.asyncio
async def test_no_server_is_a_connection_error(socket_path: Path) -> None:
    """*Prepare*: A socket path nothing listens on.
    *Do*: Run a command.
    *Assert*: It raises ConnectionError.
    """
    with pytest.raises(ConnectionError, match=str(socket_path)):
        await _sandbox(socket_path).execute("true", "/workspace", 1)


async def _close_without_answer(_request: Request) -> bytes | None:
    return None


async def _half_a_line(_request: Request) -> bytes:
    return b'{"jsonrpc":"2.0"'


async def _timed_out(request: Request) -> bytes:
    return error_line(request, TIMEOUT_ERROR_CODE, "the command timed out")


async def _timed_out_at_2s(request: Request) -> bytes:
    return error_line(
        request, TIMEOUT_ERROR_CODE, "the command timed out", data={"timeoutSeconds": 2}
    )


async def _failed(request: Request) -> bytes:
    return error_line(request, -32000, "the container is gone")


async def _not_json(_request: Request) -> bytes:
    return b"not json\n"


async def _wrong_shape(request: Request) -> bytes:
    return _result_line(request, {"exitCode": "zero"})


async def _wrong_id(request: Request) -> bytes:
    return encode_line({
        "jsonrpc": "2.0",
        "id": "another",
        "result": {"exitCode": 0, "stdout": "", "stderr": ""},
    })


async def _neither_result_nor_error(request: Request) -> bytes:
    return encode_line({"jsonrpc": "2.0", "id": request["id"]})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "error", "message"),
    [
        (_close_without_answer, ConnectionError, "closed the connection"),
        (_half_a_line, ConnectionError, "closed the connection"),
        (_failed, SandboxUnavailableError, "the container is gone"),
        (_not_json, SandboxUnavailableError, "malformed"),
        (_wrong_shape, SandboxUnavailableError, "malformed"),
        (_wrong_id, SandboxUnavailableError, "malformed"),
        (_neither_result_nor_error, SandboxUnavailableError, "malformed"),
    ],
    ids=[
        "eof",
        "eof-mid-line",
        "error",
        "not-json",
        "wrong-shape",
        "wrong-id",
        "empty",
    ],
)
async def test_a_failed_execute_raises_what_the_session_reports_as_a_sandbox_failure(
    socket_path: Path,
    workspace: Path,
    reply: Reply,
    error: type[Exception],
    message: str,
) -> None:
    """*Prepare*: A server that answers with a failure.
    *Do*: Run a command.
    *Assert*: The failure maps to ConnectionError or SandboxUnavailableError,
    with the server's message when it gave one.
    """
    # Prepare
    async with FakeAgentServer(socket_path, workspace, reply=reply):
        # Do
        with pytest.raises(error, match=message):
            await _sandbox(socket_path).execute("true", "/workspace", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "message", "applied"),
    [
        (
            _timed_out,
            "The agent server stopped the command: the command timed out",
            None,
        ),
        (
            _timed_out_at_2s,
            "The agent server stopped the command after 2s: the command timed out",
            2.0,
        ),
    ],
)
async def test_a_command_the_server_times_out_is_a_command_timeout(
    socket_path: Path,
    workspace: Path,
    reply: Reply,
    message: str,
    applied: float | None,
) -> None:
    """*Prepare*: A server that answers a command with the timeout error,
    with or without the limit it applied.
    *Do*: Run a command with a 5s timeout.
    *Assert*: It raises SandboxCommandTimeoutError with the server's message,
    and the limit the server applied when it said, never the 5s asked for.
    """
    # Prepare
    async with FakeAgentServer(socket_path, workspace, reply=reply):
        # Do
        with pytest.raises(SandboxCommandTimeoutError) as raised:
            await _sandbox(socket_path).execute("true", "/workspace", 5)

    # Assert
    assert str(raised.value) == message
    assert raised.value.timeout == applied


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data", [{"timeoutSeconds": 0}, {"timeoutSeconds": True}, {"other": 2}, "2"]
)
async def test_a_timeout_error_with_an_unusable_limit_names_none(
    socket_path: Path, workspace: Path, data: JsonValue
) -> None:
    """*Prepare*: A server whose timeout error carries data with no positive
    ``timeoutSeconds``.
    *Do*: Run a command.
    *Assert*: It is still a command timeout, naming no limit.
    """

    # Prepare
    async def timed_out(request: Request) -> bytes:
        return error_line(request, TIMEOUT_ERROR_CODE, "timed out", data=data)

    async with FakeAgentServer(socket_path, workspace, reply=timed_out):
        # Do
        with pytest.raises(SandboxCommandTimeoutError) as raised:
            await _sandbox(socket_path).execute("true", "/workspace", 5)

    # Assert
    assert raised.value.timeout is None


@pytest.mark.asyncio
async def test_a_read_file_reply_that_is_not_base64_is_malformed(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A server that answers a read with content that is not base64.
    *Do*: Read a file.
    *Assert*: It raises SandboxUnavailableError.
    """

    async def not_base64(request: Request) -> bytes:
        return _result_line(request, {"contentBase64": "%%%"})

    async with FakeAgentServer(socket_path, workspace, reply=not_base64):
        with pytest.raises(SandboxUnavailableError, match="malformed"):
            await _sandbox(socket_path).read_file("/workspace/a.txt", 10)


@pytest.mark.asyncio
async def test_a_server_that_outlives_the_timeout_is_a_timeout(
    socket_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A server that never answers, and a short grace period.
    *Do*: Run a command with a timeout.
    *Assert*: It raises a TimeoutError of Vibe's own once the timeout and the
    grace have passed, not a command timeout.
    """
    # Prepare
    monkeypatch.setattr(agent_socket, "EXECUTE_GRACE_SECONDS", 0.1)

    async def never(_request: Request) -> bytes:
        await asyncio.Event().wait()
        raise AssertionError

    async with FakeAgentServer(socket_path, workspace, reply=never):
        # Do / Assert
        with pytest.raises(TimeoutError) as raised:
            await _sandbox(socket_path).execute("true", "/workspace", 0.1)
    assert not isinstance(raised.value, SandboxCommandTimeoutError)
    assert str(raised.value) == (
        "Vibe stopped waiting for the agent server's reply to sandbox/execute "
        "after 0.2s"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "message"),
    [
        (
            {"protocolVersion": 1, "workspace": "/w", "python": "python3"},
            "protocol version 1",
        ),
        (
            {"protocolVersion": 2, "workspace": "w", "python": "python3"},
            "not an absolute path",
        ),
        ({"protocolVersion": 2, "workspace": "/w"}, "malformed"),
        ({"workspace": "/w", "python": "python3"}, "malformed"),
        ({"protocolVersion": 2, "tools": [{"description": "no name"}]}, "malformed"),
        ({"protocolVersion": 2, "toolTimeoutSeconds": 0}, "malformed"),
        ({"protocolVersion": 2, "toolTimeoutSeconds": -1}, "malformed"),
        ({"protocolVersion": 2, "toolTimeoutSeconds": "soon"}, "malformed"),
    ],
    ids=[
        "other-version",
        "relative-workspace",
        "workspace-without-python",
        "no-version",
        "nameless-tool",
        "zero-tool-timeout",
        "negative-tool-timeout",
        "tool-timeout-not-a-number",
    ],
)
async def test_a_handshake_the_adapter_cannot_use_fails_the_connection(
    socket_path: Path, workspace: Path, result: dict[str, JsonValue], message: str
) -> None:
    """*Prepare*: A server whose handshake reply cannot be used.
    *Do*: Connect.
    *Assert*: It raises SandboxUnavailableError, saying why.
    """

    async def handshake(request: Request) -> bytes:
        return _result_line(request, result)

    async with FakeAgentServer(socket_path, workspace, reply=handshake):
        with pytest.raises(SandboxUnavailableError, match=message):
            await connect_agent_socket(socket_path)


@pytest.mark.asyncio
async def test_a_tool_vibe_cannot_offer_fails_the_handshake(socket_path: Path) -> None:
    """*Prepare*: A server that declares a tool in Vibe's own namespace.
    *Do*: Connect.
    *Assert*: It raises ClientToolDeclarationError, saying why.
    """
    tool: dict[str, JsonValue] = {"namespace": "vibe", "name": "x"}
    async with FakeAgentServer(socket_path, None, tools=[tool]):
        with pytest.raises(ClientToolDeclarationError, match="Vibe's own"):
            await connect_agent_socket(socket_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_timeout", "time_limit", "timeout"),
    [
        (None, None, CLIENT_TOOL_TIMEOUT_SECONDS),
        (1200, None, 1200.0),
        (5.5, None, 5.5),
        (1200, 30.0, 30.0),
    ],
    ids=["default", "longer", "fractional", "time-limit-caps-it"],
)
async def test_the_handshake_sets_how_long_a_tool_call_waits(
    socket_path: Path,
    tool_timeout: float | None,
    time_limit: float | None,
    timeout: float,
) -> None:
    """*Prepare*: A server with a tool, which sets `toolTimeoutSeconds` or not.
    *Do*: Connect, and take its tools for a run with or without a time limit.
    *Assert*: A call waits as long as the server asked, by default the host's
    own wait, and never past the run's time limit.
    """

    async def handshake(request: Request) -> bytes:
        result: dict[str, JsonValue] = {"protocolVersion": 2, "tools": [_LOOKUP]}
        if tool_timeout is not None:
            result["toolTimeoutSeconds"] = tool_timeout
        return _result_line(request, result)

    async with FakeAgentServer(socket_path, None, reply=handshake):
        agent = await connect_agent_socket(socket_path)

    client_tools = agent.client_tools(time_limit)
    assert client_tools is not None
    assert client_tools.timeout == timeout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("given", "instructions"),
    [
        (None, None),
        ("Run `make check` first.\n", "Run `make check` first.\n"),
        (" \n", None),
    ],
    ids=["none", "given", "blank"],
)
async def test_the_handshake_gives_project_instructions(
    socket_path: Path, given: str | None, instructions: str | None
) -> None:
    """*Prepare*: A server whose handshake gives `instructions` or not.
    *Do*: Connect.
    *Assert*: The connection carries them as given; blank ones are none.
    """
    server = FakeAgentServer(socket_path, None, instructions=given)

    async with server:
        agent = await connect_agent_socket(socket_path)

    assert agent.instructions == instructions


async def _abort(request: Request) -> bytes:
    return error_line(request, ABORT_ERROR_CODE, "The task's budget is gone")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_kind", ["handshake", "execute", "read-file", "tool-call"]
)
async def test_the_abort_code_on_any_reply_aborts_the_run(
    socket_path: Path, workspace: Path, request_kind: str
) -> None:
    """*Prepare*: A server that answers one kind of request with the abort
    code and a message, and an abort handler that records what it is given.
    *Do*: Send that request.
    *Assert*: The handler gets the server's message, and the request raises
    RunAbortedError with it, which a sandbox failure handler also catches.
    """
    # Prepare
    aborts: list[str] = []
    method = {
        "handshake": "agent/initialize",
        "execute": "sandbox/execute",
        "read-file": "sandbox/readFile",
        "tool-call": "tools/call",
    }[request_kind]
    server = FakeAgentServer(
        socket_path,
        workspace,
        tools=[_LOOKUP],
        aborts={method: "The task's budget is gone"},
    )
    call = ClientToolCall(
        call_id="client_tool:1", name="kb.lookup", input={}, tool_call_id="call-1"
    )

    async with server:
        # Do
        with pytest.raises(RunAbortedError) as exc_info:
            agent = await connect_agent_socket(socket_path, on_abort=aborts.append)
            assert agent.sandbox is not None
            match request_kind:
                case "execute":
                    await agent.sandbox.execute("true", str(workspace), 1)
                case "read-file":
                    await agent.sandbox.read_file(str(workspace / "a"), 10)
                case _:
                    await agent.call_tool(call)

    # Assert
    assert aborts == ["The task's budget is gone"]
    assert str(exc_info.value) == "The task's budget is gone"
    assert isinstance(exc_info.value, SandboxUnavailableError)


@pytest.mark.asyncio
async def test_a_tool_handler_can_abort_the_run(socket_path: Path) -> None:
    """*Prepare*: A server whose tool handler aborts the run.
    *Do*: Call the tool.
    *Assert*: The call raises RunAbortedError with the handler's message.
    """

    async def abort(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        raise AbortRun("Stop now")

    aborts: list[str] = []
    async with FakeAgentServer(socket_path, None, tools=[_LOOKUP], on_tool_call=abort):
        agent = await connect_agent_socket(socket_path, on_abort=aborts.append)
        with pytest.raises(RunAbortedError, match="Stop now"):
            await agent.call_tool(
                ClientToolCall(
                    call_id="client_tool:1",
                    name="kb.lookup",
                    input={},
                    tool_call_id="call-1",
                )
            )

    assert aborts == ["Stop now"]


@pytest.mark.asyncio
async def test_a_reply_may_carry_fields_this_vibe_does_not_know(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A server that adds fields to its replies.
    *Do*: Connect and run a command.
    *Assert*: Both succeed.
    """

    async def chatty(request: Request) -> bytes:
        result: dict[str, JsonValue] = (
            {"protocolVersion": 2, "workspace": "/w", "python": "python3"}
            if request["method"] == "agent/initialize"
            else {"exitCode": 0, "stdout": "ok", "stderr": "", "durationMs": 3}
        )
        return encode_line({
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {**result, "server": "next"},
            "extra": True,
        })

    async with FakeAgentServer(socket_path, workspace, reply=chatty):
        sandbox = await _connect_sandbox(socket_path)
        result = await sandbox.execute("true", "/w", 1)

    assert result.stdout == "ok"


def test_the_socket_request_is_one_json_line(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A raw server that records the bytes it receives.
    *Do*: Connect.
    *Assert*: The request is one compact JSON-RPC 2.0 line.
    """
    received: list[bytes] = []

    async def scenario() -> None:
        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            line = await reader.readline()
            received.append(line)
            request = json.loads(line)
            writer.write(
                _result_line(
                    request,
                    {"protocolVersion": 2, "workspace": "/w", "python": "python3"},
                )
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=str(socket_path))
        async with server:
            await connect_agent_socket(socket_path)

    asyncio.run(scenario())

    [line] = received
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert b" " not in line.replace(__version__.encode(), b"")
    assert json.loads(line)["jsonrpc"] == "2.0"


# --- The mount of `vibe-app-server --agent-socket` ----------------------------


@pytest.mark.asyncio
async def test_a_mount_serves_the_sandbox_tools_and_instructions(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A server with a sandbox, a tool and instructions.
    *Do*: Mount it.
    *Assert*: The mount holds all three and fails no session open.
    """
    server = FakeAgentServer(
        socket_path, workspace, tools=[_LOOKUP], instructions="Be brief."
    )

    async with server:
        mount = await AgentSocketMount.connect(socket_path)

    mount.check()
    assert mount.sandbox is not None
    assert mount.sandbox.workspace == str(workspace)
    assert mount.agent_socket is not None
    assert [tool.name for tool in mount.agent_socket.tools] == ["lookup"]
    assert mount.agent_socket.instructions == "Be brief."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("server_kind", "code", "outcome", "message"),
    [
        (
            "none",
            ProtocolErrorCode.INTERNAL_ERROR,
            RunOutcome.INFRASTRUCTURE_FAILURE,
            "Cannot open the agent socket at",
        ),
        (
            "abort",
            ProtocolErrorCode.INTERNAL_ERROR,
            RunOutcome.ABORTED,
            "The task's budget is gone",
        ),
        (
            "bad-tool",
            ProtocolErrorCode.INVALID_PARAMS,
            RunOutcome.USAGE_ERROR,
            "declares a tool Vibe cannot offer",
        ),
    ],
)
async def test_a_failed_handshake_fails_every_session_open_with_its_outcome(
    socket_path: Path,
    workspace: Path,
    server_kind: str,
    code: ProtocolErrorCode,
    outcome: RunOutcome,
    message: str,
) -> None:
    """*Prepare*: No server, a server that aborts the handshake, or one that
    declares a tool in a namespace of Vibe's own.
    *Do*: Mount it, then check it as a session open does.
    *Assert*: The check raises the error the run reports, its data naming the
    run's outcome.
    """
    # Prepare
    reserved = {**_LOOKUP, "namespace": "vibe"}
    servers = {
        "abort": FakeAgentServer(
            socket_path, workspace, aborts={"agent/initialize": message}
        ),
        "bad-tool": FakeAgentServer(socket_path, None, tools=[reserved]),
    }

    # Do
    if (server := servers.get(server_kind)) is None:
        mount = await AgentSocketMount.connect(socket_path)
    else:
        async with server:
            mount = await AgentSocketMount.connect(socket_path)

    # Assert
    assert mount.agent_socket is None
    with pytest.raises(SessionBackendError) as exc_info:
        mount.check()
    assert exc_info.value.code is code
    assert message in str(exc_info.value)
    assert exc_info.value.data == {OUTCOME_ERROR_FIELD: outcome.value}


@pytest.mark.asyncio
async def test_an_abort_during_the_run_is_notified_before_the_request_fails(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A mounted server that aborts the run on its next command,
    and an app server subscribed to the mount.
    *Do*: Run a command in the sandbox.
    *Assert*: The app server was told of the abort, with the server's
    message, by the time the command raises.
    """
    # Prepare
    services = FakeSessionBackendServices()
    server = FakeAgentServer(
        socket_path, workspace, aborts={"sandbox/execute": "Out of budget"}
    )

    async with server:
        mount = await AgentSocketMount.connect(socket_path)
        mount.subscribe(services.notify)
        assert mount.sandbox is not None

        # Do
        with pytest.raises(RunAbortedError):
            await mount.sandbox.execute("true", str(workspace), 1)

        # Assert
        assert services.notifications == [
            (ABORTED_NOTIFICATION, AgentSocketAbortedParams(message="Out of budget"))
        ]
        with pytest.raises(SessionBackendError) as exc_info:
            mount.check()
        assert exc_info.value.data == {OUTCOME_ERROR_FIELD: "aborted"}


@pytest.mark.asyncio
async def test_an_abort_the_handshake_swallows_still_fails_the_session_open(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A server that aborts the run on the command the handshake
    runs after `agent/initialize`, to look for project files it ignores.
    *Do*: Mount it, then check it as a session open does.
    *Assert*: The open fails as aborted, with the server's message.
    """
    server = FakeAgentServer(
        socket_path, workspace, aborts={"sandbox/execute": "Out of budget"}
    )

    async with server:
        mount = await AgentSocketMount.connect(socket_path)

    assert server.methods == ["agent/initialize", "sandbox/execute"]
    with pytest.raises(SessionBackendError, match="Out of budget") as exc_info:
        mount.check()
    assert exc_info.value.data == {OUTCOME_ERROR_FIELD: "aborted"}


@pytest.mark.asyncio
async def test_a_sandboxed_session_is_pinned_to_the_workspace(
    socket_path: Path, workspace: Path
) -> None:
    """*Prepare*: A mounted sandbox, and session options naming a host
    directory, trusted.
    *Do*: Pin them.
    *Assert*: The session starts in the sandbox's workspace, untrusted.
    """
    async with FakeAgentServer(socket_path, workspace):
        mount = await AgentSocketMount.connect(socket_path)

    pinned = mount.pin_session_options(
        SessionOptions(cwd="/host/project", trust_workspace=True, headless=True)
    )

    assert pinned.cwd == str(workspace)
    assert pinned.trust_workspace is False
    assert pinned.headless is True


@pytest.mark.asyncio
async def test_a_tools_only_session_keeps_the_clients_directory(
    socket_path: Path,
) -> None:
    """*Prepare*: A mounted server with tools and no sandbox.
    *Do*: Pin session options naming a trusted host directory.
    *Assert*: They are unchanged: the session runs on this host.
    """
    async with FakeAgentServer(socket_path, None, tools=[_LOOKUP]):
        mount = await AgentSocketMount.connect(socket_path)
    options = SessionOptions(cwd="/host/project", trust_workspace=True)

    assert mount.pin_session_options(options) == options
