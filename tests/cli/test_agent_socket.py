from __future__ import annotations

import argparse
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
from tests.cli.test_programmatic_setup import _make_args, _run_cli
from tests.conftest import OrchestratorLoader, build_test_vibe_config
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
    serving_in_background,
)
from vibe import __version__
from vibe.app_server._client_provided_tools import (
    CLIENT_TOOL_TIMEOUT_SECONDS,
    ClientToolCall,
    ClientToolDeclarationError,
    ClientToolDefinition,
    ClientToolError,
    ClientToolResult,
)
from vibe.app_server.local import LocalHarnessOptions
from vibe.app_server.run_export import EXPORT_FILENAME, RunExport, RunOutcome, RunResult
from vibe.cli import (
    agent_socket,
    cli as cli_mod,
    entrypoint as entrypoint_mod,
    programmatic as programmatic_mod,
)
from vibe.cli.agent_socket import (
    AgentSocket,
    RunAbortedError,
    SocketSandbox,
    connect_agent_socket,
)
from vibe.cli.headless_run import RunReport
from vibe.core.config import VibeConfigSchema


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
    }
    assert found == ClientToolResult(output={"hits": 1}, annotations={"cost": 2})
    assert failed == ClientToolResult(
        error=ClientToolError(message="no such term", code="not_found")
    )


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


# --- The command line ---------------------------------------------------------


def _read_export(output_dir: Path) -> RunExport:
    return RunExport.model_validate_json(
        (output_dir / EXPORT_FILENAME).read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "extra",
    [["--workdir", "."], ["--worktree"], ["--add-dir", "."], ["-c"], ["--teleport"]],
    ids=["workdir", "worktree", "add-dir", "continue", "teleport"],
)
def test_options_that_pick_a_host_directory_or_session_are_usage_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: list[str]
) -> None:
    """*Prepare*: `vibe -p --agent-socket` with an option that names a host
    directory or an earlier session.
    *Do*: Run the entrypoint.
    *Assert*: It exits 1, with a usage error export naming the option.
    """
    # Prepare
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "vibe",
            "-p",
            "hi",
            "--agent-socket",
            "/tmp/none.sock",
            "--output-dir",
            str(output_dir),
            *extra,
        ],
    )

    # Do
    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    # Assert
    assert exc_info.value.code == 1
    export = _read_export(output_dir)
    assert export.outcome == RunOutcome.USAGE_ERROR
    assert export.error is not None
    assert "--agent-socket cannot be combined with" in export.error.message
    assert extra[0] in export.error.message


def test_resuming_a_named_session_is_not_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: `vibe -p --agent-socket --resume SESSION_ID`, with nothing
    listening on the socket.
    *Do*: Run the entrypoint.
    *Assert*: The options are accepted; the run gets as far as the socket and
    fails there as infrastructure.
    """
    # Prepare
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv",
        [
            "vibe",
            "-p",
            "hi",
            "--agent-socket",
            str(tmp_path / "none.sock"),
            "--resume",
            "abc",
            "--output-dir",
            str(output_dir),
        ],
    )

    # Do
    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    # Assert
    export = _read_export(output_dir)
    assert export.outcome == RunOutcome.INFRASTRUCTURE_FAILURE, export.error
    assert exc_info.value.code == 2


def test_the_option_on_windows_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: `vibe -p --agent-socket` on a platform reported as Windows.
    *Do*: Run the entrypoint.
    *Assert*: It exits 1, with a usage error export saying why.
    """
    # Prepare
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "out"
    monkeypatch.setattr("vibe.utils.platform.is_windows", lambda: True)
    monkeypatch.setattr(
        "sys.argv",
        [
            "vibe",
            "-p",
            "hi",
            "--agent-socket",
            "/tmp/none.sock",
            "--output-dir",
            str(output_dir),
        ],
    )

    # Do
    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    # Assert
    assert exc_info.value.code == 1
    export = _read_export(output_dir)
    assert export.outcome == RunOutcome.USAGE_ERROR
    assert export.error is not None
    assert export.error.message == (
        "--agent-socket needs Unix domain sockets; not supported on Windows"
    )


def test_the_experimental_option_is_hidden_from_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """*Do*: Ask for `vibe --help`.
    *Assert*: The experimental option is not listed.
    """
    monkeypatch.setattr("sys.argv", ["vibe", "--help"])

    with pytest.raises(SystemExit):
        entrypoint_mod.parse_arguments()

    help_text = capsys.readouterr().out
    assert "--prompt-file" in help_text
    assert "--agent-socket" not in help_text


def test_the_option_without_programmatic_mode_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """*Prepare*: `vibe --agent-socket` without -p or --prompt-file.
    *Do*: Run the entrypoint.
    *Assert*: It exits 1, saying the option needs programmatic mode.
    """
    # Prepare
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["vibe", "--agent-socket", "/tmp/none.sock"])

    # Do
    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    # Assert
    assert exc_info.value.code == 1
    assert "--agent-socket needs programmatic mode" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("sandboxed", "expected"),
    [(True, ("user",)), (False, ("user", "project"))],
    ids=["sandbox", "tools-only"],
)
def test_a_sandbox_loads_only_the_users_harness_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    socket_path: Path,
    workspace: Path,
    sandboxed: bool,
    expected: tuple[str, ...],
) -> None:
    """*Prepare*: `vibe -p --agent-socket` on a server with a sandbox, and on
    one with tools only, stopped before the CLI runs.
    *Do*: Run the entrypoint.
    *Assert*: With a sandbox the process-wide harness files are the user's
    only; with tools only, the session is on this host and loads its project.
    """
    # Prepare
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["vibe", "-p", "hi", "--agent-socket", str(socket_path)]
    )
    sources: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "vibe.core.config.harness_files.init_harness_files_manager",
        lambda *names: sources.append(names),
    )
    connected: list[object] = []

    def stop(args: argparse.Namespace, **_kwargs: object) -> None:
        connected.append(args.agent_connection)
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", stop)
    server = FakeAgentServer(
        socket_path, workspace if sandboxed else None, tools=[_LOOKUP]
    )

    # Do
    with serving_in_background(server), pytest.raises(SystemExit):
        entrypoint_mod.main()

    # Assert
    assert sources == [expected]
    [agent] = connected
    assert isinstance(agent, AgentSocket)
    assert (agent.sandbox is not None) == sandboxed
    assert server.methods[0] == "agent/initialize"


def _load_test_config(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    monkeypatch.setattr(cli_mod, "bootstrap_vibe_home", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "load_config_orchestrator",
        lambda: load_orchestrator(build_test_vibe_config()),
    )
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)


def test_a_failed_handshake_is_an_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: `vibe -p --agent-socket` with a socket nothing listens on.
    *Do*: Run the CLI.
    *Assert*: It exits 2 with an infrastructure failure export, before any
    session starts.
    """
    # Prepare
    _load_test_config(monkeypatch, load_orchestrator)
    output_dir = tmp_path / "out"

    def no_session(**_kwargs: object) -> RunReport:
        raise AssertionError("no session starts without a sandbox")

    monkeypatch.setattr(programmatic_mod, "run_programmatic", no_session)

    # Do
    with pytest.raises(SystemExit) as exc_info:
        _run_cli(_make_args(agent_socket=socket_path, output_dir=output_dir))

    # Assert
    assert exc_info.value.code == 2
    export = _read_export(output_dir)
    assert export.outcome == RunOutcome.INFRASTRUCTURE_FAILURE
    assert export.error is not None
    assert str(socket_path) in export.error.message


def test_a_handshake_the_server_aborts_ends_the_run_as_aborted(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: `vibe -p --agent-socket` on a server that answers the
    handshake with the abort code.
    *Do*: Run the CLI.
    *Assert*: It exits 4 with an aborted export carrying the server's message,
    before any session starts.
    """
    # Prepare
    _load_test_config(monkeypatch, load_orchestrator)
    output_dir = tmp_path / "out"

    def no_session(**_kwargs: object) -> RunReport:
        raise AssertionError("no session starts after an abort")

    monkeypatch.setattr(programmatic_mod, "run_programmatic", no_session)

    # Do
    with (
        serving_in_background(FakeAgentServer(socket_path, None, reply=_abort)),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_cli(_make_args(agent_socket=socket_path, output_dir=output_dir))

    # Assert
    assert exc_info.value.code == 4
    export = _read_export(output_dir)
    assert export.outcome == RunOutcome.ABORTED
    assert export.error is not None
    assert export.error.message == "The task's budget is gone"
    assert export.session_id is None


def test_a_tool_vibe_cannot_offer_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: `vibe -p --agent-socket` on a server that declares the same
    tool twice.
    *Do*: Run the CLI.
    *Assert*: It exits 1 with a usage error export, before any session
    starts.
    """
    # Prepare
    _load_test_config(monkeypatch, load_orchestrator)
    output_dir = tmp_path / "out"

    def no_session(**_kwargs: object) -> RunReport:
        raise AssertionError("no session starts with tools Vibe cannot offer")

    monkeypatch.setattr(programmatic_mod, "run_programmatic", no_session)
    server = FakeAgentServer(socket_path, None, tools=[_LOOKUP, _LOOKUP])

    # Do
    with serving_in_background(server), pytest.raises(SystemExit) as exc_info:
        _run_cli(_make_args(agent_socket=socket_path, output_dir=output_dir))

    # Assert
    assert exc_info.value.code == 1
    export = _read_export(output_dir)
    assert export.outcome == RunOutcome.USAGE_ERROR
    assert export.error is not None
    assert "cannot offer" in export.error.message


def test_the_session_runs_in_the_sandbox_untrusted_and_warns_of_project_files(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    socket_path: Path,
    workspace: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """*Prepare*: An agent server whose workspace has a `.vibe` directory, and
    `vibe -p --agent-socket --trust`.
    *Do*: Run the CLI.
    *Assert*: The session gets the connected sandbox, starts in its workspace
    (no host cwd) without trust, and the run warns of the ignored `.vibe`.
    """
    # Prepare
    _load_test_config(monkeypatch, load_orchestrator)
    (workspace / ".vibe").mkdir()
    (workspace / ".vibe" / "config.toml").write_text("", encoding="utf-8")
    call: dict[str, object] = {}

    def fake_run_programmatic(**kwargs: object) -> RunReport:
        call.update(kwargs)
        return RunReport(result=RunResult(outcome=RunOutcome.FINISHED))

    monkeypatch.setattr(programmatic_mod, "run_programmatic", fake_run_programmatic)

    # Do
    with (
        serving_in_background(FakeAgentServer(socket_path, workspace)),
        caplog.at_level("WARNING"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_cli(_make_args(agent_socket=socket_path, trust=True))

    # Assert
    assert exc_info.value.code == 0
    options = call["harness_options"]
    assert isinstance(options, LocalHarnessOptions)
    assert isinstance(options.sandbox, SocketSandbox)
    assert options.sandbox.workspace == str(workspace)
    assert options.session_options.cwd is None
    assert options.session_options.trust_workspace is False
    assert options.session_options.workspace_roots == []
    assert options.client_tools is None
    assert any(
        ".vibe" in record.getMessage() and "ignores" in record.getMessage()
        for record in caplog.records
    )


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


@pytest.mark.parametrize(
    ("time_limit", "timeout"),
    [(None, CLIENT_TOOL_TIMEOUT_SECONDS), (30.0, 30.0)],
    ids=["no-limit", "time-limit"],
)
def test_a_tools_only_socket_runs_the_session_on_this_host_with_its_tools(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
    socket_path: Path,
    tmp_path: Path,
    time_limit: float | None,
    timeout: float,
) -> None:
    """*Prepare*: An agent server with a tool and no workspace, and
    `vibe -p --agent-socket --trust`, with and without `--time-limit`.
    *Do*: Run the CLI.
    *Assert*: The session runs on this host in the current directory, trusted,
    with no sandbox; the process gets the server's tool, run through the
    socket, and a call waits no longer than the run may last. Nothing on the
    session's wire declares the tool.
    """
    # Prepare
    _load_test_config(monkeypatch, load_orchestrator)
    monkeypatch.chdir(tmp_path)
    call: dict[str, object] = {}

    def fake_run_programmatic(**kwargs: object) -> RunReport:
        call.update(kwargs)
        return RunReport(result=RunResult(outcome=RunOutcome.FINISHED))

    monkeypatch.setattr(programmatic_mod, "run_programmatic", fake_run_programmatic)

    # Do
    with (
        serving_in_background(FakeAgentServer(socket_path, None, tools=[_LOOKUP])),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_cli(
            _make_args(agent_socket=socket_path, trust=True, time_limit=time_limit)
        )

    # Assert
    assert exc_info.value.code == 0
    options = call["harness_options"]
    assert isinstance(options, LocalHarnessOptions)
    assert options.sandbox is None
    assert options.session_options.cwd == str(Path.cwd())
    assert options.session_options.trust_workspace is True
    assert options.session_options.tools == []
    client_tools = options.client_tools
    assert client_tools is not None
    assert [tool.name for tool in client_tools.definitions] == ["lookup"]
    assert isinstance(client_tools.runner, AgentSocket)
    assert client_tools.runner.path == socket_path
    assert client_tools.timeout == timeout
