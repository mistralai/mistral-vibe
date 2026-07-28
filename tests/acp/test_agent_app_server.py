from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from unittest.mock import AsyncMock

from acp import (
    PROTOCOL_VERSION,
    CreateTerminalResponse,
    ReadTextFileResponse,
    RequestPermissionResponse,
)
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    ClientCapabilities,
    EnvVariable,
    FileSystemCapabilities,
    HttpHeader,
    HttpMcpServer,
    McpServerStdio,
    TerminalOutputResponse,
    TextContentBlock,
    UserMessageChunk,
    WaitForTerminalExitResponse,
)
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import start_test_app_server
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.app_server.local import LocalHarnessOptions
from vibe.app_server.models import PublicError, PublicTurnStatus
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.core.config import SessionLoggingConfig
from vibe.core.types import FunctionCall, ScheduledLoop, ToolCall


def _agent(backend: FakeBackend) -> tuple[VibeAcpAgent, FakeClient]:
    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        loop = build_test_agent_loop(backend=backend, enable_streaming=True)
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
            client_tool_handler=options.client_tool_handler,
        )

    agent = VibeAcpAgent(session_starter=start_session)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    return agent, client


@pytest.mark.asyncio
async def test_prompt_is_a_thin_translation_of_public_app_server_events() -> None:
    agent, client = _agent(FakeBackend([mock_llm_chunk(content="Public response")]))
    try:
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        response = await agent.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )

        assert response.stop_reason == "end_turn"
        updates = [notification.update for notification in client._session_updates]
        messages = [
            update
            for update in updates
            if isinstance(update, UserMessageChunk | AgentMessageChunk)
        ]
        assert [update.content.text for update in messages] == [
            "Hello",
            "Public response",
        ]
        assert all(update.message_id for update in messages)
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_cancelled_prompt_uses_interrupted_app_server_turn_status() -> None:
    backend_started = asyncio.Event()
    release_backend = asyncio.Event()

    class GatedBackend(FakeBackend):
        async def complete_streaming(self, **kwargs):
            backend_started.set()
            await release_backend.wait()
            async for chunk in super().complete_streaming(**kwargs):
                yield chunk

    agent, _ = _agent(GatedBackend([mock_llm_chunk(content="unused")]))
    try:
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        prompt_task = asyncio.create_task(
            agent.prompt(
                session_id=created.session_id,
                prompt=[TextContentBlock(type="text", text="Wait")],
            )
        )
        try:
            await asyncio.wait_for(backend_started.wait(), timeout=1)
            await agent.cancel(created.session_id)
            response = await asyncio.wait_for(prompt_task, timeout=1)

            assert response.stop_reason == "cancelled"
            session = agent.sessions[created.session_id]
            turn = session.app_server.state.latest_turn
            assert turn is not None
            assert turn.status is PublicTurnStatus.INTERRUPTED
        finally:
            release_backend.set()
            if not prompt_task.done():
                prompt_task.cancel()
                await asyncio.gather(prompt_task, return_exceptions=True)
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_reasoning_is_projected_from_public_app_server_events() -> None:
    agent, client = _agent(
        FakeBackend([mock_llm_chunk(content="Answer", reasoning_content="Thinking")])
    )
    try:
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        await agent.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )

        thoughts = [
            notification.update
            for notification in client._session_updates
            if isinstance(notification.update, AgentThoughtChunk)
        ]
        assert len(thoughts) == 1
        assert thoughts[0].content.text == "Thinking"
        assert thoughts[0].message_id
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_acp_session_workspace_and_mcp_inputs_cross_the_app_server_boundary(
    tmp_path: Path,
) -> None:
    captured: list[LocalHarnessOptions] = []

    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        captured.append(options)
        loop = build_test_agent_loop(enable_streaming=True)
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
            client_tool_handler=options.client_tool_handler,
        )

    agent = VibeAcpAgent(session_starter=start_session)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        await agent.new_session(
            cwd=str(Path.cwd()),
            additional_directories=[str(workspace)],
            mcp_servers=[
                HttpMcpServer(
                    type="http",
                    name="remote",
                    url="https://mcp.example.test",
                    headers=[HttpHeader(name="Authorization", value="token")],
                ),
                McpServerStdio(
                    name="local",
                    command="mcp-server",
                    args=["--stdio"],
                    env=[EnvVariable(name="TOKEN", value="secret")],
                ),
            ],
        )

        options = captured[0].session_options
        assert options.workspace_roots == [str(workspace)]
        assert [server.name for server in options.mcp_servers] == ["remote", "local"]
        assert options.mcp_servers[0].transport == "streamable-http"
        assert options.mcp_servers[1].transport == "stdio"
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_unsolicited_scheduled_turn_is_forwarded_to_acp(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )

    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        loop = build_test_agent_loop(
            config=config,
            backend=FakeBackend([mock_llm_chunk(content="Scheduled response")]),
            enable_streaming=True,
        )
        metadata = loop.session_logger.session_metadata
        assert metadata is not None
        now = time.time()
        metadata.loops = [
            ScheduledLoop(
                id="scheduled-1",
                interval_seconds=30,
                prompt="Scheduled prompt",
                next_fire_at=now - 1,
                created_at=now - 31,
            )
        ]
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
            client_tool_handler=options.client_tool_handler,
        )

    agent = VibeAcpAgent(session_starter=start_session)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    try:
        await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        async with asyncio.timeout(2):
            while True:
                messages = [
                    update.content.text
                    for notification in client._session_updates
                    if isinstance(
                        update := notification.update,
                        UserMessageChunk | AgentMessageChunk,
                    )
                ]
                if messages == ["Scheduled prompt", "Scheduled response"]:
                    break
                await asyncio.sleep(0.01)
    finally:
        await agent.close()


def test_acp_runtime_adapter_has_no_direct_core_dependency() -> None:
    root = Path(__file__).parents[2] / "vibe" / "acp"
    runtime_files = [
        root / "agent.py",
        root / "session.py",
        root / "session_updates.py",
    ]
    contents = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)

    assert "AgentLoop" not in contents
    assert ".agent_loop" not in contents
    assert "vibe.acp.tools" not in contents


@pytest.mark.asyncio
async def test_response_too_long_returns_max_tokens_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = _agent(FakeBackend())
    try:
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        session = agent.sessions[created.session_id]

        async def response_too_long(*args: object, **kwargs: object):
            del args, kwargs
            if False:
                yield
            raise AppServerTurnError(
                PublicError(
                    code="response_too_long",
                    message="Response exceeded the output token limit",
                )
            )

        monkeypatch.setattr(session.app_server, "act", response_too_long)

        response = await agent.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="Write a long answer")],
        )

        assert response.stop_reason == "max_tokens"
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_acp_host_filesystem_flows_through_the_app_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = Path.cwd() / "host.txt"
    file_path.touch()
    tool_call = ToolCall(
        id="read-1",
        index=0,
        function=FunctionCall(
            name="read_file", arguments=json.dumps({"file_path": str(file_path)})
        ),
    )
    agent, client = _agent(
        FakeBackend([
            [mock_llm_chunk(content="", tool_calls=[tool_call])],
            [mock_llm_chunk(content="done")],
        ])
    )
    read_text_file = AsyncMock(
        return_value=ReadTextFileResponse(content="from the ACP host")
    )
    monkeypatch.setattr(client, "read_text_file", read_text_file)
    await agent.initialize(
        protocol_version=PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(
            fs=FileSystemCapabilities(read_text_file=True, write_text_file=False)
        ),
    )

    try:
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        response = await agent.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="Read the host file")],
        )

        assert response.stop_reason == "end_turn"
        read_text_file.assert_awaited_once()
        await_args = read_text_file.await_args
        assert await_args is not None
        assert await_args.kwargs["path"] == str(file_path)
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_acp_host_write_only_filesystem_flows_through_the_app_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = Path.cwd() / "host-write.txt"
    tool_call = ToolCall(
        id="write-1",
        index=0,
        function=FunctionCall(
            name="write_file",
            arguments=json.dumps({"file_path": str(file_path), "content": "hello"}),
        ),
    )
    agent, client = _agent(
        FakeBackend([
            [mock_llm_chunk(content="", tool_calls=[tool_call])],
            [mock_llm_chunk(content="done")],
        ])
    )
    write_text_file = AsyncMock()
    monkeypatch.setattr(client, "write_text_file", write_text_file)
    monkeypatch.setattr(
        client,
        "request_permission",
        AsyncMock(
            return_value=RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id="allow_once")
            )
        ),
    )
    await agent.initialize(
        protocol_version=PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(
            fs=FileSystemCapabilities(read_text_file=False, write_text_file=True)
        ),
    )

    try:
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        response = await agent.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="Write the host file")],
        )

        assert response.stop_reason == "end_turn"
        write_text_file.assert_awaited_once_with(
            session_id=created.session_id, path=str(file_path), content="hello"
        )
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_acp_terminal_timeout_can_kill_while_wait_request_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call = ToolCall(
        id="shell-1",
        index=0,
        function=FunctionCall(
            name="bash", arguments=json.dumps({"command": "echo hi", "timeout": 1})
        ),
    )
    agent, client = _agent(
        FakeBackend([
            [mock_llm_chunk(content="", tool_calls=[tool_call])],
            [mock_llm_chunk(content="done")],
        ])
    )
    wait_started = asyncio.Event()

    async def wait_forever(**kwargs: object) -> WaitForTerminalExitResponse:
        del kwargs
        wait_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    create_terminal = AsyncMock(
        return_value=CreateTerminalResponse(terminal_id="terminal-1")
    )
    wait_for_terminal_exit = AsyncMock(side_effect=wait_forever)
    kill_terminal = AsyncMock()
    release_terminal = AsyncMock()
    terminal_output = AsyncMock(
        return_value=TerminalOutputResponse(output="", truncated=False)
    )
    monkeypatch.setattr(client, "create_terminal", create_terminal)
    monkeypatch.setattr(client, "wait_for_terminal_exit", wait_for_terminal_exit)
    monkeypatch.setattr(client, "kill_terminal", kill_terminal)
    monkeypatch.setattr(client, "release_terminal", release_terminal)
    monkeypatch.setattr(client, "terminal_output", terminal_output)
    await agent.initialize(
        protocol_version=PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(terminal=True),
    )

    try:
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        response = await agent.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="Run the command")],
        )

        assert response.stop_reason == "end_turn"
        assert wait_started.is_set()
        kill_terminal.assert_awaited_once()
        release_terminal.assert_awaited_once()
        terminal_output.assert_not_awaited()
    finally:
        await agent.close()
