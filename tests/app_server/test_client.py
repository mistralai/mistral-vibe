from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import pytest

from vibe.app_server.client import AppServerClient

_BURST_OVER_ANY_QUEUE_BOUND = 400


class ScriptedTransport:
    def __init__(self) -> None:
        self.inbound: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.request_sent = asyncio.Event()

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        if "id" in message:
            self.request_sent.set()

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while (message := await self.inbound.get()) is not None:
            yield message

    async def close(self) -> None:
        await self.inbound.put(None)


@pytest.mark.asyncio
async def test_reader_answers_a_consumer_request_behind_an_unread_burst() -> None:
    transport = ScriptedTransport()
    client = AppServerClient(transport)
    resynced = asyncio.Event()

    async def pump() -> None:
        # `AppServerSession._pump_messages` is the only consumer of `incoming()`
        # and resyncs mid-message, so nothing drains while the request is out.
        async for _ in client.incoming():
            if resynced.is_set():
                continue
            await client.request("session/read")
            resynced.set()

    await client.start()
    pump_task = asyncio.create_task(pump())
    try:
        for index in range(_BURST_OVER_ANY_QUEUE_BOUND):
            await transport.inbound.put({
                "jsonrpc": "2.0",
                "method": "turn/started",
                "params": {"index": index},
            })
        await asyncio.wait_for(transport.request_sent.wait(), timeout=2)
        request_id = next(
            message["id"] for message in transport.sent if "id" in message
        )
        await transport.inbound.put({"jsonrpc": "2.0", "id": request_id, "result": {}})

        await asyncio.wait_for(resynced.wait(), timeout=2)
    finally:
        pump_task.cancel()
        with suppress(asyncio.CancelledError):
            await pump_task
        await client.close()
