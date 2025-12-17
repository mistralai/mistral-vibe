from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
import json
import os
import types
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Protocol, TypeVar

import httpx

import time

try:
    from opentelemetry.trace import SpanKind

    from vibe.core.observability.tracing import (
        LLM_REQUEST_ATTRIBUTES,
        async_run_in_trace_span,
        build_llm_request_attributes,
        run_in_trace_span_async,
    )
    from vibe.core.observability.metrics import (
        get_metrics_manager,
        GENAI_CLIENT_TOKEN_USAGE,
        GENAI_CLIENT_OPERATION_DURATION,
    )
    from vibe.core.observability.semconv import (
        ATTR_GEN_AI_OPERATION_NAME,
        ATTR_GEN_AI_RESPONSE_MODEL,
        ATTR_GEN_AI_SYSTEM,
        ATTR_GEN_AI_TOKEN_TYPE,
        ATTR_GEN_AI_USAGE_INPUT_TOKENS,
        ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
        ATTR_GEN_AI_USAGE_TOTAL_TOKENS,
        OP_CHAT,
        SYSTEM_OPENAI,
        TOKEN_TYPE_INPUT,
        TOKEN_TYPE_OUTPUT,
    )

    _LLM_TRACING_AVAILABLE = True
except ImportError:
    SpanKind = None
    LLM_REQUEST_ATTRIBUTES = {}
    async_run_in_trace_span = None
    build_llm_request_attributes = None
    run_in_trace_span_async = None
    get_metrics_manager = None
    GENAI_CLIENT_TOKEN_USAGE = None
    GENAI_CLIENT_OPERATION_DURATION = None
    ATTR_GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
    ATTR_GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
    ATTR_GEN_AI_SYSTEM = "gen_ai.system"
    ATTR_GEN_AI_TOKEN_TYPE = "gen_ai.token.type"
    ATTR_GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    ATTR_GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    ATTR_GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
    OP_CHAT = "chat"
    SYSTEM_OPENAI = "openai"
    TOKEN_TYPE_INPUT = "input"
    TOKEN_TYPE_OUTPUT = "output"
    _LLM_TRACING_AVAILABLE = False


def _record_genai_metrics(
    duration_s: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model_name: str = "",
    provider_name: str = "",
) -> None:
    """Record GenAI metrics following OTel GenAI semantic conventions.
    
    Args:
        duration_s: Duration in seconds (OTel convention)
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        model_name: Model name for attributes
        provider_name: Provider name for gen_ai.system attribute
    """
    if get_metrics_manager is None:
        return
    metrics = get_metrics_manager()
    
    base_attrs = {
        ATTR_GEN_AI_OPERATION_NAME: OP_CHAT,
        ATTR_GEN_AI_SYSTEM: provider_name or SYSTEM_OPENAI,
    }
    if model_name:
        base_attrs[ATTR_GEN_AI_RESPONSE_MODEL] = model_name
    
    # Record input tokens with token type attribute
    if prompt_tokens > 0:
        input_attrs = {**base_attrs, ATTR_GEN_AI_TOKEN_TYPE: TOKEN_TYPE_INPUT}
        metrics.get_counter(GENAI_CLIENT_TOKEN_USAGE).add(prompt_tokens, input_attrs)
    
    # Record output tokens with token type attribute
    if completion_tokens > 0:
        output_attrs = {**base_attrs, ATTR_GEN_AI_TOKEN_TYPE: TOKEN_TYPE_OUTPUT}
        metrics.get_counter(GENAI_CLIENT_TOKEN_USAGE).add(completion_tokens, output_attrs)
    
    # Record duration in seconds
    metrics.get_histogram(GENAI_CLIENT_OPERATION_DURATION).record(duration_s, base_attrs)

from vibe.core.llm.exceptions import BackendErrorBuilder
from vibe.core.types import (
    AvailableTool,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StrToolChoice,
)
from vibe.core.utils import async_generator_retry, async_retry

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig, ProviderConfig


class PreparedRequest(NamedTuple):
    endpoint: str
    headers: dict[str, str]
    body: bytes


class APIAdapter(Protocol):
    endpoint: ClassVar[str]

    def prepare_request(
        self,
        *,
        model_name: str,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderConfig,
        api_key: str | None = None,
    ) -> PreparedRequest: ...

    def parse_response(self, data: dict[str, Any]) -> LLMChunk: ...


BACKEND_ADAPTERS: dict[str, APIAdapter] = {}

T = TypeVar("T", bound=APIAdapter)


def register_adapter(
    adapters: dict[str, APIAdapter], name: str
) -> Callable[[type[T]], type[T]]:

    def decorator(cls: type[T]) -> type[T]:
        adapters[name] = cls()
        return cls

    return decorator


def _should_trace_llm() -> bool:
    return (
        _LLM_TRACING_AVAILABLE
        and run_in_trace_span_async is not None
        and async_run_in_trace_span is not None
        and SpanKind is not None
    )


@register_adapter(BACKEND_ADAPTERS, "openai")
class OpenAIAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/chat/completions"

    def build_payload(
        self,
        model_name: str,
        converted_messages: list[dict[str, Any]],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> dict[str, Any]:
        payload = {
            "model": model_name,
            "messages": converted_messages,
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = [tool.model_dump(exclude_none=True) for tool in tools]
        if tool_choice:
            payload["tool_choice"] = (
                tool_choice
                if isinstance(tool_choice, str)
                else tool_choice.model_dump()
            )
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        return payload

    def build_headers(self, api_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def prepare_request(
        self,
        *,
        model_name: str,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderConfig,
        api_key: str | None = None,
    ) -> PreparedRequest:
        converted_messages = [msg.model_dump(exclude_none=True) for msg in messages]

        payload = self.build_payload(
            model_name, converted_messages, temperature, tools, max_tokens, tool_choice
        )

        if enable_streaming:
            payload["stream"] = True
            stream_options = {"include_usage": True}
            if provider.name == "mistral":
                stream_options["stream_tool_calls"] = True
            payload["stream_options"] = stream_options

        headers = self.build_headers(api_key)

        body = json.dumps(payload).encode("utf-8")

        return PreparedRequest(self.endpoint, headers, body)

    def parse_response(self, data: dict[str, Any]) -> LLMChunk:
        if data.get("choices"):
            if "message" in data["choices"][0]:
                message = LLMMessage.model_validate(data["choices"][0]["message"])
            elif "delta" in data["choices"][0]:
                message = LLMMessage.model_validate(data["choices"][0]["delta"])
            else:
                raise ValueError("Invalid response data")
            finish_reason = data["choices"][0].get("finish_reason", None)

        elif "message" in data:
            message = LLMMessage.model_validate(data["message"])
            finish_reason = data["choices"][0].get("finish_reason", None)
        elif "delta" in data:
            message = LLMMessage.model_validate(data["delta"])
            finish_reason = None
        else:
            message = LLMMessage(role=Role.assistant, content="")
            finish_reason = None

        usage_data = data.get("usage") or {}
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
        )

        return LLMChunk(message=message, usage=usage, finish_reason=finish_reason)


class GenericBackend:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        provider: ProviderConfig,
        timeout: float = 720.0,
    ) -> None:
        """Initialize the backend.

        Args:
            client: Optional httpx client to use. If not provided, one will be created.
        """
        self._client = client
        self._owns_client = client is None
        self._provider = provider
        self._timeout = timeout

    async def __aenter__(self) -> GenericBackend:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
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

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
            self._owns_client = True
        return self._client

    async def complete(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        tools: list[AvailableTool] | None = None,
        max_tokens: int | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> LLMChunk:
        api_key = (
            os.getenv(self._provider.api_key_env_var)
            if self._provider.api_key_env_var
            else None
        )

        api_style = getattr(self._provider, "api_style", "openai")
        adapter = BACKEND_ADAPTERS[api_style]

        endpoint, headers, body = adapter.prepare_request(
            model_name=model.name,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            enable_streaming=False,
            provider=self._provider,
            api_key=api_key,
        )

        if extra_headers:
            headers.update(extra_headers)

        url = f"{self._provider.api_base}{endpoint}"

        async def _perform_request() -> LLMChunk:
            try:
                res_data, _ = await self._make_request(url, body, headers)
                return adapter.parse_response(res_data)

            except httpx.HTTPStatusError as e:
                raise BackendErrorBuilder.build_http_error(
                    provider=self._provider.name,
                    endpoint=url,
                    response=e.response,
                    headers=dict(e.response.headers.items()),
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

        if _should_trace_llm():
            async def _execute(span_payload: dict[str, Any]) -> LLMChunk:
                metadata = span_payload["metadata"]
                attributes = metadata.setdefault("attributes", {})
                attributes.update(
                    build_llm_request_attributes(
                        model_name=model.name,
                        provider_name=self._provider.name,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        streaming=False,
                        tool_count=len(tools or []),
                        endpoint=url,
                    )
                )
                metadata["input"] = {
                    "message_count": len(messages),
                    "tool_count": len(tools or []),
                    "streaming": False,
                }
                start_time = time.time()
                result = await _perform_request()
                duration_s = time.time() - start_time
                prompt_tokens = 0
                completion_tokens = 0
                if getattr(result, "usage", None):
                    prompt_tokens = result.usage.prompt_tokens or 0
                    completion_tokens = result.usage.completion_tokens or 0
                    attributes[ATTR_GEN_AI_USAGE_INPUT_TOKENS] = prompt_tokens
                    attributes[ATTR_GEN_AI_USAGE_OUTPUT_TOKENS] = completion_tokens
                    attributes[ATTR_GEN_AI_USAGE_TOTAL_TOKENS] = (
                        prompt_tokens + completion_tokens
                    )
                _record_genai_metrics(duration_s, prompt_tokens, completion_tokens, model.name, self._provider.name)
                # Record output (LLM response content)
                if result and hasattr(result, "content") and result.content:
                    metadata["output"] = {"content": result.content}
                return result

            return await run_in_trace_span_async(
                "LLMRequest",
                _execute,
                span_kind=SpanKind.CLIENT,
                initial_attributes=LLM_REQUEST_ATTRIBUTES,
            )

        # Record metrics even when tracing is disabled
        start_time = time.time()
        result = await _perform_request()
        duration_s = time.time() - start_time
        prompt_tokens = 0
        completion_tokens = 0
        if getattr(result, "usage", None):
            prompt_tokens = result.usage.prompt_tokens or 0
            completion_tokens = result.usage.completion_tokens or 0
        _record_genai_metrics(duration_s, prompt_tokens, completion_tokens, model.name, self._provider.name)
        return result

    async def complete_streaming(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.2,
        tools: list[AvailableTool] | None = None,
        max_tokens: int | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        api_key = (
            os.getenv(self._provider.api_key_env_var)
            if self._provider.api_key_env_var
            else None
        )

        api_style = getattr(self._provider, "api_style", "openai")
        adapter = BACKEND_ADAPTERS[api_style]

        endpoint, headers, body = adapter.prepare_request(
            model_name=model.name,
            messages=messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            enable_streaming=True,
            provider=self._provider,
            api_key=api_key,
        )

        if extra_headers:
            headers.update(extra_headers)

        url = f"{self._provider.api_base}{endpoint}"

        async def _stream_raw() -> AsyncGenerator[LLMChunk, None]:
            try:
                async for res_data in self._make_streaming_request(
                    url, body, headers
                ):
                    yield adapter.parse_response(res_data)

            except httpx.HTTPStatusError as e:
                raise BackendErrorBuilder.build_http_error(
                    provider=self._provider.name,
                    endpoint=url,
                    response=e.response,
                    headers=dict(e.response.headers.items()),
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

        if not _should_trace_llm():
            start_time = time.time()
            last_usage: LLMUsage | None = None
            async for chunk in _stream_raw():
                if getattr(chunk, "usage", None):
                    last_usage = chunk.usage
                yield chunk
            duration_s = time.time() - start_time
            prompt_tokens = last_usage.prompt_tokens or 0 if last_usage else 0
            completion_tokens = last_usage.completion_tokens or 0 if last_usage else 0
            _record_genai_metrics(duration_s, prompt_tokens, completion_tokens, model.name, self._provider.name)
            return

        async def _wrapped() -> AsyncGenerator[LLMChunk, None]:
            async with async_run_in_trace_span(
                "LLMRequest",
                span_kind=SpanKind.CLIENT,
                initial_attributes=LLM_REQUEST_ATTRIBUTES,
            ) as (metadata, end_span):
                attributes = metadata.setdefault("attributes", {})
                attributes.update(
                    build_llm_request_attributes(
                        model_name=model.name,
                        provider_name=self._provider.name,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        streaming=True,
                        tool_count=len(tools or []),
                        endpoint=url,
                    )
                )
                metadata["input"] = {
                    "message_count": len(messages),
                    "tool_count": len(tools or []),
                    "streaming": True,
                }
                start_time = time.time()
                last_usage: LLMUsage | None = None
                caught: BaseException | None = None
                output_chunks: list[str] = []
                try:
                    async for chunk in _stream_raw():
                        if getattr(chunk, "usage", None):
                            last_usage = chunk.usage
                        # Collect content for output
                        if hasattr(chunk, "content") and chunk.content:
                            output_chunks.append(str(chunk.content))
                        yield chunk
                except BaseException as exc:
                    caught = exc
                    metadata["error"] = exc
                    raise
                finally:
                    duration_s = time.time() - start_time
                    prompt_tokens = last_usage.prompt_tokens or 0 if last_usage else 0
                    completion_tokens = last_usage.completion_tokens or 0 if last_usage else 0
                    if last_usage:
                        attributes[ATTR_GEN_AI_USAGE_INPUT_TOKENS] = prompt_tokens
                        attributes[ATTR_GEN_AI_USAGE_OUTPUT_TOKENS] = completion_tokens
                        attributes[ATTR_GEN_AI_USAGE_TOTAL_TOKENS] = prompt_tokens + completion_tokens
                    _record_genai_metrics(duration_s, prompt_tokens, completion_tokens, model.name, self._provider.name)
                    # Record output (streamed content)
                    if output_chunks:
                        metadata["output"] = {"content": "".join(output_chunks)}
                    await end_span()

        async for chunk in _wrapped():
            yield chunk

    class HTTPResponse(NamedTuple):
        data: dict[str, Any]
        headers: dict[str, str]

    @async_retry(tries=3)
    async def _make_request(
        self, url: str, data: bytes, headers: dict[str, str]
    ) -> HTTPResponse:
        client = self._get_client()
        response = await client.post(url, content=data, headers=headers)
        response.raise_for_status()

        response_headers = dict(response.headers.items())
        response_body = response.json()
        return self.HTTPResponse(response_body, response_headers)

    @async_generator_retry(tries=3)
    async def _make_streaming_request(
        self, url: str, data: bytes, headers: dict[str, str]
    ) -> AsyncGenerator[dict[str, Any]]:
        client = self._get_client()
        async with client.stream(
            method="POST", url=url, content=data, headers=headers
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip() == "":
                    continue

                DELIM_CHAR = ":"
                assert f"{DELIM_CHAR} " in line, "line should look like `key: value`"
                delim_index = line.find(DELIM_CHAR)
                key = line[0:delim_index]
                value = line[delim_index + 2 :]

                if key != "data":
                    # This might be the case with openrouter, so we just ignore it
                    continue
                if value == "[DONE]":
                    return
                yield json.loads(value.strip())

    async def count_tokens(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        tools: list[AvailableTool] | None = None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> int:
        probe_messages = list(messages)
        if not probe_messages or probe_messages[-1].role != Role.user:
            probe_messages.append(LLMMessage(role=Role.user, content=""))

        result = await self.complete(
            model=model,
            messages=probe_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=16,  # Minimal amount for openrouter with openai models
            tool_choice=tool_choice,
            extra_headers=extra_headers,
        )
        assert result.usage is not None, (
            "Usage should be present in non-streaming completions"
        )

        return result.usage.prompt_tokens

    async def close(self) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None
