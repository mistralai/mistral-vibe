"""Generic (non-Mistral) completion execution.

Bridges the Rust Harness message model to provider adapters:
translate ``RustMessage`` history into the provider-neutral model, run the
selected ``api_style`` adapter over a streamed HTTP response, accumulate the
chunks, and translate the final assistant message back into a
``RustCompletionResult``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping
from html import escape
import json
import logging
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
    ProviderRetryObserver,
    ProviderStreamDelta,
)
from mistralai_vibe_local_harness.vibe._ssl import build_ssl_context
from mistralai_vibe_local_harness.vibe.adapters._correlation import correlation_hook
from mistralai_vibe_local_harness.vibe.adapters._provider_failure import (
    IncompleteProviderStream,
    until_connection_lost,
)
from mistralai_vibe_local_harness.vibe.adapters._retry import (
    RetryNotices,
    call_with_retries,
)
from mistralai_vibe_local_harness.vibe.adapters._stream_idle import StreamIdleGuard
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

# The finish reason OpenAI-compatible providers, Mistral among them, report when
# they abort a generation part way.
_ABORTED_FINISH_REASON = "error"

# A completion is only interpretable next to the request knobs that decided it:
# which model answered, what reasoning effort it was given, and how many tools it
# could reach. None of that is recoverable from the result, so they travel with
# the one outcome the Runtime cannot explain on its own.
_COMPLETION_WITHOUT_WORK_LOG = (
    "Completion neither reasoned nor called a tool: "
    "provider=%s api_style=%s model=%s reasoning_effort=%s tools_offered=%d "
    "finish_reason=%s parts=%s output_tokens=%s"
)


def _openai_responses_adapter() -> APIAdapter:
    from mistralai_vibe_local_harness.vibe.adapters.generic._openai_responses import (
        OpenAIResponsesAdapter,
    )

    return OpenAIResponsesAdapter()


def _anthropic_adapter() -> APIAdapter:
    from mistralai_vibe_local_harness.vibe.adapters.generic._anthropic import (
        AnthropicAdapter,
    )

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
    # Accepted for signature parity with the Mistral adapter and dropped:
    # these provider APIs have no request-metadata channel, so the call's
    # attribution rides the Host's request-sent telemetry instead.
    metadata: Mapping[str, str] | None = None,
) -> RustCompletionResult:
    route = route or config.active_model
    provider = _provider_view(config)
    adapter = _get_adapter(provider.api_style)
    notices = RetryNotices(on_retry)
    request = adapter.prepare_request(
        model_name=route.model,
        messages=[_to_llm_message(message) for message in messages],
        temperature=route.temperature,
        top_p=route.top_p,
        tools=[_to_available_tool(tool) for tool in tools],
        max_tokens=config.output_token_cap(route),
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
        event_hooks={"response": [correlation_hook(config), notices.clear_on_success]},
    ) as client:
        result = await call_with_retries(
            lambda: _complete_once(
                client=client,
                url=url,
                body=request.body,
                headers=headers,
                provider=provider,
                model=route.model,
                idle_timeout_s=config.stream_idle_timeout_s,
                read_timeout_s=config.timeout_s,
                on_delta=on_delta,
            ),
            max_elapsed_time_s=config.retry_max_elapsed_time_s,
            notices=notices,
        )

    _warn_on_workless_completion(
        result, provider=provider, route=route, tools_offered=len(tools)
    )
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


async def _complete_once(
    *,
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
    provider: ProviderView,
    model: str,
    idle_timeout_s: float | None,
    read_timeout_s: float,
    on_delta: ProviderDeltaObserver | None = None,
) -> RustCompletionResult:
    # Adapters buffer parse state, so a retried attempt gets a fresh one.
    adapter = _get_adapter(provider.api_style)
    accumulated: LLMChunk | None = None
    usage = LLMUsage()
    has_usage = False
    idle_guard = StreamIdleGuard(idle_timeout_s, read_timeout_s=read_timeout_s)
    raw_chunks = _stream_json_chunks(client, url, body, headers, idle_guard)
    async for parsed in until_connection_lost(
        adapter.parse_stream(raw_chunks, provider),
        # Read when the stream fails, so it must see the latest chunk.
        complete=lambda: accumulated is not None and accumulated.stop is not None,  # noqa: B023
    ):
        chunk = parsed.chunk
        if _carries_model_output(chunk):
            idle_guard.output_started()
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
        raise IncompleteProviderStream(
            f"Model stream from {provider.name} ({model}) produced no chunks."
        )
    if provider.emits_finish_reason and accumulated.stop is None:
        raise IncompleteProviderStream(
            f"Model stream from {provider.name} ({model}) ended without a finish reason."
        )
    if (
        accumulated.stop is not None
        and accumulated.stop.reason == _ABORTED_FINISH_REASON
    ):
        raise IncompleteProviderStream(
            f"Model stream from {provider.name} ({model}) ended with the error "
            "finish reason."
        )
    return _to_completion_result(
        accumulated.model_copy(update={"usage": usage if has_usage else None})
    )


def _carries_model_output(chunk: LLMChunk) -> bool:
    """Whether a parsed chunk holds model output rather than a preamble.

    Providers may open a stream with a role-only or metadata event.
    """
    message = chunk.message
    return bool(message.content or message.reasoning_content or message.tool_calls)


def _reports_usage(response_data: dict[str, Any]) -> bool:
    """Whether the raw payload carries usage of its own, under any adapter shape."""
    if isinstance(response_data.get("usage"), dict):
        return True

    message = response_data.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return True

    response = response_data.get("response")
    return isinstance(response, dict) and isinstance(response.get("usage"), dict)


async def _stream_json_chunks(
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
    idle_guard: StreamIdleGuard,
) -> AsyncGenerator[dict[str, Any]]:
    async with client.stream(
        method="POST", url=url, content=body, headers=headers
    ) as response:
        if not response.is_success:
            await response.aread()
        response.raise_for_status()
        # Every line counts as activity once armed, so heartbeats and comments
        # keep a slow but live stream going.
        async for line in idle_guard.watch(iter_sse_lines(response)):
            if line.strip() == "" or line.startswith(":"):
                continue
            delimiter = ": "
            if delimiter not in line:
                raise ValueError(
                    "Stream chunk improperly formatted. Expected `key: value`."
                )
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
                if isinstance(
                    item, RustReasoningTextContent | RustReasoningSummaryContent
                ):
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
                    source=InlineImageSource(data=block.data), mime_type=block.mime_type
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
        parts=parts, finish_reason=finish_reason, usage=_token_usage(chunk)
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


def _finish_reason(
    chunk: LLMChunk, *, has_tool_calls: bool
) -> RustCompletionFinishReason:
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
