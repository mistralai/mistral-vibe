from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from typing import Any, cast

from tests.mock.utils import mock_llm_chunk
from vibe.core.llm.exceptions import BackendError, PayloadSummary
from vibe.core.types import LLMChunk, LLMMessage, Role
from vibe.core.utils import RetryObserver, RetryReason


class FakeBackend:
    """Minimal async backend stub to drive Agent.act without network.

    Provide a finite sequence of LLMResult objects to be returned by
    `complete`. When exhausted, returns an empty assistant message.
    """

    def __init__(
        self,
        chunks: LLMChunk
        | Iterable[LLMChunk]
        | Iterable[Iterable[LLMChunk]]
        | None = None,
        *,
        exception_to_raise: Exception | None = None,
        retries_before_response: int = 0,
    ) -> None:
        """Fake backend that will output the given chunks in the order they are given.

        chunks: A single chunk, a sequence of chunks, or a sequence of sequences of chunks.
        A single chunk would be outputted as such in complete / complete_streaming
        A sequence of chunks will is considered a single stream: a completion would output
        all chunks (either streaming or in an aggregated way)
        A sequence of sequences of chunks is considered a list of streams: each completion
        will output a stream (either streaming or in an aggregated way)
        """
        self._requests_messages: list[list[LLMMessage]] = []
        self._requests_extra_headers: list[dict[str, str] | None] = []
        self._requests_metadata: list[dict[str, str] | None] = []
        self._requests_max_tokens: list[int | None] = []
        self._requests_tools: list[Any] = []
        self._requests_tool_choices: list[Any] = []
        self._exception_to_raise = exception_to_raise
        self._retries_before_response = retries_before_response
        self.on_retry: RetryObserver | None = None

        self._streams: list[list[LLMChunk]]
        if chunks is None:
            self._streams = []
            return
        if isinstance(chunks, LLMChunk):
            self._streams = [[chunks]]
            return
        if all(isinstance(chunk, LLMChunk) for chunk in chunks):
            self._streams = [[cast(LLMChunk, chunk) for chunk in chunks]]
            return
        if any(isinstance(chunk, LLMChunk) for chunk in chunks):
            raise TypeError(
                f"Invalid type for chunks, expected a value of type "
                f"LLMChunk | Iterable[LLMChunk] | Iterable[Iterable[LLMChunk]], got {chunks!r}"
            )
        chunks = cast(Iterable[Iterable[LLMChunk]], chunks)
        self._streams = [[chunk for chunk in stream] for stream in chunks]

    @property
    def requests_messages(self) -> list[list[LLMMessage]]:
        return self._requests_messages

    @property
    def requests_extra_headers(self) -> list[dict[str, str] | None]:
        return self._requests_extra_headers

    @property
    def requests_metadata(self) -> list[dict[str, str] | None]:
        return self._requests_metadata

    @property
    def requests_max_tokens(self) -> list[int | None]:
        return self._requests_max_tokens

    @property
    def requests_tools(self) -> list[Any]:
        return self._requests_tools

    @property
    def requests_tool_choices(self) -> list[Any]:
        return self._requests_tool_choices

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    async def notice_retries(self) -> None:
        """Report `retries_before_response` retries, as a real backend would."""
        if self.on_retry is None:
            return
        for _ in range(self._retries_before_response):
            await self.on_retry(RetryReason.from_http_status(429))

    async def complete(
        self,
        *,
        model,
        messages,
        temperature,
        tools,
        tool_choice,
        extra_headers,
        max_tokens,
        metadata=None,
    ) -> LLMChunk:
        if self._exception_to_raise:
            raise self._exception_to_raise

        await self.notice_retries()

        self._requests_messages.append(list(messages))
        self._requests_extra_headers.append(extra_headers)
        self._requests_metadata.append(metadata)
        self._requests_max_tokens.append(max_tokens)
        self._requests_tools.append(tools)
        self._requests_tool_choices.append(tool_choice)

        if self._streams:
            stream = self._streams.pop(0)
            chunk_agg = LLMChunk(message=LLMMessage(role=Role.assistant))
            for chunk in stream:
                chunk_agg += chunk
            return chunk_agg

        return mock_llm_chunk(content="")

    async def complete_streaming(
        self,
        *,
        model,
        messages,
        temperature,
        tools,
        tool_choice,
        extra_headers,
        max_tokens,
        metadata=None,
    ) -> AsyncGenerator[LLMChunk]:
        if self._exception_to_raise:
            raise self._exception_to_raise

        await self.notice_retries()

        self._requests_messages.append(list(messages))
        self._requests_extra_headers.append(extra_headers)
        self._requests_metadata.append(metadata)
        self._requests_max_tokens.append(max_tokens)
        self._requests_tools.append(tools)
        self._requests_tool_choices.append(tool_choice)

        if self._streams:
            stream = list(self._streams.pop(0))
        else:
            stream = [mock_llm_chunk(content="")]
        for chunk in stream:
            yield chunk


class FakeInterruptedStreamingBackend(FakeBackend):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.streaming_attempts = 0

    async def complete_streaming(self, **kwargs: Any) -> AsyncGenerator[LLMChunk]:
        self.streaming_attempts += 1
        async for chunk in super().complete_streaming(**kwargs):
            yield chunk
            if self.streaming_attempts == 1:
                raise BackendError(
                    provider="test",
                    endpoint="https://example.test/chat",
                    status=None,
                    reason="network boom",
                    headers=None,
                    body_text=None,
                    parsed_error="Network error",
                    model="test",
                    payload_summary=PayloadSummary(
                        model="test",
                        message_count=0,
                        approx_chars=0,
                        temperature=0,
                        has_tools=False,
                        tool_choice=None,
                    ),
                )
