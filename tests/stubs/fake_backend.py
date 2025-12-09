from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Iterable

from tests.mock.utils import mock_llm_chunk
from vibe.core.types import LLMChunk, LLMMessage


class FakeBackend:
    """Minimal async backend stub to drive Agent.act without network.

    Provide a finite sequence of LLMResult objects to be returned by
    `complete`. When exhausted, returns an empty assistant message.
    """

    def __init__(
        self,
        results: Iterable[LLMChunk] | None = None,
        *,
        token_counter: Callable[[list[LLMMessage]], int] | None = None,
        exception_to_raise: Exception | None = None,
    ) -> None:
        self._chunks = list(results or [])
        self._requests_messages: list[list[LLMMessage]] = []
        self._requests_extra_headers: list[dict[str, str] | None] = []
        self._count_tokens_calls: list[list[LLMMessage]] = []
        self._token_counter = token_counter or self._default_token_counter
        self._exception_to_raise = exception_to_raise

    @property
    def requests_messages(self) -> list[list[LLMMessage]]:
        return self._requests_messages

    @property
    def requests_extra_headers(self) -> list[dict[str, str] | None]:
        return self._requests_extra_headers

    @staticmethod
    def _default_token_counter(messages: list[LLMMessage]) -> int:
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
    ) -> LLMChunk:
        if self._exception_to_raise:
            raise self._exception_to_raise

        self._requests_messages.append(messages)
        self._requests_extra_headers.append(extra_headers)
        if self._chunks:
            chunk = self._chunks.pop(0)
            if not self._chunks:
                chunk = chunk.model_copy(update={"finish_reason": "stop"})
            return chunk
        return mock_llm_chunk(content="", finish_reason="stop")

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
    ) -> AsyncGenerator[LLMChunk]:
        if self._exception_to_raise:
            raise self._exception_to_raise

        self._requests_messages.append(messages)
        self._requests_extra_headers.append(extra_headers)
        has_final_chunk = False
        while self._chunks:
            chunk = self._chunks.pop(0)
            is_last_provided_chunk = not self._chunks
            if is_last_provided_chunk:
                chunk = chunk.model_copy(update={"finish_reason": "stop"})

            if chunk.finish_reason is not None:
                has_final_chunk = True

            yield chunk
            if has_final_chunk:
                break

        if not has_final_chunk:
            yield mock_llm_chunk(content="", finish_reason="stop")

    async def count_tokens(
        self,
        *,
        model,
        messages,
        temperature=0.0,
        tools,
        tool_choice=None,
        extra_headers,
    ) -> int:
        self._count_tokens_calls.append(list(messages))
        return self._token_counter(messages)
