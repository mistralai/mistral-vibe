from __future__ import annotations

from typing import Any, cast

from acp import Client, CreateTerminalResponse, ReadTextFileResponse
from acp.schema import (
    EnvVariable,
    TerminalOutputResponse,
    ToolCallProgress,
    WaitForTerminalExitResponse,
)
import pytest

from vibe.acp.tool_io import AcpClientToolHandler
from vibe.app_server.protocol import (
    ClientToolReadTextFileParams,
    ClientToolTerminalCreateParams,
    ClientToolTerminalParams,
    ClientToolWriteTextFileParams,
)


class FakeAcpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[ToolCallProgress] = []

    async def read_text_file(self, **kwargs: Any) -> ReadTextFileResponse:
        self.calls.append(("read", kwargs))
        return ReadTextFileResponse(content="client contents")

    async def write_text_file(self, **kwargs: Any) -> None:
        self.calls.append(("write", kwargs))

    async def create_terminal(self, **kwargs: Any) -> CreateTerminalResponse:
        self.calls.append(("create", kwargs))
        return CreateTerminalResponse(terminal_id="terminal-1")

    async def wait_for_terminal_exit(
        self, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        self.calls.append(("wait", kwargs))
        return WaitForTerminalExitResponse(exit_code=0)

    async def terminal_output(self, **kwargs: Any) -> TerminalOutputResponse:
        self.calls.append(("output", kwargs))
        return TerminalOutputResponse(output="terminal output", truncated=False)

    async def kill_terminal(self, **kwargs: Any) -> None:
        self.calls.append(("kill", kwargs))

    async def release_terminal(self, **kwargs: Any) -> None:
        self.calls.append(("release", kwargs))

    async def session_update(
        self, *, session_id: str, update: ToolCallProgress, **kwargs: Any
    ) -> None:
        del session_id, kwargs
        self.updates.append(update)


@pytest.mark.asyncio
async def test_acp_client_tool_handler_adapts_host_capabilities() -> None:
    client = FakeAcpClient()
    handler = AcpClientToolHandler(cast(Client, client))
    handler.bind_session("acp-session")

    read = await handler.read_text_file(
        ClientToolReadTextFileParams(
            session_id="session-1", path="/file", line=2, limit=3
        )
    )
    await handler.write_text_file(
        ClientToolWriteTextFileParams(
            session_id="session-1", path="/file", content="updated"
        )
    )
    created = await handler.create_terminal(
        ClientToolTerminalCreateParams(
            session_id="session-1",
            command="/bin/bash",
            args=["-c", "echo hi"],
            env={"CI": "true", "CUSTOM": "value"},
            cwd="/workspace",
            output_byte_limit=100,
            tool_call_id="call-1",
        )
    )
    terminal = ClientToolTerminalParams(
        session_id="session-1", terminal_id=created.terminal_id
    )
    waited = await handler.wait_for_terminal_exit(terminal)
    output = await handler.terminal_output(terminal)
    await handler.kill_terminal(terminal)
    await handler.release_terminal(terminal)

    assert read.content == "client contents"
    assert waited.exit_code == 0
    assert output.output == "terminal output"
    assert [name for name, _ in client.calls] == [
        "read",
        "write",
        "create",
        "wait",
        "output",
        "kill",
        "release",
    ]
    assert all(params["session_id"] == "acp-session" for _, params in client.calls)
    create_params = next(params for name, params in client.calls if name == "create")
    assert create_params["command"] == "/bin/bash"
    assert create_params["args"] == ["-c", "echo hi"]
    assert create_params["env"] == [
        EnvVariable(name="CI", value="true"),
        EnvVariable(name="CUSTOM", value="value"),
    ]
    assert len(client.updates) == 1
    update = client.updates[0]
    assert update.tool_call_id == "call-1"
    assert update.status == "in_progress"
    assert update.kind == "execute"
    assert update.content is not None
    assert update.content[0].type == "terminal"
