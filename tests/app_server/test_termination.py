from __future__ import annotations

import asyncio
from collections.abc import Iterator
from io import BytesIO
import os
import signal
import sys
import threading
from typing import Any

import pytest

from vibe.app_server import _termination, stdio
from vibe.app_server._termination import (
    TERMINATED_EXIT_CODE,
    InputEndingTransport,
    ending_on_sigterm,
)
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair

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
    monkeypatch.setattr(stdio, "create_harness_server", create_harness_server)
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
