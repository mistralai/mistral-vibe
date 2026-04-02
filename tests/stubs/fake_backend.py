from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Iterable, Sequence
from typing import cast

from tests.mock.utils import mock_llm_chunk
from vibe.core.types import LLMChunk, LLMMessage, Role


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
        token_counter: Callable[[Sequence[LLMMessage]], int] | None = None,
        exception_to_raise: Exception | None = None,
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
        self._count_tokens_calls: list[list[LLMMessage]] = []
        self._token_counter = token_counter or self._default_token_counter
        self._exception_to_raise = exception_to_raise

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

    @staticmethod
    def _default_token_counter(messages: Sequence[LLMMessage]) -> int:
        return 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

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

        self._requests_messages.append(messages)
        self._requests_extra_headers.append(extra_headers)
        self._requests_metadata.append(metadata)

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

        self._requests_messages.append(messages)
        self._requests_extra_headers.append(extra_headers)
        self._requests_metadata.append(metadata)

        if self._streams:
            stream = list(self._streams.pop(0))
        else:
            stream = [mock_llm_chunk(content="")]
        for chunk in stream:
            yield chunk

    async def count_tokens(
        self,
        *,
        model,
        messages,
        temperature=0.0,
        tools,
        tool_choice=None,
        extra_headers,
        metadata=None,
    ) -> int:
        self._count_tokens_calls.append(list(messages))
        return self._token_counter(messages)
