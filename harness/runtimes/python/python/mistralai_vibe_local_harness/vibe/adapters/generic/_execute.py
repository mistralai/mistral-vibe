"""Generic (non-Mistral) completion execution.

Bridges the Rust Harness message model to provider adapters:
translate ``RustMessage`` history into the provider-neutral model, run the
selected ``api_style`` adapter over a streamed HTTP response, accumulate the
chunks, and translate the final assistant message back into a
``RustCompletionResult``.
"""

import asyncio
import json
import logging
import ssl
import time
from collections.abc import AsyncGenerator, Callable
from html import escape
from http import HTTPStatus
from typing import Any, cast

import httpx

from mistralai_vibe_local_harness.protocol import (
    RustAssistantMessage,
    RustCompletionFinishReason,
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustEmbeddedResourceContentBlock,
    RustImageContentBlock,
    RustMessage,
    RustModelToolCallPart,
    RustReasoningPart,
    RustReasoningRedactedContent,
    RustReasoningSummaryContent,
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
from mistralai_vibe_local_harness.vibe.adapters.generic._base import (
    MODEL_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    APIAdapter,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._model import (
    AvailableFunction,
    AvailableTool,
    FunctionCall,
    ImageAttachment,
    InlineImageSource,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    ToolCall,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._openai_chat import (
    OpenAIAdapter,
    ReasoningAdapter,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._provider import ProviderView
from mistralai_vibe_local_harness.vibe.adapters.generic._sse import iter_sse_lines

logger = logging.getLogger(__name__)

# Key under which provider-specific reasoning payloads (encrypted content,
# signatures) are round-tripped through a RustReasoningPart's meta, so a resumed
# assistant turn can send them back for KV-cache reuse.
_REASONING_PAYLOADS_META_KEY = "vibe_reasoning_payloads"

# Retry transient failures for as long as ``retry_max_elapsed_time_s`` allows,
# rather than using a fixed attempt count.
_RETRYABLE_HTTP_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_RETRYABLE_REQUEST_ERRORS: tuple[type[httpx.RequestError], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)
# A TLS fault while the response body streams arrives here as a bare
# `ssl.SSLError` that no `httpx.RequestError` entry can match. Certificate rejection is the one
# deterministic case: it will fail the same way on every attempt.
_NON_RETRYABLE_TLS_ERRORS: tuple[type[ssl.SSLError], ...] = (ssl.SSLCertVerificationError,)
_INITIAL_RETRY_DELAY_S = 0.5
_MAX_RETRY_DELAY_S = 30.0
_RETRY_BACKOFF = 2.0

# A completion is only interpretable next to the request knobs that decided it:
# which model answered, what reasoning effort it was given, and how many tools it
# could reach. None of that is recoverable from the result, so they travel with
# the one outcome the Runtime cannot explain on its own.
_COMPLETION_WITHOUT_WORK_LOG = (
    "Completion neither reasoned nor called a tool: "
    "provider=%s api_style=%s model=%s reasoning_effort=%s tools_offered=%d "
    "finish_reason=%s parts=%s output_tokens=%s"
)


class _IncompleteStreamError(RuntimeError):
    """A provider that emits finish reasons closed the stream without one."""


def _openai_responses_adapter() -> APIAdapter:
    from mistralai_vibe_local_harness.vibe.adapters.generic._openai_responses import (
        OpenAIResponsesAdapter,
    )

    return OpenAIResponsesAdapter()


def _anthropic_adapter() -> APIAdapter:
    from mistralai_vibe_local_harness.vibe.adapters.generic._anthropic import AnthropicAdapter

    return AnthropicAdapter()


def _vertex_anthropic_adapter() -> APIAdapter:
    # Imported on use because the Vertex adapter pulls in google.auth, which is
    # not a Runtime dependency.
    from mistralai_vibe_local_harness.vibe.adapters.generic._vertex import (
        VertexAnthropicAdapter,
    )

    return VertexAnthropicAdapter()


_ADAPTERS: dict[str, Callable[[], APIAdapter]] = {
    "openai": OpenAIAdapter,
    "reasoning": ReasoningAdapter,
    "anthropic": _anthropic_adapter,
    "openai-responses": _openai_responses_adapter,
    "vertex-anthropic": _vertex_anthropic_adapter,
}


def _get_adapter(api_style: str) -> APIAdapter:
    """Build the adapter for the given API style.

    Adapters are built per request: several buffer state while parsing a
    streamed response, and a shared instance would let concurrent sessions
    observe each other's partial state.
    """
    try:
        return _ADAPTERS[api_style]()
    except KeyError:
        raise ValueError(f"Unsupported provider api_style: {api_style}") from None


async def execute_generic_completion(
    messages: list[RustMessage],
    tools: list[RustToolDefinition],
    config: LocalRuntimeAdapterConfig,
    credential: ProviderCredentialSnapshot,
    route: LocalModelRoute | None = None,
    on_retry: ProviderRetryObserver | None = None,
    on_delta: ProviderDeltaObserver | None = None,
) -> RustCompletionResult:
    route = route or config.active_model
    provider = _provider_view(config)
    adapter = _get_adapter(provider.api_style)
    retry_active = False

    async def report_retry(retry: ProviderRetry | None) -> None:
        nonlocal retry_active
        retry_active = retry is not None
        if on_retry is not None:
            await on_retry(retry)

    async def clear_retry_after_response(response: httpx.Response) -> None:
        if response.is_success and retry_active:
            await report_retry(None)

    request = adapter.prepare_request(
        model_name=route.model,
        messages=[_to_llm_message(message) for message in messages],
        temperature=route.temperature,
        tools=[_to_available_tool(tool) for tool in tools],
        max_tokens=config.max_tokens,
        tool_choice=None,
        enable_streaming=True,
        provider=provider,
        # Each adapter turns the key into the header its API expects
        # (``Authorization``, ``x-api-key``), so the resolved token is what
        # crosses the boundary rather than the snapshot's headers. The
        # ``vertex-anthropic`` adapter ignores it and obtains its own Google ADC
        # token.
        api_key=credential.token,
        thinking=route.thinking,
    )

    headers = dict(request.headers)
    headers.update(config.request_headers())

    base = request.base_url or provider.api_base.rstrip("/")
    url = f"{base}{request.endpoint}"
    response_hooks = [correlation_hook(config)]
    if on_retry is not None:
        response_hooks.append(clear_retry_after_response)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(config.timeout_s),
        limits=httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=MODEL_HTTP_KEEPALIVE_EXPIRY_SECONDS,
        ),
        verify=build_ssl_context(),
        follow_redirects=True,
        # Mistral-hosted models reached through the generic (OpenAI-compatible)
        # path still return mistral-correlation-id; capture it like the native
        # adapter. Non-Mistral providers omit the header (sink gets None).
        event_hooks={"response": response_hooks},
    ) as client:
        result = await _complete_with_retries(
            client=client,
            url=url,
            body=request.body,
            headers=headers,
            provider=provider,
            model=route.model,
            max_elapsed_time_s=config.retry_max_elapsed_time_s,
            on_retry=report_retry if on_retry is not None else None,
            on_delta=on_delta,
        )

    _warn_on_workless_completion(result, provider=provider, route=route, tools_offered=len(tools))
    return result


def _warn_on_workless_completion(
    result: RustCompletionResult,
    *,
    provider: ProviderView,
    route: LocalModelRoute,
    tools_offered: int,
) -> None:
    """Report a completion that ended a turn without doing any of the work it was given.

    A model handed tools and a reasoning budget that returns neither a tool call
    nor reasoning has answered without acting, and nothing downstream can tell
    that outcome from a deliberate final answer. Report only that case, carrying
    the request knobs, because the result alone cannot explain it.
    """
    if not tools_offered or route.thinking == "off":
        return
    kinds = [part.type for part in result.parts]
    if "tool_call" in kinds or "reasoning" in kinds:
        return
    logger.warning(
        _COMPLETION_WITHOUT_WORK_LOG,
        provider.name,
        provider.api_style,
        route.model,
        route.thinking,
        tools_offered,
        result.finish_reason,
        "+".join(kinds),
        result.usage.output_tokens if result.usage is not None else None,
    )


async def _complete_with_retries(
    *,
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
    provider: ProviderView,
    model: str,
    max_elapsed_time_s: float,
    on_retry: ProviderRetryObserver | None,
    on_delta: ProviderDeltaObserver | None = None,
) -> RustCompletionResult:
    start = time.monotonic()
    attempt = 0
    while True:
        try:
            # A failure part way through the stream is as retryable as one
            # before it starts: `_complete_once` buffers the whole response and
            # returns once, so nothing has entered the model-visible result.
            # Provisional output is different: a retried attempt has already
            # published its partial stream through ``on_delta``, so the retry
            # notice must reach the same observer chain before the next
            # attempt, letting the projection discard the failed attempt.
            return await _complete_once(
                client=client,
                url=url,
                body=body,
                headers=headers,
                provider=provider,
                model=model,
                on_delta=on_delta,
            )
        except Exception as exc:
            budget_spent = time.monotonic() - start >= max_elapsed_time_s
            if budget_spent or not _is_retryable_error(exc):
                raise
            if on_retry is not None:
                await on_retry(_provider_retry(exc))
            delay = _next_retry_delay(exc, attempt)
            logger.warning(
                "Retrying generic completion (attempt %d, delay %.2fs): %r",
                attempt + 1,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
            attempt += 1


async def _complete_once(
    *,
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
    provider: ProviderView,
    model: str,
    on_delta: ProviderDeltaObserver | None = None,
) -> RustCompletionResult:
    # Adapters buffer parse state, so a retried attempt gets a fresh one.
    adapter = _get_adapter(provider.api_style)
    accumulated: LLMChunk | None = None
    usage = LLMUsage()
    has_usage = False
    raw_chunks = _stream_json_chunks(client, url, body, headers)
    async for parsed in adapter.parse_stream(raw_chunks, provider):
        chunk = parsed.chunk
        # Adapters synthesise a zero ``LLMUsage`` for chunks the provider sent no
        # usage on, so summing ``chunk.usage`` cannot tell "the provider reported
        # nothing" from "the provider reported zero". Track the payloads that
        # carried usage of their own, and leave usage unset when none did, rather
        # than handing the Harness an authoritative-looking zero.
        if chunk.usage is not None and _reports_usage(parsed.data):
            usage += chunk.usage
            has_usage = True
        accumulated = chunk if accumulated is None else accumulated + chunk
        if on_delta is not None:
            text = chunk.message.content or ""
            reasoning = chunk.message.reasoning_content or ""
            if text or reasoning:
                await on_delta(ProviderStreamDelta(text=text, reasoning=reasoning))

    if accumulated is None:
        raise _IncompleteStreamError(
            f"Model stream from {provider.name} ({model}) produced no chunks."
        )
    if provider.emits_finish_reason and accumulated.stop is None:
        raise _IncompleteStreamError(
            f"Model stream from {provider.name} ({model}) ended without a finish reason."
        )
    return _to_completion_result(
        accumulated.model_copy(update={"usage": usage if has_usage else None})
    )


def _reports_usage(response_data: dict[str, Any]) -> bool:
    """Whether the raw payload carries usage of its own, under any adapter shape."""
    if isinstance(response_data.get("usage"), dict):
        return True

    message = response_data.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return True

    response = response_data.get("response")
    return isinstance(response, dict) and isinstance(response.get("usage"), dict)


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, _IncompleteStreamError):
        return True
    status = _http_status(error)
    if status is not None:
        return status in _RETRYABLE_HTTP_STATUS
    if isinstance(error, ssl.SSLError):
        return not isinstance(error, _NON_RETRYABLE_TLS_ERRORS)
    return isinstance(error, _RETRYABLE_REQUEST_ERRORS)


def _provider_retry(error: Exception) -> ProviderRetry:
    status = _http_status(error)
    if status is not None:
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            category = "rate_limited"
        elif status == HTTPStatus.REQUEST_TIMEOUT:
            category = "timed_out"
        elif status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            category = "server_error"
        else:
            category = "unknown"
        return ProviderRetry(category=category, detail=f"HTTP {status}")
    if isinstance(error, httpx.TimeoutException):
        return ProviderRetry(category="timed_out", detail=type(error).__name__)
    if isinstance(error, _RETRYABLE_REQUEST_ERRORS + (ssl.SSLError,)):
        return ProviderRetry(category="connection", detail=type(error).__name__)
    return ProviderRetry(category="unknown", detail=type(error).__name__)


def _http_status(error: Exception) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    # OpenAIResponsesStreamError carries a mapped HTTP status.
    status = getattr(error, "status", None)
    return status if isinstance(status, int) else None


def _next_retry_delay(error: Exception, attempt: int) -> float:
    retry_after = _retry_after_seconds(error)
    if retry_after is not None:
        return min(retry_after, _MAX_RETRY_DELAY_S)
    capped_attempt = min(attempt, 10)
    return min(_INITIAL_RETRY_DELAY_S * (_RETRY_BACKOFF**capped_attempt), _MAX_RETRY_DELAY_S)


def _retry_after_seconds(error: Exception) -> float | None:
    if not isinstance(error, httpx.HTTPStatusError):
        return None
    value = error.response.headers.get("retry-after", "").strip()
    return float(value) if value.isdigit() else None


async def _stream_json_chunks(
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
) -> AsyncGenerator[dict[str, Any]]:
    async with client.stream(method="POST", url=url, content=body, headers=headers) as response:
        if not response.is_success:
            await response.aread()
        response.raise_for_status()
        async for line in iter_sse_lines(response):
            if line.strip() == "" or line.startswith(":"):
                continue
            delimiter = ": "
            if delimiter not in line:
                raise ValueError("Stream chunk improperly formatted. Expected `key: value`.")
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key != "data":
                # e.g. anthropic/responses `event:` lines, or OpenRouter comments.
                continue
            if value.strip() == "[DONE]":
                return
            try:
                yield json.loads(value.strip())
            except json.JSONDecodeError:
                raise ValueError("Stream chunk contains malformed JSON.") from None


def _provider_view(config: LocalRuntimeAdapterConfig) -> ProviderView:
    return ProviderView(
        name=config.provider,
        api_base=config.base_url,
        api_style=config.api_style,
        reasoning_field_name=config.reasoning_field_name,
        emits_finish_reason=config.emits_finish_reason,
        project_id=config.project_id,
        region=config.region,
        extra_headers=dict(config.extra_headers),
    )


def _to_available_tool(tool: RustToolDefinition) -> AvailableTool:
    return AvailableTool(
        function=AvailableFunction(
            name=tool.name,
            description=tool.description,
            parameters=cast(dict[str, Any], tool.parameters),
        )
    )


def _to_llm_message(message: RustMessage) -> LLMMessage:
    if isinstance(message, RustSystemMessage):
        return LLMMessage(role=Role.system, content=_blocks_text(message.content))
    if isinstance(message, RustUserMessage):
        return LLMMessage(
            role=Role.user,
            content=_blocks_text(message.content) or None,
            images=_blocks_images(message.content) or None,
        )
    if isinstance(message, RustAssistantMessage):
        return _assistant_to_llm_message(message)
    if isinstance(message, RustToolMessage):
        return LLMMessage(
            role=Role.tool,
            content=_blocks_text(message.content),
            name=message.name,
            tool_call_id=message.tool_call_id,
        )
    raise TypeError(f"Unsupported message type: {type(message).__name__}")


def _assistant_to_llm_message(message: RustAssistantMessage) -> LLMMessage:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_payloads: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []

    for part in message.content:
        if isinstance(part, RustTextContentBlock):
            text_parts.append(part.text)
        elif isinstance(part, RustReasoningPart):
            for item in part.content:
                if isinstance(item, RustReasoningTextContent | RustReasoningSummaryContent):
                    reasoning_parts.append(item.text)
            if part.meta:
                stored = part.meta.get(_REASONING_PAYLOADS_META_KEY)
                if isinstance(stored, list):
                    reasoning_payloads.extend(cast(list[dict[str, Any]], stored))
        elif isinstance(part, RustModelToolCallPart):
            tool_calls.append(
                ToolCall(
                    id=part.id,
                    # Position among the tool calls, not among the assistant parts.
                    index=len(tool_calls),
                    function=FunctionCall(
                        name=part.name,
                        arguments=tool_call_wire_arguments(part.arguments),
                    ),
                )
            )
        elif isinstance(part, RustResourceLinkContentBlock):
            text_parts.append(f"[resource: {part.uri}]")

    return LLMMessage(
        role=Role.assistant,
        content="".join(text_parts) or None,
        reasoning_content="".join(reasoning_parts) or None,
        reasoning_payloads=reasoning_payloads or None,
        tool_calls=tool_calls or None,
    )


def _blocks_text(content: Any) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, RustTextContentBlock):
            parts.append(block.text)
        elif isinstance(block, RustEmbeddedResourceContentBlock) and isinstance(
            block.resource, RustTextResourceContents
        ):
            parts.append(_resource_text(block.resource))
        elif isinstance(block, RustResourceLinkContentBlock):
            parts.append(f"[resource: {block.uri}]")
    return "\n\n".join(parts)


def _blocks_images(content: Any) -> list[ImageAttachment]:
    images: list[ImageAttachment] = []
    for block in content:
        if isinstance(block, RustImageContentBlock):
            images.append(
                ImageAttachment(
                    source=InlineImageSource(data=block.data),
                    mime_type=block.mime_type,
                )
            )
    return images


def _resource_text(resource: RustTextResourceContents) -> str:
    uri = escape(resource.uri, quote=True)
    fence = "````"
    while fence in resource.text:
        fence += "`"
    return f"Resource: {uri}\n{fence}\n{resource.text}\n{fence}"


def _to_completion_result(chunk: LLMChunk) -> RustCompletionResult:
    message = chunk.message
    parts: list[RustCompletionResultPart] = []

    if reasoning := _reasoning_part(message):
        parts.append(reasoning)
    if message.content:
        parts.append(RustTextContentBlock(text=message.content))
    parts.extend(_tool_call_parts(message.tool_calls))

    if not parts:
        parts.append(RustTextContentBlock(text=" "))

    finish_reason = _finish_reason(chunk, has_tool_calls=bool(message.tool_calls))
    return RustCompletionResult(
        parts=parts,
        finish_reason=finish_reason,
        usage=_token_usage(chunk),
    )


def _reasoning_part(message: LLMMessage) -> RustReasoningPart | None:
    content: list[Any] = []
    if message.reasoning_content:
        content.append(RustReasoningTextContent(text=message.reasoning_content))
    elif message.reasoning_payloads:
        # Preserve the reasoning slot for KV-cache reuse even when the provider
        # only returns opaque/redacted reasoning without visible text.
        content.append(RustReasoningRedactedContent(data=""))

    if not content:
        return None

    data: dict[str, Any] = {"content": content}
    if message.reasoning_payloads:
        # `_meta` is the wire alias for the model's `meta` field.
        data["_meta"] = {_REASONING_PAYLOADS_META_KEY: message.reasoning_payloads}
    return RustReasoningPart.model_validate(data)


def _tool_call_parts(
    tool_calls: list[ToolCall] | None,
) -> list[RustCompletionResultToolCallPart]:
    parts: list[RustCompletionResultToolCallPart] = []
    for index, tc in enumerate(tool_calls or []):
        if not tc.function.name:
            continue
        parts.append(
            RustCompletionResultToolCallPart(
                id=tc.id or f"tool_call_{tc.index if tc.index is not None else index}",
                name=tc.function.name,
                arguments_json=tc.function.arguments or "{}",
            )
        )
    return parts


def _finish_reason(chunk: LLMChunk, *, has_tool_calls: bool) -> RustCompletionFinishReason:
    if has_tool_calls:
        return "tool_call"
    raw = chunk.stop.reason if chunk.stop else None
    if raw in {"tool_calls", "tool_use", "tool_call"}:
        return "tool_call"
    if raw in {"stop", "end_turn", "completed", "stop_sequence"}:
        return "stop"
    if raw in {"length", "max_tokens", "incomplete"}:
        return "length"
    if raw == "content_filter":
        return "content_filter"
    return "stop" if raw is None else "other"


def _token_usage(chunk: LLMChunk) -> RustTokenUsage | None:
    if chunk.usage is None:
        return None
    input_tokens = chunk.usage.prompt_tokens
    output_tokens = chunk.usage.completion_tokens
    return RustTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cached_input_tokens=chunk.usage.cached_tokens,
    )


__all__ = ["execute_generic_completion"]
