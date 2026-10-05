from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
import logging
from typing import Any

import pytest

from vibe.app_server.multiplex import ChannelServer, serve_multiplexed
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair
from vibe.observability.logging import StructuredLogFormatter, logger


@asynccontextmanager
async def running_channels() -> AsyncIterator[
    tuple[JsonRpcTransport, AsyncIterator[dict[str, Any]], list[JsonRpcTransport]]
]:
    client, server = memory_transport_pair()
    opened: list[JsonRpcTransport] = []

    async def create_channel(transport: JsonRpcTransport) -> ChannelServer:
        opened.append(transport)

        class EchoServer:
            async def serve(self) -> None:
                try:
                    async for message in transport.messages():
                        await transport.send(message)
                finally:
                    await transport.close()

        return EchoServer()

    task = asyncio.create_task(serve_multiplexed(server, create_channel))
    try:
        yield client, client.messages(), opened
    finally:
        await client.close()
        await task


async def _stop_server(task: asyncio.Task[None], client: JsonRpcTransport) -> None:
    async def drain() -> None:
        async for _ in client.messages():
            pass

    # A failing assertion may leave a full outbox. Keep draining while cleanup
    # flushes its close frames so the regression fails instead of hanging.
    draining = asyncio.create_task(drain())
    task.cancel()
    try:
        with suppress(asyncio.CancelledError):
            await task
    finally:
        await draining


@pytest.mark.asyncio
async def test_channels_isolate_overlapping_request_and_callback_ids() -> None:
    async with running_channels() as (client, messages, opened):
        frames = [
            {"channelId": channel, "message": message}
            for channel in ("1", "2")
            for message in (
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": "callback-1", "result": {"owner": channel}},
            )
        ]
        for frame in frames:
            await client.send(frame)
        received = [await anext(messages) for _ in frames]
        for channel in ("1", "2"):
            assert [f for f in received if f["channelId"] == channel] == [
                f for f in frames if f["channelId"] == channel
            ]
        assert len(opened) == 2


@pytest.mark.asyncio
async def test_closing_a_channel_keeps_other_channels_and_the_process_available() -> (
    None
):
    async with running_channels() as (client, messages, _):
        for channel in ("1", "2"):
            await client.send({"channelId": channel, "message": {"id": 1}})
            assert (await anext(messages))["channelId"] == channel
        await client.send({"channelId": "1", "message": None})
        assert await anext(messages) == {"channelId": "1", "message": None}
        for channel in ("2", "3"):
            frame = {"channelId": channel, "message": {"id": 2}}
            await client.send(frame)
            assert await anext(messages) == frame


@pytest.mark.asyncio
async def test_close_acknowledgement_waits_for_runtime_cleanup() -> None:
    client, transport = memory_transport_pair()
    cleaning = asyncio.Event()
    release = asyncio.Event()

    async def create_channel(channel: JsonRpcTransport) -> ChannelServer:
        class SlowClosingServer:
            async def serve(self) -> None:
                async for _ in channel.messages():
                    pass
                cleaning.set()
                await release.wait()

        return SlowClosingServer()

    serving = asyncio.create_task(serve_multiplexed(transport, create_channel))
    response = asyncio.ensure_future(anext(client.messages()))
    try:
        await client.send({"channelId": "1", "message": {"id": 1}})
        await client.send({"channelId": "1", "message": None})
        await cleaning.wait()
        assert not response.done()
        release.set()
        assert await response == {"channelId": "1", "message": None}
    finally:
        release.set()
        await client.close()
        await serving


@pytest.mark.asyncio
async def test_eof_flushes_and_closes_every_channel() -> None:
    async with running_channels() as (client, messages, opened):
        for channel in ("1", "2"):
            await client.send({"channelId": channel, "message": {"id": 1}})
            await anext(messages)
        await client.close()
        closed = [await anext(messages), await anext(messages)]
        assert {frame["channelId"] for frame in closed} == {"1", "2"}
        assert all(frame["message"] is None for frame in closed)
        for channel in opened:
            with pytest.raises(RuntimeError, match="closed"):
                await channel.send({"id": 2})


@pytest.mark.asyncio
async def test_client_close_can_cross_server_initiated_close() -> None:
    client, transport = memory_transport_pair()

    async def create_channel(channel: JsonRpcTransport) -> ChannelServer:
        class SelfClosingServer:
            async def serve(self) -> None:
                await anext(channel.messages())

        return SelfClosingServer()

    serving = asyncio.create_task(serve_multiplexed(transport, create_channel))
    messages = client.messages()
    try:
        await client.send({"channelId": "1", "message": {"id": 1}})
        assert await anext(messages) == {"channelId": "1", "message": None}
        await client.send({"channelId": "1", "message": None})
        await client.send({"channelId": "2", "message": {"id": 1}})
        assert await anext(messages) == {"channelId": "2", "message": None}
    finally:
        await client.close()
        await serving


@pytest.mark.asyncio
async def test_late_frames_never_reopen_a_closed_channel() -> None:
    async with running_channels() as (client, messages, opened):
        for channel in ("1", "2"):
            await client.send({"channelId": channel, "message": {"id": 1}})
            await anext(messages)
        await client.send({"channelId": "1", "message": None})
        assert await anext(messages) == {"channelId": "1", "message": None}
        await client.send({"channelId": "1", "message": {"id": 2}})
        await client.send({"channelId": "1", "message": None})
        healthy = {"channelId": "2", "message": {"id": 3}}
        await client.send(healthy)
        assert await anext(messages) == healthy
        assert len(opened) == 2


@pytest.mark.asyncio
async def test_server_failure_closes_only_its_channel_with_the_reason() -> None:
    client, transport = memory_transport_pair()

    async def create_channel(channel: JsonRpcTransport) -> ChannelServer:
        class Server:
            async def serve(self) -> None:
                async for message in channel.messages():
                    if message.get("fail"):
                        raise RuntimeError("injected server failure")
                    await channel.send(message)

        return Server()

    serving = asyncio.create_task(serve_multiplexed(transport, create_channel))
    messages = client.messages()
    try:
        for channel in ("1", "2"):
            frame = {"channelId": channel, "message": {"id": 1}}
            await client.send(frame)
            assert await anext(messages) == frame
        await client.send({"channelId": "1", "message": {"fail": True}})
        assert await anext(messages) == {
            "channelId": "1",
            "message": None,
            "error": "injected server failure",
        }
        healthy = {"channelId": "2", "message": {"id": 2}}
        await client.send(healthy)
        assert await anext(messages) == healthy
        assert not serving.done()
    finally:
        await _stop_server(serving, client)


@pytest.mark.asyncio
async def test_channel_setup_failure_keeps_the_process_serving() -> None:
    client, transport = memory_transport_pair()
    opened = 0

    async def create_channel(channel: JsonRpcTransport) -> ChannelServer:
        nonlocal opened
        opened += 1
        if opened == 1:
            raise ValueError()

        class EchoServer:
            async def serve(self) -> None:
                async for message in channel.messages():
                    await channel.send(message)

        return EchoServer()

    serving = asyncio.create_task(serve_multiplexed(transport, create_channel))
    messages = client.messages()
    try:
        await client.send({"channelId": "1", "message": {"id": 1}})
        assert await anext(messages) == {
            "channelId": "1",
            "message": None,
            "error": "ValueError",
        }
        healthy = {"channelId": "2", "message": {"id": 1}}
        await client.send(healthy)
        assert await anext(messages) == healthy
    finally:
        await _stop_server(serving, client)


@pytest.mark.asyncio
async def test_closing_an_unknown_channel_is_ignored() -> None:
    async with running_channels() as (client, messages, opened):
        await client.send({"channelId": "5", "message": None})
        frame = {"channelId": "6", "message": {"id": 1}}
        await client.send(frame)
        assert await anext(messages) == frame
        assert len(opened) == 1


@pytest.mark.asyncio
async def test_channel_logs_name_their_channel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, transport = memory_transport_pair()

    async def create_channel(channel: JsonRpcTransport) -> ChannelServer:
        class LoggingServer:
            async def serve(self) -> None:
                async for message in channel.messages():
                    await asyncio.to_thread(logger.warning, "handled")
                    await channel.send(message)

        return LoggingServer()

    caplog.handler.setFormatter(StructuredLogFormatter())
    serving = asyncio.create_task(serve_multiplexed(transport, create_channel))
    messages = client.messages()
    try:
        with caplog.at_level(logging.WARNING, logger="vibe"):
            for channel in ("3", "4"):
                await client.send({"channelId": channel, "message": {"id": 1}})
                await anext(messages)
        assert [line.split(" ", 4)[4] for line in caplog.text.splitlines()] == [
            "channel=3 handled",
            "channel=4 handled",
        ]
    finally:
        await _stop_server(serving, client)


@pytest.mark.asyncio
@pytest.mark.parametrize("message_count", [256, 257, 1024])
async def test_saturated_channel_never_blocks_its_peers(message_count: int) -> None:
    client, transport = memory_transport_pair()
    release = asyncio.Event()
    opened = 0

    async def create_channel(channel: JsonRpcTransport) -> ChannelServer:
        nonlocal opened
        opened += 1
        stalled = opened == 1

        class Server:
            async def serve(self) -> None:
                if stalled:
                    # The consumer can exit during startup or cleanup without
                    # draining its inbox. Its peers must remain reachable.
                    await release.wait()
                    return
                async for message in channel.messages():
                    await channel.send(message)

        return Server()

    serving = asyncio.create_task(serve_multiplexed(transport, create_channel))
    messages = client.messages()
    try:
        async with asyncio.timeout(2):
            for index in range(message_count):
                await client.send({"channelId": "1", "message": {"id": index}})
            healthy = {"channelId": "2", "message": {"id": "before-close"}}
            await client.send(healthy)
            assert await anext(messages) == healthy

        release.set()
        assert await asyncio.wait_for(anext(messages), 1) == {
            "channelId": "1",
            "message": None,
        }
        healthy = {"channelId": "2", "message": {"id": "after-close"}}
        await client.send(healthy)
        assert await asyncio.wait_for(anext(messages), 1) == healthy
        assert not serving.done()
    finally:
        release.set()
        await _stop_server(serving, client)


@pytest.mark.asyncio
@pytest.mark.parametrize("close_kind", ["channel", "eof", "overflow"])
async def test_full_inbox_drains_admitted_messages_before_closing(
    close_kind: str,
) -> None:
    client, transport = memory_transport_pair()
    release = asyncio.Event()
    opened = 0

    async def create_channel(channel: JsonRpcTransport) -> ChannelServer:
        nonlocal opened
        opened += 1
        stalled = opened == 1

        class Server:
            async def serve(self) -> None:
                if stalled:
                    await release.wait()
                async for message in channel.messages():
                    await channel.send(message)

        return Server()

    serving = asyncio.create_task(serve_multiplexed(transport, create_channel))
    messages = client.messages()
    frames = [{"channelId": "1", "message": {"id": i}} for i in range(256)]
    try:
        for frame in frames:
            await client.send(frame)
        if close_kind == "eof":
            await client.close()
        else:
            await client.send({
                "channelId": "1",
                "message": {"id": "overflow"} if close_kind == "overflow" else None,
            })
            # The peer response proves the close/overflow was routed while the
            # first consumer was still paused, independent of task scheduling.
            healthy = {"channelId": "2", "message": {"id": "barrier"}}
            await client.send(healthy)
            assert await asyncio.wait_for(anext(messages), 1) == healthy
        release.set()
        for expected in [*frames, {"channelId": "1", "message": None}]:
            assert await asyncio.wait_for(anext(messages), 1) == expected
    finally:
        release.set()
        await _stop_server(serving, client)
