from __future__ import annotations

import asyncio
from contextlib import suppress
import signal
import sys
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.stubs.thread_signals import sigterm_another_thread_once_the_loop_waits
from vibe.acp import agent as agent_module


@pytest.mark.asyncio
async def test_acp_agent_closes_when_protocol_stream_reaches_eof(monkeypatch) -> None:
    run_agent = AsyncMock()
    close = AsyncMock()
    agent: Any = SimpleNamespace(close=close)
    monkeypatch.setattr(agent_module, "run_agent", run_agent)

    await agent_module._serve_acp_agent(agent)

    run_agent.assert_awaited_once()
    close.assert_awaited_once()


class _AgentWithMessagePump:
    def __init__(self, **_: Any) -> None:
        self.pump: asyncio.Task[None] | None = None
        self.closes = 0

    async def close(self) -> None:
        # Like the app-server session, closing waits for its message pump.
        if self.pump is not None:
            self.pump.cancel()
            with suppress(asyncio.CancelledError):
                await self.pump
        self.closes += 1


def test_sigterm_received_while_another_task_runs_still_ends_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents: list[_AgentWithMessagePump] = []
    server_ended = threading.Event()

    def build_agent(**kwargs: Any) -> _AgentWithMessagePump:
        agents.append(_AgentWithMessagePump(**kwargs))
        return agents[-1]

    async def pump_receiving_sigterm() -> None:
        signal.raise_signal(signal.SIGTERM)
        await asyncio.Event().wait()

    async def run_agent(*, agent: _AgentWithMessagePump, **_: Any) -> None:
        server = asyncio.current_task()
        assert server is not None
        # Worker threads (anyio's, for one) stop when this task ends.
        server.add_done_callback(lambda _: server_ended.set())
        agent.pump = asyncio.create_task(pump_receiving_sigterm())
        await asyncio.Event().wait()

    monkeypatch.setattr(agent_module, "VibeAcpAgent", build_agent)
    monkeypatch.setattr(agent_module, "run_agent", run_agent)
    previous_handler = signal.getsignal(signal.SIGTERM)

    try:
        agent_module.run_acp_server()
    except KeyboardInterrupt:
        pytest.fail("SIGTERM escaped the server as a KeyboardInterrupt")

    assert server_ended.is_set()
    assert agents[0].closes == 1
    assert signal.getsignal(signal.SIGTERM) is previous_handler


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigterm_delivered_to_another_thread_still_ends_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents: list[_AgentWithMessagePump] = []
    woken_by_fallback: list[bool] = []

    def build_agent(**kwargs: Any) -> _AgentWithMessagePump:
        agents.append(_AgentWithMessagePump(**kwargs))
        return agents[-1]

    async def run_agent(**_: Any) -> None:
        asyncio.get_running_loop().call_later(5, woken_by_fallback.append, True)
        sender = sigterm_another_thread_once_the_loop_waits()
        try:
            await asyncio.Event().wait()
        finally:
            sender.join()

    monkeypatch.setattr(agent_module, "VibeAcpAgent", build_agent)
    monkeypatch.setattr(agent_module, "run_agent", run_agent)
    previous_handler = signal.getsignal(signal.SIGTERM)

    agent_module.run_acp_server()

    assert woken_by_fallback == []
    assert agents[0].closes == 1
    assert signal.getsignal(signal.SIGTERM) is previous_handler
