from __future__ import annotations

from collections.abc import Sequence
import json
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Protocol

from vibe.core.types import AvailableTool, LLMChunk, LLMMessage, StrToolChoice

if TYPE_CHECKING:
    from vibe.core.config import ProviderConfig


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


class APIAdapter(Protocol):
    endpoint: ClassVar[str]

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
        provider: ProviderConfig,
        api_key: str | None = None,
        thinking: str = "off",
    ) -> PreparedRequest: ...

    def parse_response(
        self, data: dict[str, Any], provider: ProviderConfig
    ) -> LLMChunk: ...
