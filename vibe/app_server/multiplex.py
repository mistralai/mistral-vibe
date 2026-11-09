"""Many isolated app-server connections over one stdio stream.

Each frame is ``{"channelId": str, "message": object | null}``; a ``null``
message closes the channel, and the server echoes the close once the channel's
server has flushed, with ``"error"`` when it failed.

The first frame of a channel may also carry ``"open"``: the options of the one
run the channel serves, for ``vibe run-server``. ``{"runId": str,
"agentSocket": str, "config": object | null, "credentials": {str: str}}``.
``open`` on any other frame is a protocol error. A malformed frame ends the
whole stream, and its error names fields only, never their values.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from typing import Any, Protocol

from pydantic import Field, StrictStr, ValidationError

from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.transport import JsonRpcTransport
from vibe.observability.logging import app_server_channel, headless_run, logger
from vibe.utils.secrets import redact


class ChannelOpen(ProtocolModel):
    """The options of the run a channel serves."""

    run_id: StrictStr = Field(min_length=1)
    agent_socket: StrictStr = Field(min_length=1)
    # Stands in for the user config file; None reads this host's.
    config: dict[str, Any] | None = None
    # By environment variable name, as a provider's ``api_key_env_var`` names it.
    credentials: dict[StrictStr, StrictStr] = Field(default_factory=dict)


class ChannelFrame(ProtocolModel):
    channel_id: StrictStr = Field(pattern=r"^[1-9][0-9]{0,15}$")
    message: dict[str, Any] | None
    open: ChannelOpen | None = None


class ChannelProtocolError(ValueError):
    """A frame the multiplexed stream cannot carry. Names fields, not values."""


class ChannelServer(Protocol):
    async def serve(self) -> None: ...


type ServerFactory = Callable[
    [JsonRpcTransport, ChannelOpen | None], Awaitable[ChannelServer]
]


def parse_frame(value: object) -> ChannelFrame:
    """Validate one frame. Raises :class:`ChannelProtocolError` naming the
    fields at fault: a pydantic error would echo their values, credentials
    among them.
    """
    try:
        return validate_wire(ChannelFrame, value)
    except ValidationError as e:
        issues = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(frame)'}: "
            f"{error['msg']}"
            for error in e.errors(include_input=False, include_url=False)
        )
        raise ChannelProtocolError(f"Malformed channel frame: {issues}") from None


@contextmanager
def _run_context(options: ChannelOpen | None) -> Iterator[None]:
    """The run's credentials and log tag, for everything the channel runs."""
    with ExitStack() as stack:
        if options is not None:
            from vibe.utils.api_keys import session_credentials

            stack.enter_context(session_credentials(options.credentials))
            # The channel's own task: nothing outside it sees the tag.
            headless_run.set(options.run_id)
        yield


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

    async def serve_channel(
        channel_id: str, channel: _ChannelTransport, options: ChannelOpen | None
    ) -> None:
        app_server_channel.set(channel_id)
        close: dict[str, Any] = {"channelId": channel_id, "message": None}
        try:
            # Redacted while the run's credentials are still held.
            with _run_context(options):
                try:
                    server = await create_server(channel, options)
                    await server.serve()
                except Exception as exc:
                    # One failing session must not cancel its peers through the
                    # task group.
                    logger.exception("App-server channel failed")
                    close["error"] = redact(str(exc) or type(exc).__name__)
        finally:
            await channel.close()
            channels.pop(channel_id, None)
            # Acknowledge only after the harness has flushed and released its lease.
            await transport.send(close)

    try:
        async with asyncio.TaskGroup() as tasks:
            async for value in transport.messages():
                frame = parse_frame(value)
                channel = channels.get(frame.channel_id)
                opens_channel = (
                    channel is None and int(frame.channel_id) > last_channel_id
                )
                if frame.open is not None and (
                    not opens_channel or frame.message is None
                ):
                    raise ChannelProtocolError(
                        f"Channel {frame.channel_id}: open is allowed only on "
                        "the frame that opens the channel, with its first message"
                    )
                if channel is None:
                    if not opens_channel:
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
                    tasks.create_task(
                        serve_channel(frame.channel_id, channel, frame.open)
                    )
                channel.receive(frame.message)
            # EOF closes all channels; each server owns and flushes its runtime.
            for channel in tuple(channels.values()):
                channel.receive(None)
    finally:
        await transport.close()
