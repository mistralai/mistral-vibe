from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import build_test_app_server
from vibe.app_server._tool_io import ClientToolIO
from vibe.app_server.models import ProtocolModel
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientToolReadTextFileParams,
    ClientToolReadTextFileResponse,
    ClientToolTerminalCreateParams,
    ClientToolTerminalCreateResponse,
    ClientToolTerminalOutputResponse,
    ClientToolTerminalWaitResponse,
    EmptyResponse,
    JsonRpcSuccessResponse,
)
from vibe.app_server.transport import memory_transport_pair
from vibe.core.tools.io_port import ShellCommandRequest


class FakeClientRequester:
    def __init__(
        self, terminal_exit: ClientToolTerminalWaitResponse | None = None
    ) -> None:
        self.calls: list[tuple[str, ProtocolModel]] = []
        self.terminal_exit = terminal_exit or ClientToolTerminalWaitResponse(
            exit_code=0
        )

    async def __call__[ResultT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResultT]
    ) -> ResultT:
        self.calls.append((method, params))
        match method:
            case "clientTool/readTextFile":
                request = cast(ClientToolReadTextFileParams, params)
                content = "first\nsecond\nthird\n" if request.limit else "a\r\nb\r\n"
                response: ProtocolModel = ClientToolReadTextFileResponse(
                    content=content
                )
            case "clientTool/terminal/create":
                response = ClientToolTerminalCreateResponse(terminal_id="terminal-1")
            case "clientTool/terminal/wait":
                response = self.terminal_exit
            case "clientTool/terminal/output":
                response = ClientToolTerminalOutputResponse(
                    output="host output", truncated=False
                )
            case _:
                response = EmptyResponse()
        return response_type.model_validate(response.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_client_tool_io_projects_typed_filesystem_and_terminal_requests() -> None:
    requester = FakeClientRequester()
    capabilities = ClientCapabilities(
        client_tools=["filesystem/read", "filesystem/write", "terminal"]
    )
    tool_io = ClientToolIO(requester, lambda: capabilities, lambda: "root")

    bounded = await tool_io.read_lines(
        Path("/workspace/file.txt"), start_line=1, limit=2, max_bytes=100
    )
    text = await tool_io.read_text(Path("/workspace/file.txt"))
    result = await tool_io.run_shell(
        ShellCommandRequest(
            session_id="root",
            tool_call_id="call-1",
            command="/bin/bash",
            args=["-c", "echo hi"],
            env={"CI": "true"},
            cwd=Path("/workspace"),
            timeout=1,
            max_output_bytes=100,
        )
    )

    assert bounded.lines == ["first", "second"]
    assert bounded.was_truncated
    assert text.text == "a\nb\n"
    assert text.newline == "\r\n"
    assert result.stdout == "host output"
    terminal_create = cast(
        ClientToolTerminalCreateParams,
        next(
            params
            for method, params in requester.calls
            if method == "clientTool/terminal/create"
        ),
    )
    assert terminal_create.command == "/bin/bash"
    assert terminal_create.args == ["-c", "echo hi"]
    assert terminal_create.env == {"CI": "true"}
    methods = [method for method, _ in requester.calls]
    assert methods == [
        "clientTool/readTextFile",
        "clientTool/readTextFile",
        "clientTool/terminal/create",
        "clientTool/terminal/wait",
        "clientTool/terminal/output",
        "clientTool/terminal/release",
    ]


@pytest.mark.asyncio
async def test_client_terminal_signal_is_not_reported_as_success() -> None:
    requester = FakeClientRequester(ClientToolTerminalWaitResponse(signal="SIGTERM"))
    tool_io = ClientToolIO(
        requester, lambda: ClientCapabilities(client_tools=["terminal"]), lambda: "root"
    )

    result = await tool_io.run_shell(
        ShellCommandRequest(
            session_id="root",
            tool_call_id="call-1",
            command="sleep 10",
            cwd=Path("/workspace"),
            timeout=1,
            max_output_bytes=100,
        )
    )

    assert result.returncode == -1
    assert result.stderr == "Process terminated by SIGTERM"


@pytest.mark.asyncio
async def test_server_ignores_late_client_tool_response_after_cancellation() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    server._connection_attached = True
    request = asyncio.create_task(
        server._request_client_result("clientTool/test", EmptyResponse(), EmptyResponse)
    )
    outgoing = await anext(client_transport.messages())
    request_id = cast(int, outgoing["id"])

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    await server._handle_response(JsonRpcSuccessResponse(id=request_id, result={}))

    assert server._abandoned_client_request_ids == set()
    await server.close()
    await client_transport.close()
    await agent_loop.aclose()


@pytest.mark.asyncio
async def test_child_sessions_share_the_server_owned_tool_io_port() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    child = await server._runtime_factory.create_child(agent_loop, "explore")
    child_runtime = server._sessions._build_child_runtime(child)

    assert child_runtime.turns._tool_io is server._tool_io

    await child_runtime.close()
    await server.close()
    await client_transport.close()
    await agent_loop.aclose()
