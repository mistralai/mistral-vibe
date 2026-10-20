"""Stall detection for provider response streams.

A transport read timeout has to cover the wait for the first token, which can be
long for a large prompt. Providers may send preamble items before that token,
such as SSE ``event:`` lines, heartbeats, or a role-only first event, so the
arrival of a transport item does not mean the model has started answering.

Heartbeats also reset the transport read timeout, so a provider that keeps a
response open with heartbeats alone would never time out. The guard below
applies the read timeout again, to the silence since the latest response data,
from the start of the stream: heartbeats do not reset it.

Once the model has produced output, text and reasoning arrive in steady
increments, so a long silence usually means the provider has stalled. The guard
below applies a shorter deadline between transport items, but only after the
model's first output. It is off unless configured: some providers send a tool
call in one piece once it is fully generated, so a large tool call is also a
long silence.

A transport read timeout while the stream is read is reported as a stall too,
whether or not the idle deadline is configured, and whether or not the model
has produced output yet: the response has started, so the provider is
generating, and retrying repeats the same silent generation. The retry policy
limits how often a stall is retried. A timeout waiting for the response itself
happens before the stream is read and is not a stall.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable

import httpx


class ProviderStreamStalled(httpx.ReadTimeout):
    """A started response stream that stopped producing output.

    A read timeout, so retry policies that already retry one treat a stall the
    same way.
    """

    def __init__(self, silence_s: float) -> None:
        super().__init__(
            f"The model stopped sending output: no response data for "
            f"{silence_s:g}s after the response started."
        )
        self.silence_s = silence_s


class StreamIdleGuard:
    """Fails one response stream that stops sending response data.

    ``read_timeout_s`` bounds the silence since the latest item that is not a
    heartbeat, as told by ``watch``'s ``is_heartbeat`` predicate. It applies
    from the start of the stream, so a provider that keeps a response open
    with heartbeats alone fails as it would without them.

    ``idle_timeout_s`` is a shorter deadline, armed by ``output_started`` or by
    ``watch``'s ``starts_output`` predicate when the transport items themselves
    show model output. Once armed, every transport item resets it, so
    heartbeats keep a slow but live stream going. ``None`` applies no idle
    deadline.
    """

    def __init__(self, idle_timeout_s: float | None, *, read_timeout_s: float) -> None:
        self.idle_timeout_s = idle_timeout_s
        self.read_timeout_s = read_timeout_s
        self._armed = False
        self._last_data_at: float | None = None

    def output_started(self) -> None:
        self._armed = True

    async def watch[T](
        self,
        source: AsyncIterator[T],
        starts_output: Callable[[T], bool] | None = None,
        is_heartbeat: Callable[[T], bool] | None = None,
    ) -> AsyncGenerator[T]:
        """Yield transport items from ``source``, enforcing the deadlines."""
        iterator = aiter(source)
        while True:
            try:
                item = await self._next(iterator)
            except StopAsyncIteration:
                return
            if is_heartbeat is None or not is_heartbeat(item):
                self._last_data_at = asyncio.get_running_loop().time()
            if starts_output is not None and starts_output(item):
                self.output_started()
            yield item

    async def _next[T](self, iterator: AsyncIterator[T]) -> T:
        try:
            return await self._next_within_deadline(iterator)
        except httpx.ReadTimeout as error:
            if isinstance(error, ProviderStreamStalled):
                raise
            raise ProviderStreamStalled(self.read_timeout_s) from error

    async def _next_within_deadline[T](self, iterator: AsyncIterator[T]) -> T:
        now = asyncio.get_running_loop().time()
        if self._last_data_at is None:
            self._last_data_at = now
        expires_at = self._last_data_at + self.read_timeout_s
        silence_s = self.read_timeout_s
        idle_timeout_s = self.idle_timeout_s
        if self._armed and idle_timeout_s is not None:
            if now + idle_timeout_s < expires_at:
                expires_at = now + idle_timeout_s
                silence_s = idle_timeout_s
        deadline = asyncio.timeout_at(expires_at)
        try:
            async with deadline:
                return await anext(iterator)
        except TimeoutError as error:
            if not deadline.expired():
                raise
            raise ProviderStreamStalled(silence_s) from error


__all__ = ["ProviderStreamStalled", "StreamIdleGuard"]
