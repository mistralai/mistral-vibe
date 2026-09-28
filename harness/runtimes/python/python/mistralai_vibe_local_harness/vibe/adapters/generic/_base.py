"""Adapter contract and shared payload helpers.

An ``APIAdapter`` turns the provider-neutral message model into an HTTP request
and parses provider chunks back into :class:`LLMChunk` values.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
import json
from typing import Any, ClassVar, NamedTuple

from mistralai_vibe_local_harness.vibe.adapters.generic._model import (
    AvailableTool,
    LLMChunk,
    LLMMessage,
    StrToolChoice,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._provider import ProviderView

MODEL_HTTP_KEEPALIVE_EXPIRY_SECONDS = 60.0


def apply_reasoning_effort(payload: dict[str, Any], thinking: str) -> None:
    if thinking != "off":
        payload["reasoning_effort"] = thinking


def build_chat_payload(
    *,
    model_name: str,
    messages: list[dict[str, Any]],
    temperature: float,
    tools: list[AvailableTool] | None,
    max_tokens: int | None,
    tool_choice: StrToolChoice | AvailableTool | None,
    thinking: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
    }
    apply_reasoning_effort(payload, thinking)
    if tools:
        payload["tools"] = [tool.model_dump(exclude_none=True) for tool in tools]
    if tool_choice:
        payload["tool_choice"] = (
            tool_choice if isinstance(tool_choice, str) else tool_choice.model_dump()
        )
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


def build_auth_headers(api_key: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def finalize_chat_request(
    *,
    payload: dict[str, Any],
    enable_streaming: bool,
    stream_options: dict[str, Any],
    api_key: str | None,
    endpoint: str,
) -> PreparedRequest:
    if enable_streaming:
        payload["stream"] = True
        payload["stream_options"] = stream_options
    headers = build_auth_headers(api_key)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return PreparedRequest(endpoint, headers, body)


class PreparedRequest(NamedTuple):
    endpoint: str
    headers: dict[str, str]
    body: bytes
    base_url: str = ""


class ParsedStreamChunk(NamedTuple):
    data: dict[str, Any]
    chunk: LLMChunk


class APIAdapter(ABC):
    endpoint: ClassVar[str]

    @abstractmethod
    def prepare_request(
        self,
        *,
        model_name: str,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderView,
        api_key: str | None = None,
        thinking: str = "off",
    ) -> PreparedRequest: ...

    @abstractmethod
    def parse_response(
        self, data: dict[str, Any], provider: ProviderView
    ) -> LLMChunk: ...

    async def parse_stream(
        self, responses: AsyncGenerator[dict[str, Any]], provider: ProviderView
    ) -> AsyncGenerator[ParsedStreamChunk]:
        async with aclosing(responses):
            async for data in responses:
                yield ParsedStreamChunk(data, self.parse_response(data, provider))


__all__ = [
    "MODEL_HTTP_KEEPALIVE_EXPIRY_SECONDS",
    "APIAdapter",
    "ParsedStreamChunk",
    "PreparedRequest",
    "apply_reasoning_effort",
    "build_auth_headers",
    "build_chat_payload",
    "finalize_chat_request",
]
