from __future__ import annotations

import asyncio
from contextlib import suppress
from io import BytesIO
import json
import socket
from threading import Event
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import build_test_app_server
from vibe.app_server import stdio
from vibe.app_server._legacy_composition import create_legacy_app_server
from vibe.app_server._runtime import HarnessServer, RootOpenRequest
from vibe.app_server.client import AppServerClient
from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.models import PublicMessageEntry
from vibe.app_server.protocol import ClientCapabilities, ClientInfo
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import (
    InvalidJsonRpcMessage,
    MemoryJsonRpcTransport,
    StdioJsonRpcTransport,
)


class BlockingWriter:
    def __init__(self, failure: str | None = None) -> None:
        self.output = BytesIO()
        self.started = Event()
        self.release = Event()
        self.failure = failure

    def write(self, data: bytes) -> int:
        self.started.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test writer was not released")
        if self.failure == "write":
            raise OSError("write failed")
        return self.output.write(data)

    def flush(self) -> None:
        if self.failure == "flush":
            raise OSError("flush failed")
        self.output.flush()


class CountingWriter(BytesIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


@pytest.mark.asyncio
async def test_stdio_server_creates_the_harness_behind_its_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = AsyncMock(spec=HarnessServer)
    factory = AsyncMock(return_value=harness)
    monkeypatch.setattr(stdio, "create_harness_server", factory)

    await stdio.serve_stdio(reader=BytesIO(), writer=BytesIO())

    factory.assert_awaited_once()
    call = factory.await_args
    assert call is not None
    args, kwargs = call
    assert isinstance(args[0], StdioJsonRpcTransport)
    assert kwargs == {"transport_kind": "stdio", "experimental_harness": False}
    harness.serve.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_stdio_server_forwards_experimental_harness_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = AsyncMock(spec=HarnessServer)
    factory = AsyncMock(return_value=harness)
    monkeypatch.setattr(stdio, "create_harness_server", factory)

    await stdio.serve_stdio(
        reader=BytesIO(), writer=BytesIO(), experimental_harness=True
    )

    call = factory.await_args
    assert call is not None
    _, kwargs = call
    assert kwargs == {"transport_kind": "stdio", "experimental_harness": True}


@pytest.mark.asyncio
async def test_stdio_transport_reads_json_lines() -> None:
    reader = BytesIO(
        b'{"jsonrpc":"2.0","method":"first"}\n{"jsonrpc":"2.0","method":"second"}\n'
    )
    transport = StdioJsonRpcTransport(reader, BytesIO())

    messages = [message async for message in transport.messages()]

    assert [message["method"] for message in messages] == ["first", "second"]
    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (b"{\n", "Malformed JSON-RPC message"),
        (b"[]\n", "JSON-RPC messages must be objects"),
    ],
)
async def test_stdio_transport_rejects_invalid_messages(raw: bytes, error: str) -> None:
    transport = StdioJsonRpcTransport(BytesIO(raw), BytesIO())

    with pytest.raises(InvalidJsonRpcMessage, match=error):
        await anext(transport.messages())

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "error"),
    [("{", "Malformed JSON-RPC message"), ("[]", "JSON-RPC messages must be objects")],
)
async def test_memory_transport_rejects_invalid_messages(raw: str, error: str) -> None:
    incoming: asyncio.Queue[str | None] = asyncio.Queue()
    outgoing: asyncio.Queue[str | None] = asyncio.Queue()
    transport = MemoryJsonRpcTransport(incoming, outgoing)
    await incoming.put(raw)

    with pytest.raises(InvalidJsonRpcMessage, match=error):
        await anext(transport.messages())

    await transport.close()


@pytest.mark.asyncio
async def test_stdio_transport_uses_one_ordered_writer() -> None:
    output = BytesIO()
    transport = StdioJsonRpcTransport(BytesIO(), output, outbox_size=1)

    await transport.send({"jsonrpc": "2.0", "id": 1, "result": {}})
    notification = asyncio.create_task(
        transport.send({"jsonrpc": "2.0", "method": "turn/started"})
    )
    await notification
    await transport.close()

    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert messages == [
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "method": "turn/started"},
    ]


@pytest.mark.asyncio
async def test_stdio_transport_bounds_pending_output() -> None:
    writer = BlockingWriter()
    transport = StdioJsonRpcTransport(BytesIO(), writer, outbox_size=1)

    await transport.send({"id": 1})
    assert await asyncio.to_thread(writer.started.wait, 1)
    await transport.send({"id": 2})
    pending = asyncio.create_task(transport.send({"id": 3}))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not pending.done()

    writer.release.set()
    await asyncio.wait_for(pending, timeout=1)
    await asyncio.wait_for(transport.close(), timeout=1)

    messages = [json.loads(line) for line in writer.output.getvalue().splitlines()]
    assert messages == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_stdio_transport_rejects_unbounded_outbox() -> None:
    with pytest.raises(ValueError, match="outbox_size must be positive"):
        StdioJsonRpcTransport(BytesIO(), BytesIO(), outbox_size=0)


@pytest.mark.asyncio
async def test_stdio_transport_send_propagates_write_failure_from_full_outbox() -> None:
    writer = BlockingWriter(failure="write")
    transport = StdioJsonRpcTransport(BytesIO(), writer, outbox_size=1)

    await transport.send({"id": 1})
    assert await asyncio.to_thread(writer.started.wait, 1)
    await transport.send({"id": 2})
    pending = asyncio.create_task(transport.send({"id": 3}))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not pending.done()

    writer.release.set()
    with pytest.raises(OSError, match="write failed"):
        await asyncio.wait_for(pending, timeout=1)
    with pytest.raises(OSError, match="write failed"):
        await asyncio.wait_for(transport.close(), timeout=1)


@pytest.mark.asyncio
async def test_stdio_close_propagates_flush_failure_from_full_outbox() -> None:
    writer = BlockingWriter(failure="flush")
    transport = StdioJsonRpcTransport(BytesIO(), writer, outbox_size=1)

    await transport.send({"id": 1})
    assert await asyncio.to_thread(writer.started.wait, 1)
    await transport.send({"id": 2})
    closing = asyncio.create_task(transport.close())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not closing.done()

    writer.release.set()
    with pytest.raises(OSError, match="flush failed"):
        await asyncio.wait_for(closing, timeout=1)


@pytest.mark.asyncio
async def test_stdio_transport_close_is_idempotent() -> None:
    writer = CountingWriter()
    transport = StdioJsonRpcTransport(BytesIO(), writer)
    await transport.send({"id": 1})

    await asyncio.gather(transport.close(), transport.close())
    await transport.close()

    assert writer.flush_count == 1
    assert [json.loads(line) for line in writer.getvalue().splitlines()] == [{"id": 1}]


@pytest.mark.asyncio
async def test_stdio_server_uses_the_same_json_rpc_lifecycle() -> None:
    agent_loop = build_test_agent_loop()

    async def open_root(_request: RootOpenRequest):
        return agent_loop

    input_messages = [
        {
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "test", "version": "1"},
                "capabilities": {"callbackKinds": ["approval", "user_input"]},
            },
        },
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": "start",
            "method": "session/start",
            "params": {"agentConfig": {"cwd": str(agent_loop.cwd)}},
        },
        {
            "jsonrpc": "2.0",
            "id": "read",
            "method": "session/read",
            "params": {"sessionId": agent_loop.session_id},
        },
    ]
    reader = BytesIO(
        b"".join(json.dumps(message).encode() + b"\n" for message in input_messages)
    )
    output = BytesIO()

    await create_legacy_app_server(
        StdioJsonRpcTransport(reader, output),
        open_root=open_root,
        transport_kind="stdio",
    ).serve()

    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["result"] == {
        "serverInfo": {
            "name": "vibe-app-server",
            "version": responses[0]["result"]["serverInfo"]["version"],
        }
    }
    assert responses[1]["result"]["state"]["session"]["id"] == (agent_loop.session_id)
    assert responses[2]["result"]["state"]["session"]["id"] == (agent_loop.session_id)


@pytest.mark.asyncio
async def test_app_server_session_round_trips_a_turn_over_stdio() -> None:
    client_socket, server_socket = socket.socketpair()
    client_reader = client_socket.makefile("rb")
    client_writer = client_socket.makefile("wb")
    server_reader = server_socket.makefile("rb")
    server_writer = server_socket.makefile("wb")
    agent_loop = build_test_agent_loop()
    client_transport = StdioJsonRpcTransport(client_reader, client_writer)
    server_transport = StdioJsonRpcTransport(server_reader, server_writer)
    server = build_test_app_server(agent_loop, server_transport)
    session = await AppServerSession.start(
        AppServerClient(client_transport, run_peer=server.serve),
        client_info=ClientInfo(name="stdio-test", version="1"),
        capabilities=ClientCapabilities(callback_kinds=["approval", "user_input"]),
    )

    try:
        events = [event async for event in session.act("hello")]
        messages = [
            event.entry
            for event in events
            if isinstance(event, HistoryEntryAdded)
            and isinstance(event.entry, PublicMessageEntry)
        ]
        assert [(message.role, message.text) for message in messages] == [
            ("user", "hello")
        ]
    finally:
        await session.close()
        for sock in (client_socket, server_socket):
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
        for stream in (client_reader, client_writer, server_reader, server_writer):
            stream.close()
        client_socket.close()
        server_socket.close()


@pytest.mark.asyncio
async def test_stdio_session_start_maps_agent_config_workdir_to_runtime_cwd() -> None:
    agent_loop = build_test_agent_loop()
    captured: list[RootOpenRequest] = []

    async def open_root(request: RootOpenRequest):
        captured.append(request)
        return agent_loop

    input_messages = [
        {
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "stdio-client",
                    "version": "1",
                    "entrypoint": "programmatic",
                    "terminalEmulator": "ghostty",
                },
                "capabilities": {"clientTools": ["terminal"]},
            },
        },
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": "start",
            "method": "session/start",
            "params": {
                "agentConfig": {
                    "workdir": "/workspace",
                    "workspaceRoots": ["/other"],
                    "agent": "plan",
                    "autoApprove": True,
                    "enabledTools": ["read_file"],
                    "disabledTools": ["bash"],
                    "maxTurns": 3,
                    "maxPrice": 2.5,
                    "maxSessionTokens": 1000,
                    "headless": True,
                    "trustWorkspace": True,
                    "mcpServers": [],
                }
            },
        },
    ]
    reader = BytesIO(
        b"".join(json.dumps(message).encode() + b"\n" for message in input_messages)
    )

    await create_legacy_app_server(
        StdioJsonRpcTransport(reader, BytesIO()),
        open_root=open_root,
        transport_kind="stdio",
    ).serve()

    assert len(captured) == 1
    request = captured[0]
    assert request.client_capabilities.client_tools == ["terminal"]
    options = request.options
    assert options.model_dump(
        by_alias=False,
        exclude={"completion", "sandbox", "instructions", "tools", "hooks"},
    ) == {
        "workdir": "/workspace",
        "cwd": "/workspace",
        "workspace_roots": ["/other"],
        "worktree": None,
        "agent": "plan",
        "auto_approve": True,
        "enabled_tools": ["read_file"],
        "disabled_tools": ["bash"],
        "max_turns": 3,
        "max_price": 2.5,
        "max_session_tokens": 1000,
        "headless": True,
        "trust_workspace": True,
        "mcp_servers": [],
    }
    assert request.client_info.entrypoint == "programmatic"
    assert request.client_info.terminal_emulator == "ghostty"
    assert request.session_id is None
    assert request.continue_latest is False
