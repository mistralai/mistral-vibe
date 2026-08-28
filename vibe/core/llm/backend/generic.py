from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, aclosing, nullcontext
import functools
import json
import types
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

import httpx

from vibe.core.llm.backend._image import to_data_uri as _to_data_uri
from vibe.core.llm.backend.anthropic import AnthropicAdapter
from vibe.core.llm.backend.base import (
    MODEL_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    APIAdapter,
    ParsedStreamChunk,
    PreparedRequest,
    build_chat_payload,
    finalize_chat_request,
)
from vibe.core.llm.backend.openai_responses import (
    OpenAIResponsesAdapter,
    OpenAIResponsesStreamError,
)
from vibe.core.llm.backend.reasoning_adapter import ReasoningAdapter
from vibe.core.llm.exceptions import BackendErrorBuilder
from vibe.core.tracing import (
    model_call_span,
    set_model_call_http_status,
    set_model_call_response_metadata,
    set_model_call_usage,
)
from vibe.core.types import (
    AvailableTool,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StopInfo,
    StrToolChoice,
)
from vibe.core.utils import (
    AdaptivePacer,
    RetryCategory,
    RetryObserver,
    RetryReason,
    async_generator_retry,
    async_retry,
)
from vibe.core.utils.sse import iter_sse_lines
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

if TYPE_CHECKING:
    from opentelemetry import trace

    from vibe.core.config import ModelConfig, ProviderConfig


class OpenAIAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/chat/completions"

    def _reasoning_to_api(
        self, msg_dict: dict[str, Any], field_name: str
    ) -> dict[str, Any]:
        if field_name != "reasoning_content" and "reasoning_content" in msg_dict:
            msg_dict[field_name] = msg_dict.pop("reasoning_content")
        return msg_dict

    def _reasoning_from_api(
        self, msg_dict: dict[str, Any], field_name: str
    ) -> dict[str, Any]:
        if field_name != "reasoning_content" and field_name in msg_dict:
            msg_dict["reasoning_content"] = msg_dict.pop(field_name)
        return msg_dict

    def _user_with_images_to_parts(
        self, msg_dict: dict[str, Any], source: LLMMessage
    ) -> dict[str, Any]:
        if source.role != Role.user or not source.images:
            return msg_dict
        parts: list[dict[str, Any]] = []
        text = msg_dict.get("content")
        if isinstance(text, str) and text:
            parts.append({"type": "text", "text": text})
        parts.extend(
            {"type": "image_url", "image_url": {"url": _to_data_uri(att)}}
            for att in source.images
        )
        msg_dict["content"] = parts
        return msg_dict

    def _convert_messages(
        self, messages: Sequence[LLMMessage], provider: ProviderConfig
    ) -> list[dict[str, Any]]:
        field_name = provider.reasoning_field_name
        return [
            self._user_with_images_to_parts(
                self._reasoning_to_api(
                    msg.model_dump(
                        exclude_none=True,
                        exclude={
                            "message_id": True,
                            "reasoning_message_id": True,
                            "reasoning_payloads": True,
                            "injected": True,
                            "images": True,
                            "tool_result": True,
                            "user_display_content": True,
                            "input_text": True,
                            "resources": True,
                            "manual_shell": True,
                            "context_boundary": True,
                            "tool_calls": {"__all__": {"presentation"}},
                        },
                    ),
                    field_name,
                ),
                msg,
            )
            for msg in messages
        ]

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
    ) -> PreparedRequest:
        converted_messages = self._convert_messages(messages, provider)

        payload = build_chat_payload(
            model_name=model_name,
            messages=converted_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            thinking=thinking,
        )

        stream_options: dict[str, Any] = {"include_usage": True}
        if provider.name == "mistral":
            stream_options["stream_tool_calls"] = True

        return finalize_chat_request(
            payload=payload,
            enable_streaming=enable_streaming,
            stream_options=stream_options,
            api_key=api_key,
            endpoint=self.endpoint,
        )

    def _parse_message(
        self, data: dict[str, Any], field_name: str
    ) -> LLMMessage | None:
        if data.get("choices"):
            choice = data["choices"][0]
            if "message" in choice:
                msg_dict = self._reasoning_from_api(choice["message"], field_name)
                return LLMMessage.model_validate(msg_dict)
            if "delta" in choice:
                msg_dict = self._reasoning_from_api(choice["delta"], field_name)
                if msg_dict.get("role") is None:
                    msg_dict["role"] = Role.assistant
                return LLMMessage.model_validate(msg_dict)
            raise ValueError("Invalid response data: missing message or delta")

        if "message" in data:
            msg_dict = self._reasoning_from_api(data["message"], field_name)
            return LLMMessage.model_validate(msg_dict)
        if "delta" in data:
            msg_dict = self._reasoning_from_api(data["delta"], field_name)
            if msg_dict.get("role") is None:
                msg_dict["role"] = Role.assistant
            return LLMMessage.model_validate(msg_dict)

        return None

    def parse_response(
        self, data: dict[str, Any], provider: ProviderConfig
    ) -> LLMChunk:
        message = self._parse_message(data, provider.reasoning_field_name)
        if message is None:
            message = LLMMessage(role=Role.assistant, content="")

        usage_data = data.get("usage") or {}
        prompt_details = usage_data.get("prompt_tokens_details") or {}
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            cached_tokens=prompt_details.get("cached_tokens", 0),
        )
        choices = data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        stop = (
            StopInfo(reason=str(finish_reason)) if finish_reason is not None else None
        )

        return LLMChunk(message=message, usage=usage, stop=stop)


def _vertex_anthropic_adapter() -> APIAdapter:
    # Imported on use because the Vertex adapter pulls in google.auth, which is
    # heavy enough to be noticeable at CLI startup.
    from vibe.core.llm.backend.vertex import VertexAnthropicAdapter

    return VertexAnthropicAdapter()


_ADAPTERS: dict[str, Callable[[], APIAdapter]] = {
    "openai": OpenAIAdapter,
    "reasoning": ReasoningAdapter,
    "anthropic": AnthropicAdapter,
    "openai-responses": OpenAIResponsesAdapter,
    "vertex-anthropic": _vertex_anthropic_adapter,
}


def _reports_usage(response_data: dict[str, Any]) -> bool:
    if isinstance(response_data.get("usage"), dict):
        return True

    message = response_data.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return True

    response = response_data.get("response")
    return isinstance(response, dict) and isinstance(response.get("usage"), dict)


def _get_adapter(api_style: str) -> APIAdapter:
    """Build the adapter for the given API style.

    Adapters are built per request: several of them buffer state while parsing a
    streamed response, and a shared instance would let concurrent sessions observe
    each other's partial state.
    """
    return _ADAPTERS[api_style]()


class GenericBackend:
    def __init__(
        self,
        *,
        client: VibeAsyncHTTPClient | None = None,
        provider: ProviderConfig,
        timeout: float = 720.0,
        retry_max_elapsed_time: float = 300.0,
        on_retry: RetryObserver | None = None,
        pacer: AdaptivePacer | None = None,
        enable_otel: bool = False,
    ) -> None:
        """Initialize the backend.

        Args:
            client: Optional Vibe HTTP client to use. If not provided, one will be created.
            retry_max_elapsed_time: Total wall-clock budget for retrying retryable
                failures (429, 5xx, network/timeout errors). Retries continue for as
                long as the budget allows, matching the Mistral backend's behavior.
            on_retry: Notified before each retry backoff.
            pacer: Adaptive call pacing. Defaults to a fresh `AdaptivePacer`, which
                is a no-op until the first rate limit and then spaces subsequent
                calls out, recovering once a quiet window passes. Inject a custom
                one to tune the schedule or to stub timing in tests.
        """
        self._client = client
        self._owns_client = client is None
        self._provider = provider
        self._timeout = timeout
        self._retry_max_elapsed_time = retry_max_elapsed_time
        self._pacer = pacer if pacer is not None else AdaptivePacer()
        self._user_on_retry = on_retry

        async def paced_on_retry(reason: RetryReason) -> None:
            if reason.category is RetryCategory.RATE_LIMITED:
                self._pacer.on_rate_limited()
            if self._user_on_retry is not None:
                await self._user_on_retry(reason)

        # Budget-bounded retry: retry for as long as `retry_max_elapsed_time`
        # allows rather than for a fixed attempt count, so a transient 429 or
        # outage is waited out the same way the Mistral SDK backend does.
        retried = async_retry(
            tries=None, max_elapsed_time=retry_max_elapsed_time, on_retry=paced_on_retry
        )(self._send_request)
        self._make_request = self._pace(retried)
        retried_stream = async_generator_retry(
            tries=None, max_elapsed_time=retry_max_elapsed_time, on_retry=paced_on_retry
        )(self._send_parsed_streaming_request)
        self._make_streaming_request = self._pace_stream(retried_stream)
        self._enable_otel = enable_otel

    def _pace(self, fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            await self._pacer.acquire()
            succeeded = False
            try:
                result = await fn(*args, **kwargs)
                succeeded = True
                return result
            finally:
                if succeeded:
                    self._pacer.on_success()
                else:
                    self._pacer.on_failure()

        return wrapper

    def _pace_stream(
        self, fn: Callable[..., AsyncGenerator[Any]]
    ) -> Callable[..., AsyncGenerator[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> AsyncGenerator[Any]:
            await self._pacer.acquire()
            succeeded = False
            try:
                async with aclosing(fn(*args, **kwargs)) as stream:
                    async for item in stream:
                        yield item
                    succeeded = True
            finally:
                if succeeded:
                    self._pacer.on_success()
                else:
                    self._pacer.on_failure()

        return wrapper

    async def __aenter__(self) -> GenericBackend:
        if self._client is None:
            self._client = VibeAsyncHTTPClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=10,
                    keepalive_expiry=MODEL_HTTP_KEEPALIVE_EXPIRY_SECONDS,
                ),
                verify=build_ssl_context(),
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> VibeAsyncHTTPClient:
        if self._client is None:
            self._client = VibeAsyncHTTPClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=10,
                    keepalive_expiry=MODEL_HTTP_KEEPALIVE_EXPIRY_SECONDS,
                ),
                verify=build_ssl_context(),
            )
            self._owns_client = True
        return self._client

    async def complete(
        self,
        *,
        model: ModelConfig,
        messages: Sequence[LLMMessage],
        temperature: float = 0.2,
        tools: list[AvailableTool] | None = None,
        max_tokens: int | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> LLMChunk:
        api_key = resolve_api_key(self._provider.api_key_env_var)

        api_style = getattr(self._provider, "api_style", "openai")
        adapter = _get_adapter(api_style)

        req = adapter.prepare_request(
            model_name=model.name,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            enable_streaming=False,
            provider=self._provider,
            api_key=api_key,
            thinking=model.thinking,
        )

        headers = req.headers
        if extra_headers:
            headers.update(extra_headers)

        base = req.base_url or self._provider.api_base
        url = f"{base}{req.endpoint}"

        async with self._model_call_span(
            model=model,
            api_style=api_style,
            streaming=False,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata=metadata,
            url=url,
        ) as span:

            def record_response_status(status_code: int) -> None:
                set_model_call_http_status(span, status_code)

            try:
                response = await self._make_request(
                    url, req.body, headers, on_response_status=record_response_status
                )
            except httpx.HTTPStatusError as e:
                set_model_call_http_status(span, e.response.status_code)
                raise BackendErrorBuilder.build_http_error(
                    provider=self._provider.name,
                    endpoint=url,
                    error=e,
                    response=e.response,
                    model=model.name,
                    messages=messages,
                    temperature=temperature,
                    has_tools=bool(tools),
                    tool_choice=tool_choice,
                ) from e
            except httpx.RequestError as e:
                raise BackendErrorBuilder.build_request_error(
                    provider=self._provider.name,
                    endpoint=url,
                    error=e,
                    model=model.name,
                    messages=messages,
                    temperature=temperature,
                    has_tools=bool(tools),
                    tool_choice=tool_choice,
                ) from e

            set_model_call_http_status(span, response.status_code)
            set_model_call_response_metadata(span, response.data)
            chunk = adapter.parse_response(response.data, self._provider)
            if chunk.usage is not None and _reports_usage(response.data):
                set_model_call_usage(
                    span,
                    prompt_tokens=chunk.usage.prompt_tokens,
                    completion_tokens=chunk.usage.completion_tokens,
                )
            return chunk

    async def complete_streaming(
        self,
        *,
        model: ModelConfig,
        messages: Sequence[LLMMessage],
        temperature: float = 0.2,
        tools: list[AvailableTool] | None = None,
        max_tokens: int | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        api_key = resolve_api_key(self._provider.api_key_env_var)

        api_style = getattr(self._provider, "api_style", "openai")
        adapter = _get_adapter(api_style)

        req = adapter.prepare_request(
            model_name=model.name,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            enable_streaming=True,
            provider=self._provider,
            api_key=api_key,
            thinking=model.thinking,
        )

        headers = req.headers
        if extra_headers:
            headers.update(extra_headers)

        base = req.base_url or self._provider.api_base
        url = f"{base}{req.endpoint}"

        async with self._model_call_span(
            model=model,
            api_style=api_style,
            streaming=True,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata=metadata,
            url=url,
        ) as span:
            usage = LLMUsage()
            has_usage = False

            def record_response_status(status_code: int) -> None:
                set_model_call_http_status(span, status_code)

            try:
                async with aclosing(
                    self._make_streaming_request(
                        url,
                        req.body,
                        headers,
                        adapter,
                        on_response_status=record_response_status,
                    )
                ) as stream:
                    async for stream_chunk in stream:
                        set_model_call_response_metadata(span, stream_chunk.data)
                        chunk = stream_chunk.chunk
                        if chunk.usage is not None and _reports_usage(
                            stream_chunk.data
                        ):
                            has_usage = True
                            usage += chunk.usage
                        yield chunk
            except OpenAIResponsesStreamError as e:
                raise BackendErrorBuilder.build_stream_error(
                    provider=self._provider.name,
                    endpoint=url,
                    status=e.status,
                    error_type=e.error_type,
                    error_message=e.message,
                    model=model.name,
                    messages=messages,
                    temperature=temperature,
                    has_tools=bool(tools),
                    tool_choice=tool_choice,
                ) from e
            except httpx.HTTPStatusError as e:
                set_model_call_http_status(span, e.response.status_code)
                raise BackendErrorBuilder.build_http_error(
                    provider=self._provider.name,
                    endpoint=url,
                    error=e,
                    response=e.response,
                    model=model.name,
                    messages=messages,
                    temperature=temperature,
                    has_tools=bool(tools),
                    tool_choice=tool_choice,
                ) from e
            except httpx.RequestError as e:
                raise BackendErrorBuilder.build_request_error(
                    provider=self._provider.name,
                    endpoint=url,
                    error=e,
                    model=model.name,
                    messages=messages,
                    temperature=temperature,
                    has_tools=bool(tools),
                    tool_choice=tool_choice,
                ) from e
            if has_usage:
                set_model_call_usage(
                    span,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                )

    class HTTPResponse(NamedTuple):
        data: dict[str, Any]
        status_code: int

    class HTTPStreamChunk(NamedTuple):
        data: dict[str, Any]

    def _model_call_span(
        self,
        *,
        model: ModelConfig,
        api_style: str,
        streaming: bool,
        temperature: float | None,
        max_tokens: int | None,
        metadata: dict[str, str] | None,
        url: str,
    ) -> AbstractAsyncContextManager[trace.Span | None]:
        if not self._enable_otel:
            return nullcontext(None)

        return model_call_span(
            provider_name=self._provider.name,
            provider_api_style=api_style,
            model=model.name,
            streaming=streaming,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata=metadata,
            http_url=url,
        )

    async def _send_request(
        self,
        url: str,
        data: bytes,
        headers: dict[str, str],
        on_response_status: Callable[[int], None] | None = None,
    ) -> HTTPResponse:
        client = self._get_client()
        response = await client.post(url, content=data, headers=headers)
        response.raise_for_status()
        if on_response_status is not None:
            on_response_status(response.status_code)

        response_body = response.json()
        return self.HTTPResponse(response_body, response.status_code)

    async def _send_parsed_streaming_request(
        self,
        url: str,
        data: bytes,
        headers: dict[str, str],
        adapter: APIAdapter,
        on_response_status: Callable[[int], None] | None = None,
    ) -> AsyncGenerator[ParsedStreamChunk]:
        async with aclosing(
            self._send_streaming_request(url, data, headers, on_response_status)
        ) as response_stream:
            responses = (stream_chunk.data async for stream_chunk in response_stream)
            async with aclosing(
                adapter.parse_stream(responses, self._provider)
            ) as parsed_stream:
                async for parsed in parsed_stream:
                    yield parsed

    async def _send_streaming_request(
        self,
        url: str,
        data: bytes,
        headers: dict[str, str],
        on_response_status: Callable[[int], None] | None = None,
    ) -> AsyncGenerator[HTTPStreamChunk]:
        client = self._get_client()
        async with client.stream(
            method="POST", url=url, content=data, headers=headers
        ) as response:
            if not response.is_success:
                await response.aread()
            response.raise_for_status()
            if on_response_status is not None:
                on_response_status(response.status_code)
            async for line in iter_sse_lines(response):
                if line.strip() == "":
                    continue

                if line.startswith(":"):
                    continue

                DELIM_CHAR = ":"
                if f"{DELIM_CHAR} " not in line:
                    raise ValueError(
                        f"Stream chunk improperly formatted. "
                        f"Expected `key{DELIM_CHAR} value`."
                    )
                delim_index = line.find(DELIM_CHAR)
                key = line[0:delim_index]
                value = line[delim_index + 2 :]

                if key != "data":
                    # This might be the case with openrouter, so we just ignore it
                    continue
                if value.strip() == "[DONE]":
                    return
                try:
                    chunk_data = json.loads(value.strip())
                except json.JSONDecodeError:
                    raise ValueError("Stream chunk contains malformed JSON.") from None
                yield self.HTTPStreamChunk(data=chunk_data)

    async def close(self) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None
