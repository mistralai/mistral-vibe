from __future__ import annotations

import asyncio
from collections.abc import Iterator
from io import BytesIO
import os
from pathlib import Path
import signal
import sys
import threading
from typing import Any, cast

import pytest

from tests.stubs.thread_signals import sigterm_another_thread_once_the_loop_waits
from vibe.app_server import _termination, stdio
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._termination import (
    TERMINATED_EXIT_CODE,
    InputEndingTransport,
    ending_on_sigterm,
)
from vibe.app_server.multiplex import (
    _CHANNEL_INBOX_SIZE,
    ChannelOpen,
    _ChannelTransport,
    serve_multiplexed,
)
from vibe.app_server.server import AppServer
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair
from vibe.core.config.harness_files import HarnessFilesManager

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")


class OpenStdin:
    """A stdin whose writer never closes it: each read blocks until released."""

    def __init__(self) -> None:
        self.released = threading.Event()

    def readline(self) -> bytes:
        self.released.wait(timeout=10)
        return b""


@pytest.fixture
def stdin() -> Iterator[OpenStdin]:
    stdin = OpenStdin()
    yield stdin
    stdin.released.set()


async def _wait_for(condition: Any, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_ending_the_input_ends_its_messages_as_the_end_of_the_stream_would() -> (
    None
):
    peer, transport = memory_transport_pair()
    ending = InputEndingTransport(transport)
    received: list[dict[str, Any]] = []

    async def serve() -> None:
        async for message in ending.messages():
            received.append(message)
            await ending.send({"echo": message["id"]})

    serving = asyncio.create_task(serve())
    await peer.send({"id": 1})
    assert await anext(aiter(peer.messages())) == {"echo": 1}
    ending.end_input()

    async with asyncio.timeout(5):
        await serving
    assert received == [{"id": 1}]


@pytest.mark.asyncio
async def test_cancelling_the_serving_mid_read_ends_its_messages_cancelled() -> None:
    _, transport = memory_transport_pair()
    ending = InputEndingTransport(transport)
    reading = asyncio.Event()

    async def serve() -> None:
        messages = ending.messages()
        reading.set()
        async for _ in messages:
            pass

    serving = asyncio.create_task(serve())
    await reading.wait()
    await asyncio.sleep(0)
    serving.cancel()

    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(5):
            await serving


@pytest.mark.asyncio
async def test_a_server_closing_itself_mid_read_ends_its_serving_cleanly(
    process_services: ProcessServices,
) -> None:
    class Host:
        async def shutdown(self) -> None:
            pass

    _, transport = memory_transport_pair()
    server = AppServer(
        InputEndingTransport(transport),
        oidc_tenants=process_services.oidc_tenants,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, Host()),
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=("user", "project")),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        ),
    )
    serving = asyncio.create_task(server.serve())
    await asyncio.sleep(0)

    # As session/stop does, from its request's task.
    await server.close()

    async with asyncio.timeout(5):
        await serving


class _ClientLossHost:
    def __init__(self) -> None:
        self.disconnected = False

    def mark_client_disconnected(self) -> None:
        self.disconnected = True

    async def shutdown(self) -> None:
        pass


def _server_over(
    transport: JsonRpcTransport, host: _ClientLossHost, services: ProcessServices
) -> AppServer:
    return AppServer(
        transport,
        oidc_tenants=services.oidc_tenants,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, host),
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=("user", "project")),
            org_config=services.org_config,
            worktrees=services.worktrees,
            experiments=services.experiments,
            model_catalog=services.model_catalog,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("stopped", "disconnected"), [(False, True), (True, False)])
async def test_only_an_input_ending_without_a_stop_reports_a_lost_client(
    process_services: ProcessServices, stopped: bool, disconnected: bool
) -> None:
    _, transport = memory_transport_pair()
    ending = InputEndingTransport(transport)
    host = _ClientLossHost()
    server = _server_over(ending, host, process_services)
    serving = asyncio.create_task(server.serve())
    await asyncio.sleep(0)

    if stopped:
        await server.close()
    else:
        ending.end_input()
    async with asyncio.timeout(5):
        await serving

    assert host.disconnected is disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("closed", "acked", "disconnected"),
    [(True, True, False), (True, False, False), (False, False, True)],
)
async def test_only_a_channel_ending_without_its_close_frame_reports_a_lost_client(
    process_services: ProcessServices, closed: bool, acked: bool, disconnected: bool
) -> None:
    client, transport = memory_transport_pair()
    host = _ClientLossHost()

    async def create_server(
        channel: JsonRpcTransport, _options: ChannelOpen | None
    ) -> AppServer:
        return _server_over(channel, host, process_services)

    serving = asyncio.create_task(serve_multiplexed(transport, create_server))
    request = {"jsonrpc": "2.0", "id": 1, "method": "session/list", "params": {}}
    await client.send({"channelId": "1", "message": request})

    # As Vibe Desktop ends a session: a close frame, never a session/stop. Unacked,
    # the stream ends before the channel's server reads the close.
    if closed:
        await client.send({"channelId": "1", "message": None})
    if acked:
        async with asyncio.timeout(5):
            async for frame in client.messages():
                if frame == {"channelId": "1", "message": None}:
                    break
    await client.close()
    async with asyncio.timeout(5):
        await serving

    assert host.disconnected is disconnected


@pytest.mark.asyncio
async def test_a_channel_closed_for_a_full_inbox_does_not_report_a_lost_client(
    process_services: ProcessServices,
) -> None:
    client, transport = memory_transport_pair()
    channel = _ChannelTransport("1", transport)
    for request_id in range(_CHANNEL_INBOX_SIZE + 1):
        channel.receive({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/list",
            "params": {},
        })
    host = _ClientLossHost()
    draining = asyncio.create_task(_drain(client))

    async with asyncio.timeout(5):
        await _server_over(channel, host, process_services).serve()
    draining.cancel()

    assert host.disconnected is False


async def _drain(transport: JsonRpcTransport) -> None:
    async for _ in transport.messages():
        pass


@posix_only
@pytest.mark.asyncio
async def test_sigterm_ends_the_input_then_exits_once_serving_has_wound_down() -> None:
    _, transport = memory_transport_pair()
    exits: list[int] = []
    previous = signal.getsignal(signal.SIGTERM)
    steps: list[str] = []

    with ending_on_sigterm(transport, exit_process=exits.append) as ending:
        os.kill(os.getpid(), signal.SIGTERM)
        async with asyncio.timeout(5):
            async for _ in ending.messages():
                pass
        steps.append("input ended")
        assert exits == []

    assert steps == ["input ended"]
    assert exits == [TERMINATED_EXIT_CODE]
    assert signal.getsignal(signal.SIGTERM) is previous


@posix_only
@pytest.mark.asyncio
async def test_sigterm_delivered_to_another_thread_still_ends_the_input() -> None:
    _, transport = memory_transport_pair()
    exits: list[int] = []
    woken_by_fallback: list[bool] = []

    with ending_on_sigterm(transport, exit_process=exits.append) as ending:
        asyncio.get_running_loop().call_later(5, woken_by_fallback.append, True)
        sender = sigterm_another_thread_once_the_loop_waits()
        async for _ in ending.messages():
            pass
        sender.join()

    assert woken_by_fallback == []
    assert exits == [TERMINATED_EXIT_CODE]


@posix_only
@pytest.mark.asyncio
async def test_a_shutdown_stuck_past_the_deadline_exits_anyway() -> None:
    _, transport = memory_transport_pair()
    exited = threading.Event()

    def exit_process(code: int) -> None:
        assert code == TERMINATED_EXIT_CODE
        exited.set()

    with ending_on_sigterm(transport, deadline_s=0.05, exit_process=exit_process):
        os.kill(os.getpid(), signal.SIGTERM)
        # A shutdown that never ends.
        await _wait_for(exited.is_set)


def _handle_with_a_second_signal_at(point: int, handler: Any) -> bool:
    """Run ``handler`` as for a SIGTERM, handling another one at its
    ``point``-th line, as Python does when it lands mid-handler. False once
    ``point`` is past the handler's last line. What the handlers raise lands in
    whatever the main thread was running, as a signal's does, and is dropped.
    """
    lines = 0
    nested = False

    def trace(_frame: object, event: str, _arg: object) -> Any:
        nonlocal lines, nested
        if event == "line" and not nested:
            if lines == point:
                nested = True
                handler(signal.SIGTERM, None)
            lines += 1
        return trace

    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        handler(signal.SIGTERM, None)
    except Exception:
        pass
    finally:
        sys.settrace(previous)
    return nested


@posix_only
@pytest.mark.asyncio
async def test_a_sigterm_landing_while_the_first_is_handled_still_ends_and_exits() -> (
    None
):
    """As a process-group kill does under uv, which forwards its own SIGTERM."""

    async def nest_at(point: int) -> bool:
        _, transport = memory_transport_pair()
        exited = threading.Event()
        steps: list[str] = []
        # Its shutdown failing is swallowed once SIGTERM came: hence the steps.
        with ending_on_sigterm(
            transport, deadline_s=0.05, exit_process=lambda _code: exited.set()
        ) as ending:
            nested = _handle_with_a_second_signal_at(
                point, signal.getsignal(signal.SIGTERM)
            )
            async with asyncio.timeout(2):
                async for _ in ending.messages():
                    pass
            steps.append("input ended")
            await _wait_for(exited.is_set, timeout=2)
            steps.append("deadline passed")
        assert steps == ["input ended", "deadline passed"], point
        return nested

    point = 0
    while await nest_at(point):
        point += 1


@posix_only
@pytest.mark.asyncio
async def test_without_sigterm_serving_ends_without_exiting() -> None:
    _, transport = memory_transport_pair()
    exits: list[int] = []

    with ending_on_sigterm(transport, exit_process=exits.append):
        pass

    assert exits == []


class _EchoUntilEnd:
    def __init__(self, transport: JsonRpcTransport) -> None:
        self.transport = transport
        self.closed = False

    async def serve(self) -> None:
        async for _ in self.transport.messages():
            pass
        self.closed = True


@posix_only
@pytest.mark.asyncio
async def test_a_sigtermed_stdio_server_closes_as_on_the_end_of_its_stdin(
    monkeypatch: pytest.MonkeyPatch, stdin: OpenStdin
) -> None:
    """Its stdin still open, as a dead client's may not be closed yet."""
    servers: list[_EchoUntilEnd] = []

    async def create_harness_server(
        transport: JsonRpcTransport, **_: object
    ) -> _EchoUntilEnd:
        servers.append(_EchoUntilEnd(transport))
        return servers[-1]

    exits: list[int] = []
    monkeypatch.setattr(
        "vibe.app_server._runtime.create_harness_server", create_harness_server
    )
    monkeypatch.setattr(_termination, "_exit_now", exits.append)
    serving = asyncio.create_task(
        stdio.serve_stdio(reader=stdin, writer=BytesIO(), end_on_sigterm=True)
    )
    await _wait_for(lambda: servers)

    os.kill(os.getpid(), signal.SIGTERM)

    async with asyncio.timeout(5):
        await serving
    assert servers[0].closed
    assert exits == [TERMINATED_EXIT_CODE]
    # The real process exits instead: the loop would wait for the read.
    stdin.released.set()


_FAKE_MODEL_CONFIG = """
active_model = "fake-model"
include_project_context = false
[experiments]
enable = false
[telemetry]
enable = false
[[providers]]
name = "fake"
api_base = "http://127.0.0.1:1/v1"
api_key_env_var = "TEST_FAKE_API_KEY"
api_style = "openai"
[[models]]
name = "fake-model"
provider = "fake"
alias = "fake-model"
thinking = "off"
"""


@pytest.mark.asyncio
async def test_input_ending_while_a_stop_saves_the_session_ends_serving_cleanly(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    monkeypatch.setenv("TEST_FAKE_API_KEY", "test")
    (config_dir / "config.toml").write_text(_FAKE_MODEL_CONFIG)
    client, transport = memory_transport_pair()
    server = await create_harness_server(
        transport,
        transport_kind="stdio",
        process=HarnessProcess(services=process_services),
    )
    serving = asyncio.create_task(server.serve())
    incoming = client.messages()

    async def request(
        request_id: int, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        await client.send({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })
        async for message in incoming:
            if message.get("id") == request_id:
                assert "error" not in message, message
                return message["result"]
        raise AssertionError("Connection closed before responding")

    await request(1, "initialize", {"clientInfo": {"name": "test", "version": "1"}})
    await client.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    started = await request(2, "session/start", {"agentConfig": {"cwd": str(tmp_path)}})
    persist = adapter_module.persist_metadata
    saving = asyncio.Event()

    async def hold_first_save(metadata: Any, session_dir: Path) -> None:
        if not saving.is_set():
            saving.set()
            await asyncio.Event().wait()
        await persist(metadata, session_dir)

    monkeypatch.setattr(adapter_module, "persist_metadata", hold_first_save)
    # As a session with a turn to save.
    monkeypatch.setattr(
        adapter_module.UnifiedHarnessBackendAdapter, "_persists_to_disk", lambda _: True
    )
    await client.send({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "session/stop",
        "params": {"sessionId": started["state"]["session"]["id"]},
    })
    await asyncio.wait_for(saving.wait(), timeout=5)

    # As the TUI does once its wait for the stop runs out.
    await client.close()

    await asyncio.wait_for(serving, timeout=10)
