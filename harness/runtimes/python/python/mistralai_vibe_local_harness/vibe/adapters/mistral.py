from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import escape
from http import HTTPStatus
import logging
import random
import re
import time
from typing import Any, Literal, cast

import httpx
from mistralai.client import Mistral
from mistralai.client.errors import MistralError
from mistralai.client.models.chatcompletionstreamrequest import (
    ChatCompletionStreamRequestMessageTypedDict,
)
from mistralai.client.utils.retries import BackoffStrategy, RetryConfig

from mistralai_vibe_local_harness.protocol import (
    JsonObject,
    RustAssistantMessage,
    RustCompletionFinishReason,
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustEmbeddedResourceContentBlock,
    RustImageContentBlock,
    RustMessage,
    RustModelToolCallPart,
    RustReasoningContent,
    RustReasoningPart,
    RustReasoningTextContent,
    RustResourceLinkContentBlock,
    RustSystemMessage,
    RustTextContentBlock,
    RustTextResourceContents,
    RustTokenUsage,
    RustToolDefinition,
    RustToolMessage,
    RustUserMessage,
    tool_call_wire_arguments,
)
from mistralai_vibe_local_harness.session_protocol import PublicRetryCategory
from mistralai_vibe_local_harness.vibe._credentials import ProviderCredentialSnapshot
from mistralai_vibe_local_harness.vibe._runtime_config import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    ProviderDeltaObserver,
    ProviderRetry,
    ProviderRetryObserver,
    ProviderStreamDelta,
)
from mistralai_vibe_local_harness.vibe._ssl import build_ssl_context
from mistralai_vibe_local_harness.vibe.adapters._correlation import correlation_hook

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_ERRORS = (httpx.NetworkError, httpx.TimeoutException)
# Retries run in _call_with_retries, which needs each backoff before sleeping.
_NO_SDK_RETRIES = RetryConfig(
    strategy="none", backoff=BackoffStrategy(0, 0, 0, 0), retry_connection_errors=False
)
_INITIAL_RETRY_DELAY_S = 0.5
_RETRY_BACKOFF = 1.5
_MAX_RETRY_DELAY_S = 30.0
_MAX_RETRY_JITTER_S = 1.0


type ReasoningEffort = Literal["none", "high"]

_THINKING_TO_REASONING_EFFORT: dict[str, ReasoningEffort] = {
    "low": "none",
    "medium": "high",
    "high": "high",
    "max": "high",
}


async def execute_mistral_completion(
    messages: list[RustMessage],
    tools: list[RustToolDefinition],
    config: LocalRuntimeAdapterConfig,
    credential: ProviderCredentialSnapshot,
    route: LocalModelRoute,
    *,
    stream: bool = True,
    metadata: Mapping[str, str] | None = None,
    on_retry: ProviderRetryObserver | None = None,
    on_delta: ProviderDeltaObserver | None = None,
) -> RustCompletionResult:
    retry_active = False

    async def report_retry(retry: ProviderRetry | None) -> None:
        nonlocal retry_active
        retry_active = retry is not None
        if on_retry is not None:
            await on_retry(retry)

    async def clear_retry_after_response(response: httpx.Response) -> None:
        if response.is_success and retry_active:
            await report_retry(None)

    async with httpx.AsyncClient(
        timeout=config.timeout_s,
        verify=build_ssl_context(),
        follow_redirects=True,
        # Capture the provider correlation id at the transport level so both the
        # streaming and non-streaming paths report it; the parsed complete_async
        # response drops headers.
        event_hooks={
            "response": [correlation_hook(config), clear_retry_after_response]
        },
    ) as http_client:
        client = Mistral(
            api_key=credential.token,
            server_url=_server_url_from_api_base(config.base_url),
            async_client=http_client,
            retry_config=_NO_SDK_RETRIES,
            timeout_ms=int(config.timeout_s * 1000),
        )
        kwargs: dict[str, Any] = {
            "model": route.model,
            "messages": [_message_payload(message) for message in messages],
            "temperature": route.temperature,
        }
        if reasoning_effort := _THINKING_TO_REASONING_EFFORT.get(route.thinking):
            kwargs["reasoning_effort"] = reasoning_effort
        if config.max_tokens is not None:
            kwargs["max_tokens"] = config.max_tokens
        if metadata:
            kwargs["metadata"] = dict(metadata)
        if headers := config.request_headers():
            kwargs["http_headers"] = headers
        if tools:
            kwargs["tools"] = [_tool_payload(tool) for tool in tools]
            kwargs["parallel_tool_calls"] = True
        if stream:
            response_stream = await _call_with_retries(
                lambda: client.chat.stream_async(**kwargs),
                max_elapsed_time_s=config.retry_max_elapsed_time_s,
                on_retry=report_retry,
            )
            return await _read_stream(response_stream, on_delta)
        response = await _call_with_retries(
            lambda: client.chat.complete_async(**kwargs),
            max_elapsed_time_s=config.retry_max_elapsed_time_s,
            on_retry=report_retry,
        )
        return _read_completion(response)


async def _call_with_retries[T](
    call: Callable[[], Awaitable[T]],
    *,
    max_elapsed_time_s: float,
    on_retry: ProviderRetryObserver,
) -> T:
    start = time.monotonic()
    attempt = 0
    while True:
        try:
            return await call()
        except Exception as exc:
            budget_spent = time.monotonic() - start >= max_elapsed_time_s
            if budget_spent or not _is_retryable_error(exc):
                raise
            delay = _next_retry_delay(exc, attempt)
            logger.warning(
                "Retrying Mistral completion (attempt %d, delay %.2fs): %r",
                attempt + 1,
                delay,
                exc,
            )
            category, detail = _retry_reason(exc)
            await on_retry(
                ProviderRetry(
                    category=category,
                    detail=detail,
                    delay_s=delay,
                    retry_attempt=attempt + 1,
                )
            )
            await asyncio.sleep(delay)
            attempt += 1


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, MistralError):
        return error.status_code in _RETRYABLE_STATUS_CODES
    return isinstance(error, _RETRYABLE_ERRORS)


def _retry_reason(error: Exception) -> tuple[PublicRetryCategory, str]:
    if isinstance(error, MistralError):
        if error.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            return "rate_limited", f"HTTP {error.status_code}"
        return "server_error", f"HTTP {error.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "timed_out", type(error).__name__
    return "connection", type(error).__name__


def _next_retry_delay(error: Exception, attempt: int) -> float:
    retry_after = _retry_after_s(error)
    if retry_after is not None:
        return min(retry_after, _MAX_RETRY_DELAY_S)
    backoff = _INITIAL_RETRY_DELAY_S * _RETRY_BACKOFF**attempt
    jitter = random.uniform(0, _MAX_RETRY_JITTER_S)
    return min(backoff + jitter, _MAX_RETRY_DELAY_S)


def _retry_after_s(error: Exception) -> float | None:
    if not isinstance(error, MistralError):
        return None
    value = error.headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max((retry_at - datetime.now(UTC)).total_seconds(), 0.0)


def _read_completion(response: Any) -> RustCompletionResult:
    choice = _first_choice(response)
    if choice is None:
        raise ValueError("Completion response has no choices")
    message = _read_field(choice, "message")
    content = _read_field(message, "content")
    parts: list[RustCompletionResultPart] = []
    if isinstance(content, str):
        if content:
            parts.append(RustTextContentBlock(text=content))
    elif isinstance(content, list):
        reasoning, reasoning_meta = _extract_reasoning(content)
        if reasoning:
            parts.append(
                RustReasoningPart(content=reasoning, _meta=reasoning_meta or None)
            )
        text = _delta_text(content)
        if text:
            parts.append(RustTextContentBlock(text=text))
    if not parts:
        raise ValueError("Completion response has no text content")
    reason = _read_field(choice, "finish_reason")
    return RustCompletionResult(
        parts=parts,
        finish_reason=_finish_reason(reason) if isinstance(reason, str) else "stop",
        usage=_usage(_read_field(response, "usage")),
    )


async def _read_stream(
    stream: Any, on_delta: ProviderDeltaObserver | None = None
) -> RustCompletionResult:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_meta: JsonObject = {}
    tool_calls: dict[int, dict[str, str]] = {}
    usage: RustTokenUsage | None = None
    finish_reason = "stop"
    async for event in stream:
        chunk = _event_data(event)
        if parsed_usage := _usage(_read_field(chunk, "usage")):
            usage = parsed_usage
        choice = _first_choice(chunk)
        if choice is None:
            continue
        delta = _read_field(choice, "delta")
        delta_content = _read_field(delta, "content")
        delta_text = _delta_text(delta_content)
        if delta_text:
            text_parts.append(delta_text)
        delta_reasoning, delta_meta = _delta_reasoning(delta_content)
        if delta_reasoning:
            reasoning_parts.append(delta_reasoning)
        if delta_meta:
            reasoning_meta.update(delta_meta)
        _collect_tool_call_deltas(tool_calls, _read_field(delta, "tool_calls"))
        if isinstance(reason := _read_field(choice, "finish_reason"), str):
            finish_reason = _finish_reason(reason)
        if on_delta is not None and (delta_text or delta_reasoning):
            await on_delta(
                ProviderStreamDelta(text=delta_text, reasoning=delta_reasoning)
            )
    parts: list[RustCompletionResultPart] = []
    if reasoning_parts:
        parts.append(
            RustReasoningPart(
                content=[RustReasoningTextContent(text="".join(reasoning_parts))],
                _meta=reasoning_meta or None,
            )
        )
    text = "".join(text_parts)
    if text:
        parts.append(RustTextContentBlock(text=text))
    parts.extend(_tool_call_parts(tool_calls))
    if not parts:
        parts.append(RustTextContentBlock(text=" "))
    if tool_calls:
        finish_reason = "tool_call"
    return RustCompletionResult(parts=parts, finish_reason=finish_reason, usage=usage)


def _delta_text(content: object) -> str:
    """Read the visible assistant text out of one streaming delta.

    Mistral types delta content as ``str | list[ContentChunk]``. A model asked
    for ``reasoning_effort`` answers in the list arm, mixing ``text`` chunks
    with ``thinking`` chunks inside a single delta, and it may switch arms
    mid-stream. Reading only the ``str`` arm dropped whichever visible tokens
    rode in a list-shaped delta -- typically the first ones, right after the
    reasoning block closed.

    Chunks are discriminated on their ``type`` field rather than against SDK
    models, so both mappings and SDK objects are accepted, matching the rest
    of this module.

    ``thinking`` chunks are extracted by ``_delta_reasoning`` and carried as a
    ``RustReasoningPart``. ``_message_payload`` replays them back to the API on
    subsequent turns so the model sees its own reasoning history.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for chunk in cast(list[object], content):
        if _read_field(chunk, "type") != "text":
            continue
        if isinstance(text := _read_field(chunk, "text"), str):
            chunks.append(text)
    return "".join(chunks)


def _delta_reasoning(content: object) -> tuple[str, JsonObject]:
    """Extract reasoning text and continuation metadata from one streaming delta."""
    if not isinstance(content, list):
        return "", {}
    parts: list[str] = []
    meta: JsonObject = {}
    for chunk in cast(list[object], content):
        if _read_field(chunk, "type") != "thinking":
            continue
        thinking = _read_field(chunk, "thinking")
        if isinstance(thinking, list):
            for inner in cast(list[object], thinking):
                if (text := _thinking_inner_text(inner)) is not None:
                    parts.append(text)
        if chunk_meta := _thinking_meta(chunk):
            meta.update(chunk_meta)
    return "".join(parts), meta


def _extract_reasoning(
    content: list[object],
) -> tuple[list[RustReasoningContent], JsonObject]:
    """Extract reasoning and continuation metadata from a non-streaming response."""
    parts: list[RustReasoningContent] = []
    meta: JsonObject = {}
    for chunk in content:
        if _read_field(chunk, "type") != "thinking":
            continue
        thinking = _read_field(chunk, "thinking")
        if isinstance(thinking, list):
            for inner in cast(list[object], thinking):
                if (text := _thinking_inner_text(inner)) is not None:
                    parts.append(RustReasoningTextContent(text=text))
        if chunk_meta := _thinking_meta(chunk):
            meta.update(chunk_meta)
    return parts, meta


def _thinking_inner_text(inner: object) -> str | None:
    """Read text from one ``thinking`` inner item, accepting both dicts and raw strings."""
    if isinstance(inner, str):
        return inner
    if isinstance(text := _read_field(inner, "text"), str):
        return text
    return None


def _thinking_meta(chunk: object) -> JsonObject:
    """Extract continuation metadata (signature, closed) from a thinking chunk."""
    meta: JsonObject = {}
    if isinstance(signature := _read_field(chunk, "signature"), str):
        meta["signature"] = signature
    if isinstance(closed := _read_field(chunk, "closed"), bool):
        meta["closed"] = closed
    return meta


def _server_url_from_api_base(api_base: str) -> str | None:
    if match := re.match(r"(https?://.+)(/v\d+.*)", api_base):
        return match.group(1)
    return api_base.rstrip("/") or None


def _message_payload(
    message: RustMessage,
) -> ChatCompletionStreamRequestMessageTypedDict:
    if isinstance(message, RustSystemMessage | RustUserMessage):
        return cast(
            ChatCompletionStreamRequestMessageTypedDict,
            {"role": message.role, "content": _content(message.content)},
        )
    if isinstance(message, RustAssistantMessage):
        # Replay reasoning as structured ``thinking`` chunks so the API
        # receives the model's own reasoning history, matching the legacy
        # backend's ``ThinkChunk`` replay.
        reasoning_chunks: list[dict[str, Any]] = []
        for block in message.content:
            if isinstance(block, RustReasoningPart):
                thinking_items = [
                    {"type": "text", "text": item.text}
                    for item in block.content
                    if isinstance(item, RustReasoningTextContent)
                ]
                if thinking_items:
                    reasoning_chunks.append({
                        "type": "thinking",
                        "thinking": thinking_items,
                    })
        text_content = _text(message.content)
        if reasoning_chunks:
            all_chunks = reasoning_chunks.copy()
            if text_content:
                all_chunks.append({"type": "text", "text": text_content})
            payload: dict[str, Any] = {"role": "assistant", "content": all_chunks}
        else:
            payload = {
                "role": "assistant",
                "content": _content(message.content) or None,
            }
        tool_calls = [
            _assistant_tool_call_payload(part)
            for part in message.content
            if isinstance(part, RustModelToolCallPart)
        ]
        if tool_calls:
            payload["tool_calls"] = tool_calls
        return cast(ChatCompletionStreamRequestMessageTypedDict, payload)
    if isinstance(message, RustToolMessage):
        # Tool results may carry image blocks; the API accepts chunked
        # content on tool messages, so pass the blocks through unchanged
        # instead of flattening to text.
        return cast(
            ChatCompletionStreamRequestMessageTypedDict,
            {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "name": message.name,
                "content": _content(message.content),
            },
        )
    raise TypeError(f"Unsupported message type: {type(message).__name__}")


def _content(content: Sequence[Any]) -> str | list[dict[str, Any]]:
    if not any(isinstance(block, RustImageContentBlock) for block in content):
        return _text(content)

    parts: list[dict[str, Any]] = []
    for block in content:
        if (text := _block_text(block)) is not None:
            parts.append({"type": "text", "text": text})
        elif isinstance(block, RustImageContentBlock):
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
            })
    return parts


def _text(content: Sequence[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if (text := _block_text(block)) is not None:
            parts.append(text)
    return "\n\n".join(parts)


def _block_text(block: Any) -> str | None:
    if isinstance(block, RustTextContentBlock):
        return block.text
    if isinstance(block, RustEmbeddedResourceContentBlock) and isinstance(
        block.resource, RustTextResourceContents
    ):
        return _resource_text(block.resource)
    if isinstance(block, RustResourceLinkContentBlock):
        return f"[resource: {block.uri}]"
    return None


def _resource_text(resource: RustTextResourceContents) -> str:
    uri = escape(resource.uri, quote=True)
    fence = "````"
    while fence in resource.text:
        fence += "`"
    return f"Resource: {uri}\n{fence}\n{resource.text}\n{fence}"


def _assistant_tool_call_payload(part: RustModelToolCallPart) -> dict[str, Any]:
    return {
        "id": part.id,
        "type": "function",
        "function": {
            "name": part.name,
            "arguments": tool_call_wire_arguments(part.arguments),
        },
    }


def _tool_payload(tool: RustToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _collect_tool_call_deltas(
    tool_calls: dict[int, dict[str, str]], deltas: object
) -> None:
    if not isinstance(deltas, list):
        return
    for delta in deltas:
        raw_index = _read_field(delta, "index")
        index = raw_index if isinstance(raw_index, int) else len(tool_calls)
        current = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if (
            isinstance(identifier := _read_field(delta, "id"), str)
            and identifier != "null"
        ):
            current["id"] = _merge_streamed_value(current["id"], identifier)
        function = _read_field(delta, "function")
        if isinstance(name := _read_field(function, "name"), str) and name:
            current["name"] += name
        if isinstance(arguments := _read_field(function, "arguments"), str):
            current["arguments"] += arguments


def _merge_streamed_value(current: str, fragment: str) -> str:
    if not current or fragment.startswith(current):
        return fragment
    if current.endswith(fragment):
        return current
    return current + fragment


def _tool_call_parts(
    tool_calls: dict[int, dict[str, str]],
) -> list[RustCompletionResultToolCallPart]:
    return [
        RustCompletionResultToolCallPart(
            id=values["id"] or f"tool_call_{index}",
            name=values["name"],
            arguments_json=values["arguments"] or "{}",
        )
        for index, values in sorted(tool_calls.items())
        if values["name"]
    ]


def _event_data(event: object) -> object:
    return getattr(event, "data", event)


def _read_field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _first_choice(chunk: object) -> object | None:
    choices = _read_field(chunk, "choices")
    if isinstance(choices, list) and not choices:
        return None
    if not isinstance(choices, list):
        raise ValueError("provider chunk has no first choice")
    return choices[0]


def _usage(value: object) -> RustTokenUsage | None:
    if value is None:
        return None
    input_tokens = (
        _read_field(value, "prompt_tokens") or _read_field(value, "input_tokens") or 0
    )
    output_tokens = (
        _read_field(value, "completion_tokens")
        or _read_field(value, "output_tokens")
        or 0
    )
    prompt_tokens_details = _read_field(value, "prompt_tokens_details")
    cached_input_tokens = _read_field(prompt_tokens_details, "cached_tokens") or 0
    if (
        not isinstance(input_tokens, int)
        or not isinstance(output_tokens, int)
        or not isinstance(cached_input_tokens, int)
    ):
        return None
    return RustTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def _finish_reason(value: str) -> RustCompletionFinishReason:
    if value == "tool_calls":
        return "tool_call"
    if value in {"stop", "tool_call", "length", "content_filter", "other"}:
        return cast(RustCompletionFinishReason, value)
    return "other"


__all__ = ["execute_mistral_completion"]
