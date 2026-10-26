from __future__ import annotations

import argparse
import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys
from threading import Event
from typing import Any
from unittest.mock import AsyncMock

import pytest

from vibe.app_server import stdio
from vibe.app_server._agent_socket import AgentSocketMount
from vibe.app_server._runtime import HarnessProcess, HarnessServer
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


class _RecordingCleanup:
    def __init__(self) -> None:
        self.sweeps = 0

    def sweep(self) -> None:
        self.sweeps += 1


def _stub_stdio_main(monkeypatch: pytest.MonkeyPatch) -> _RecordingCleanup:
    cleanup = _RecordingCleanup()
    monkeypatch.setattr(
        "vibe.app_server._worktree_cleanup.worktree_cleanup", lambda: cleanup
    )
    monkeypatch.setattr(
        stdio,
        "parse_arguments",
        lambda: argparse.Namespace(
            experimental_harness=False,
            legacy_harness=False,
            multiplex=False,
            additional_builtin_plugin_roots=[],
            agent_socket=None,
        ),
    )
    monkeypatch.setattr(stdio, "init_harness_files_manager", lambda *_: None)
    monkeypatch.setattr(stdio, "init_file_logging", lambda _: None)
    monkeypatch.setattr(stdio, "_neutralize_stdout", lambda: None)
    monkeypatch.setattr("setproctitle.setproctitle", lambda _: None)
    monkeypatch.setattr("vibe.core.config.load_dotenv_values", lambda: None)
    monkeypatch.setattr(
        "vibe.core.utils.windows_asyncio.silence_proactor_transport_teardown_warnings",
        lambda: None,
    )
    return cleanup


def test_stdio_main_names_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    titles: list[str] = []
    _stub_stdio_main(monkeypatch)
    monkeypatch.setattr("setproctitle.setproctitle", titles.append)
    monkeypatch.setattr(stdio.asyncio, "run", lambda coroutine: coroutine.close())

    stdio.main()

    assert titles == ["vibe-app-server"]


def test_stdio_main_starts_when_naming_the_process_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_: str) -> None:
        raise OSError("cannot rename")

    cleanup = _stub_stdio_main(monkeypatch)
    monkeypatch.setattr("setproctitle.setproctitle", fail)
    monkeypatch.setattr(stdio.asyncio, "run", lambda coroutine: coroutine.close())

    stdio.main()

    assert cleanup.sweeps == 1


def test_stdio_main_finishes_worktree_reaps_an_earlier_process_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reap left by a process that exited mid-cleanup is picked up at startup."""
    cleanup = _stub_stdio_main(monkeypatch)
    monkeypatch.setattr(stdio.asyncio, "run", lambda coroutine: coroutine.close())

    stdio.main()

    assert cleanup.sweeps == 1


def test_stdio_main_neutralizes_stdout_after_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    neutralized = False

    def run(coroutine: Any) -> None:
        coroutine.close()

    def neutralize_stdout() -> None:
        nonlocal neutralized
        neutralized = True

    _stub_stdio_main(monkeypatch)
    monkeypatch.setattr(
        stdio,
        "parse_arguments",
        lambda: argparse.Namespace(
            multiplex=False, additional_builtin_plugin_roots=[], agent_socket=None
        ),
    )
    monkeypatch.setattr(stdio, "init_harness_files_manager", lambda *_: None)
    monkeypatch.setattr(stdio, "init_file_logging", lambda _: None)
    monkeypatch.setattr(stdio.asyncio, "run", run)
    monkeypatch.setattr(stdio, "_neutralize_stdout", neutralize_stdout)
    monkeypatch.setattr("vibe.core.config.load_dotenv_values", lambda: None)
    monkeypatch.setattr(
        "vibe.core.utils.windows_asyncio.silence_proactor_transport_teardown_warnings",
        lambda: None,
    )

    stdio.main()

    assert neutralized


@pytest.mark.asyncio
async def test_stdio_server_creates_the_harness_behind_its_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = AsyncMock(spec=HarnessServer)
    factory = AsyncMock(return_value=harness)
    monkeypatch.setattr(stdio, "create_harness_server", factory)
    warmed: list[HarnessProcess] = []
    monkeypatch.setattr(
        HarnessProcess, "start_connector_warm_up", lambda self: warmed.append(self)
    )

    await stdio.serve_stdio(reader=BytesIO(), writer=BytesIO())

    factory.assert_awaited_once()
    call = factory.await_args
    assert call is not None
    args, kwargs = call
    assert isinstance(args[0], StdioJsonRpcTransport)
    assert kwargs == {"transport_kind": "stdio", "process": warmed[0]}
    harness.serve.assert_awaited_once_with()


def test_stdio_arguments_take_the_hidden_agent_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["vibe-app-server", "--agent-socket", "/tmp/agent.sock"]
    )

    arguments = stdio.parse_arguments()

    assert arguments.agent_socket == Path("/tmp/agent.sock")
    assert arguments.multiplex is False


@pytest.mark.asyncio
async def test_stdio_server_with_an_agent_socket_mounts_it_in_its_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = AsyncMock(spec=HarnessServer)
    factory = AsyncMock(return_value=harness)
    monkeypatch.setattr(stdio, "create_harness_server", factory)
    built: list[dict[str, Any]] = []
    monkeypatch.setattr(stdio, "HarnessProcess", lambda **kwargs: built.append(kwargs))
    mount = AgentSocketMount(Path("/tmp/agent.sock"))

    await stdio.serve_stdio(reader=BytesIO(), writer=BytesIO(), agent_socket=mount)

    assert built == [{"additional_builtin_plugin_roots": (), "agent_socket": mount}]
    harness.serve.assert_awaited_once_with()


def test_stdio_arguments_collect_every_builtin_plugin_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vibe-app-server",
            "--additional-builtin-plugin-root",
            "/bundle/plugins",
            "--additional-builtin-plugin-root",
            "/bundle/more-plugins",
        ],
    )

    arguments = stdio.parse_arguments()

    assert arguments.additional_builtin_plugin_roots == [
        Path("/bundle/plugins"),
        Path("/bundle/more-plugins"),
    ]


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


@pytest.mark.asyncio
async def test_stdio_transport_batches_messages_queued_behind_a_write() -> None:
    writer = BlockingWriter()
    transport = StdioJsonRpcTransport(BytesIO(), writer, outbox_size=8)
    writes: list[bytes] = []
    write = writer.write

    def record(data: bytes) -> int:
        writes.append(data)
        return write(data)

    writer.write = record  # type: ignore[method-assign]
    await transport.send({"id": 1})
    assert await asyncio.to_thread(writer.started.wait, 1)
    for index in range(2, 6):
        await transport.send({"id": index})
    writer.release.set()
    await asyncio.wait_for(transport.close(), timeout=1)

    assert [len(data.splitlines()) for data in writes] == [1, 4]
    messages = [json.loads(line) for line in writer.output.getvalue().splitlines()]
    assert messages == [{"id": index} for index in range(1, 6)]


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
