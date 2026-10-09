"""Stall detection for provider response streams.

A transport read timeout has to cover the wait for the first token, which can be
long for a large prompt. Providers may send preamble items before that token,
such as SSE ``event:`` lines, heartbeats, or a role-only first event, so the
arrival of a transport item does not mean the model has started answering.

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
            f"The model stopped sending output: nothing received for "
            f"{silence_s:g}s after the response started."
        )
        self.silence_s = silence_s


class StreamIdleGuard:
    """Fails one response stream that goes quiet after the model's first output.

    The deadline is armed by ``output_started``, or by ``watch``'s
    ``starts_output`` predicate when the transport items themselves show model
    output. Once armed, every transport item resets it, so heartbeats keep a
    slow but live stream going. An ``idle_timeout_s`` of ``None`` applies no
    deadline of its own; ``read_timeout_s`` is the transport's read timeout,
    reported when that timeout ends the stream.
    """

    def __init__(self, idle_timeout_s: float | None, *, read_timeout_s: float) -> None:
        self.idle_timeout_s = idle_timeout_s
        self.read_timeout_s = read_timeout_s
        self._armed = False

    def output_started(self) -> None:
        self._armed = True

    async def watch[T](
        self, source: AsyncIterator[T], starts_output: Callable[[T], bool] | None = None
    ) -> AsyncGenerator[T]:
        """Yield transport items from ``source``, enforcing the armed deadline."""
        iterator = aiter(source)
        while True:
            try:
                item = await self._next(iterator)
            except StopAsyncIteration:
                return
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
        idle_timeout_s = self.idle_timeout_s
        if not self._armed or idle_timeout_s is None:
            return await anext(iterator)
        deadline = asyncio.timeout(idle_timeout_s)
        try:
            async with deadline:
                return await anext(iterator)
        except TimeoutError as error:
            if not deadline.expired():
                raise
            raise ProviderStreamStalled(idle_timeout_s) from error


__all__ = ["ProviderStreamStalled", "StreamIdleGuard"]
