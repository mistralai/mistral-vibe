from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from pydantic import Field, StrictStr

from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.transport import JsonRpcTransport
from vibe.observability.logging import app_server_channel, logger


class ChannelFrame(ProtocolModel):
    channel_id: StrictStr = Field(pattern=r"^[1-9][0-9]{0,15}$")
    message: dict[str, Any] | None


class ChannelServer(Protocol):
    async def serve(self) -> None: ...


type ServerFactory = Callable[[JsonRpcTransport], Awaitable[ChannelServer]]

_CHANNEL_INBOX_SIZE = 256


class _ChannelTransport:
    def __init__(self, channel_id: str, transport: JsonRpcTransport) -> None:
        self._channel_id = channel_id
        self._transport = transport
        # Reserve a control slot so EOF never waits for a consumer to make room.
        self._incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            _CHANNEL_INBOX_SIZE + 1
        )
        self._closed = False
        self._finished = False

    async def send(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("App-server channel is closed")
        # The inner protocol already serialized its payload. Wrap it directly
        # instead of recursively copying every streamed event a second time.
        await self._transport.send({"channelId": self._channel_id, "message": message})

    def receive(self, message: dict[str, Any] | None) -> None:
        if self._finished:
            return
        if message is None or self._incoming.qsize() == _CHANNEL_INBOX_SIZE:
            # A saturated channel drains its admitted messages and closes. Never
            # make the shared reader wait on a session that may have stopped.
            self._finished = True
            self._incoming.put_nowait(None)
            return
        self._incoming.put_nowait(message)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while (message := await self._incoming.get()) is not None:
            yield message

    async def close(self) -> None:
        self._closed = True
        self.receive(None)


async def serve_multiplexed(
    transport: JsonRpcTransport, create_server: ServerFactory
) -> None:
    channels: dict[str, _ChannelTransport] = {}
    # Opening IDs increase monotonically. A watermark avoids retaining one
    # tombstone per closed connection for the entire desktop lifetime.
    last_channel_id = 0

    async def serve_channel(channel_id: str, channel: _ChannelTransport) -> None:
        app_server_channel.set(channel_id)
        close: dict[str, Any] = {"channelId": channel_id, "message": None}
        try:
            server = await create_server(channel)
            await server.serve()
        except Exception as exc:
            # One failing session must not cancel its peers through the task group.
            logger.exception("App-server channel failed")
            close["error"] = str(exc) or type(exc).__name__
        finally:
            await channel.close()
            channels.pop(channel_id, None)
            # Acknowledge only after the harness has flushed and released its lease.
            await transport.send(close)

    try:
        async with asyncio.TaskGroup() as tasks:
            async for value in transport.messages():
                frame = validate_wire(ChannelFrame, value)
                channel = channels.get(frame.channel_id)
                if channel is None:
                    if int(frame.channel_id) <= last_channel_id:
                        # session/stop can finish the server while client frames
                        # are still in flight. Its close already acknowledged them.
                        continue
                    if frame.message is None:
                        logger.warning(
                            "Ignoring close for unknown app-server channel %s",
                            frame.channel_id,
                        )
                        continue
                    last_channel_id = int(frame.channel_id)
                    channel = _ChannelTransport(frame.channel_id, transport)
                    channels[frame.channel_id] = channel
                    tasks.create_task(serve_channel(frame.channel_id, channel))
                channel.receive(frame.message)
            # EOF closes all channels; each server owns and flushes its runtime.
            for channel in tuple(channels.values()):
                channel.receive(None)
    finally:
        await transport.close()
