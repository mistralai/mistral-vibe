"""Test data for this module was generated using real LLM provider API responses,
with responses simplified and formatted to make them readable and maintainable.

To update or modify test parameters:
1. Make actual API calls to the target providers
2. Use the raw API responses as a base for updating test data
3. Simplify only where necessary for readability while preserving core structure

The closer test data remains to real API responses, the more reliable and accurate
the tests will be. Always prefer real API data over manually constructed examples.
"""

from __future__ import annotations

import json
from typing import ClassVar, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from mistralai.client.errors import SDKError
from mistralai.client.models import AssistantMessage
from mistralai.client.utils.retries import BackoffStrategy, RetryConfig
import pytest
import respx

from tests.backend.data import Chunk, JsonResponse, ResultData, Url
from tests.backend.data.fireworks import (
    SIMPLE_CONVERSATION_PARAMS as FIREWORKS_SIMPLE_CONVERSATION_PARAMS,
    STREAMED_SIMPLE_CONVERSATION_PARAMS as FIREWORKS_STREAMED_SIMPLE_CONVERSATION_PARAMS,
    STREAMED_TOOL_CONVERSATION_PARAMS as FIREWORKS_STREAMED_TOOL_CONVERSATION_PARAMS,
    TOOL_CONVERSATION_PARAMS as FIREWORKS_TOOL_CONVERSATION_PARAMS,
)
from tests.backend.data.mistral import (
    SIMPLE_CONVERSATION_PARAMS as MISTRAL_SIMPLE_CONVERSATION_PARAMS,
    STREAMED_EMPTY_CHOICES_PARAMS as MISTRAL_STREAMED_EMPTY_CHOICES_PARAMS,
    STREAMED_SIMPLE_CONVERSATION_PARAMS as MISTRAL_STREAMED_SIMPLE_CONVERSATION_PARAMS,
    STREAMED_TOOL_CONVERSATION_PARAMS as MISTRAL_STREAMED_TOOL_CONVERSATION_PARAMS,
    TOOL_CONVERSATION_PARAMS as MISTRAL_TOOL_CONVERSATION_PARAMS,
)
from tests.constants import CHAT_COMPLETIONS_PATH
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm.backend.base import build_chat_payload
from vibe.core.llm.backend.factory import BACKEND_FACTORY, create_backend
from vibe.core.llm.backend.generic import GenericBackend, OpenAIAdapter
from vibe.core.llm.backend.mistral import MistralBackend, MistralMapper, _cached_tokens
from vibe.core.llm.exceptions import BackendError, BackendErrorBuilder
from vibe.core.llm.types import BackendLike
from vibe.core.types import Backend, FunctionCall, LLMChunk, LLMMessage, Role, ToolCall
from vibe.utils.http import VibeAsyncHTTPClient, get_user_agent
from vibe.utils.tool_presentation import (
    EffectCallDisplay,
    ToolCallPresentation,
    ToolEffectKind,
)


def test_generic_backend_keeps_idle_connections_for_tool_turns() -> None:
    provider = ProviderConfig(
        name="generic", api_base="https://example.com/v1", api_key_env_var="API_KEY"
    )
    with patch(
        "vibe.core.llm.backend.generic.VibeAsyncHTTPClient"
    ) as create_http_client:
        GenericBackend(provider=provider)._get_client()

    limits = create_http_client.call_args.kwargs["limits"]
    assert limits.keepalive_expiry == 60.0


@pytest.mark.asyncio
async def test_mistral_backend_keeps_idle_connections_for_tool_turns() -> None:
    provider = ProviderConfig(
        name="mistral", api_base="https://api.mistral.ai/v1", api_key_env_var="API_KEY"
    )
    backend = MistralBackend(provider=provider)
    with (
        patch(
            "vibe.core.llm.backend.mistral.VibeAsyncHTTPClient"
        ) as create_http_client,
        patch("vibe.core.llm.backend.mistral.Mistral"),
        patch("vibe.core.llm.backend.mistral._register_retry_hook"),
    ):
        backend._create_mistral_client()

    limits = create_http_client.call_args.kwargs["limits"]
    assert limits.keepalive_expiry == 60.0


@pytest.mark.asyncio
async def test_mistral_backend_bounds_the_connection_pool() -> None:
    provider = ProviderConfig(
        name="mistral", api_base="https://api.mistral.ai/v1", api_key_env_var="API_KEY"
    )
    backend = MistralBackend(provider=provider)
    with (
        patch(
            "vibe.core.llm.backend.mistral.VibeAsyncHTTPClient"
        ) as create_http_client,
        patch("vibe.core.llm.backend.mistral.Mistral"),
        patch("vibe.core.llm.backend.mistral._register_retry_hook"),
    ):
        backend._create_mistral_client()

    limits = create_http_client.call_args.kwargs["limits"]
    assert limits.max_connections == 20


def test_internal_tool_presentation_is_not_sent_to_provider() -> None:
    message = LLMMessage(
        role=Role.assistant,
        tool_calls=[
            ToolCall(
                id="call-1",
                function=FunctionCall(name="bash", arguments='{"command":"pwd"}'),
                presentation=ToolCallPresentation(
                    kind=ToolEffectKind.SHELL,
                    display=EffectCallDisplay(
                        summary="bash: pwd", status_text="Running command"
                    ),
                ),
            )
        ],
    )

    request = OpenAIAdapter().prepare_request(
        model_name="model",
        messages=[message],
        temperature=0.0,
        tools=None,
        max_tokens=None,
        tool_choice=None,
        enable_streaming=False,
        provider=ProviderConfig(
            name="provider", api_base="https://example.com/v1", api_key_env_var=""
        ),
    )

    payload = json.loads(request.body)
    assert "presentation" not in payload["messages"][0]["tool_calls"][0]


class TestBackend:
    @staticmethod
    def _build_fast_retry_config() -> RetryConfig:
        return RetryConfig(
            strategy="backoff",
            backoff=BackoffStrategy(
                initial_interval=1, max_interval=1, exponent=1, max_elapsed_time=1
            ),
            retry_connection_errors=False,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base_url,json_response,result_data",
        [
            *FIREWORKS_SIMPLE_CONVERSATION_PARAMS,
            *FIREWORKS_TOOL_CONVERSATION_PARAMS,
            *MISTRAL_SIMPLE_CONVERSATION_PARAMS,
            *MISTRAL_TOOL_CONVERSATION_PARAMS,
        ],
    )
    async def test_backend_complete(
        self, base_url: Url, json_response: JsonResponse, result_data: ResultData
    ):
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(status_code=200, json=json_response)
            )
            provider = ProviderConfig(
                name="provider_name",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
            )

            BackendClasses = [
                GenericBackend,
                *([MistralBackend] if base_url == "https://api.mistral.ai" else []),
            ]
            for BackendClass in BackendClasses:
                backend: BackendLike = BackendClass(provider=provider)
                model = ModelConfig(
                    name="model_name", provider="provider_name", alias="model_alias"
                )
                messages = [LLMMessage(role=Role.user, content="Just say hi")]

                result = await backend.complete(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                )

                assert result.message.content == result_data["message"]
                assert result.usage is not None
                assert (
                    result.usage.prompt_tokens == result_data["usage"]["prompt_tokens"]
                )
                assert (
                    result.usage.completion_tokens
                    == result_data["usage"]["completion_tokens"]
                )
                assert result.stop is not None

                if result.message.tool_calls is None:
                    return

                assert len(result.message.tool_calls) == len(result_data["tool_calls"])
                for i, tool_call in enumerate[ToolCall](result.message.tool_calls):
                    assert (
                        tool_call.function.name == result_data["tool_calls"][i]["name"]
                    )
                    assert (
                        tool_call.function.arguments
                        == result_data["tool_calls"][i]["arguments"]
                    )
                    assert tool_call.index == result_data["tool_calls"][i]["index"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base_url,chunks,result_data",
        [
            *FIREWORKS_STREAMED_SIMPLE_CONVERSATION_PARAMS,
            *FIREWORKS_STREAMED_TOOL_CONVERSATION_PARAMS,
            *MISTRAL_STREAMED_SIMPLE_CONVERSATION_PARAMS,
            *MISTRAL_STREAMED_TOOL_CONVERSATION_PARAMS,
            *MISTRAL_STREAMED_EMPTY_CHOICES_PARAMS,
        ],
    )
    async def test_backend_complete_streaming(
        self, base_url: Url, chunks: list[Chunk], result_data: list[ResultData]
    ):
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            provider = ProviderConfig(
                name="provider_name",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
            )
            BackendClasses = [
                GenericBackend,
                *([MistralBackend] if base_url == "https://api.mistral.ai" else []),
            ]
            for BackendClass in BackendClasses:
                backend: BackendLike = BackendClass(provider=provider)
                model = ModelConfig(
                    name="model_name", provider="provider_name", alias="model_alias"
                )

                messages = [
                    LLMMessage(role=Role.user, content="List files in current dir")
                ]

                results: list[LLMChunk] = []
                async for result in backend.complete_streaming(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                ):
                    results.append(result)

                assert any(result.stop is not None for result in results)

                for result, expected_result in zip(results, result_data, strict=True):
                    assert result.message.content == expected_result["message"]
                    assert result.usage is not None
                    assert (
                        result.usage.prompt_tokens
                        == expected_result["usage"]["prompt_tokens"]
                    )
                    assert (
                        result.usage.completion_tokens
                        == expected_result["usage"]["completion_tokens"]
                    )

                    if result.message.tool_calls is None:
                        continue

                    for i, tool_call in enumerate(result.message.tool_calls):
                        assert (
                            tool_call.function.name
                            == expected_result["tool_calls"][i]["name"]
                        )
                        assert (
                            tool_call.function.arguments
                            == expected_result["tool_calls"][i]["arguments"]
                        )
                        assert (
                            tool_call.index == expected_result["tool_calls"][i]["index"]
                        )

    @pytest.mark.asyncio
    async def test_mistral_backend_streaming_closes_response_on_early_exit(self):
        # Regression: terminating the streaming generator early (user interrupt,
        # max-tokens truncation) used to leave the SDK stream -- and the httpx
        # connection it holds -- open until async-gen finalization, exhausting
        # the pool over a long session. The fix closes the SDK stream via its
        # async context manager, so ``EventStreamAsync.__aexit__`` (which calls
        # ``response.aclose()``) runs synchronously on every exit path.
        from mistralai.client.utils.eventstreaming import EventStreamAsync

        base_url, chunks, _ = MISTRAL_STREAMED_SIMPLE_CONVERSATION_PARAMS[0]
        aexit_called = False
        original_aexit = EventStreamAsync.__aexit__

        async def spy_aexit(self, exc_type, exc_val, exc_tb):
            nonlocal aexit_called
            aexit_called = True
            return await original_aexit(self, exc_type, exc_val, exc_tb)

        with (
            respx.mock(base_url=base_url) as mock_api,
            patch.object(EventStreamAsync, "__aexit__", spy_aexit),
        ):
            route = mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            provider = ProviderConfig(
                name="provider_name",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
            )
            backend = MistralBackend(provider=provider)
            model = ModelConfig(
                name="model_name", provider="provider_name", alias="model_alias"
            )
            messages = [LLMMessage(role=Role.user, content="List files")]

            generator = backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )
            first = await generator.__anext__()
            assert first is not None
            # Simulate the consumer dropping the stream mid-flight.
            await generator.aclose()

            assert aexit_called, "SDK stream was not closed on early exit"
            assert route.calls.last.response.is_closed

    @pytest.mark.asyncio
    async def test_backend_complete_streaming_keeps_unicode_line_breaks(self):
        content = "first\u2028second\u0085third"
        chunk = json.dumps(
            {
                "id": "fake_id_1234",
                "object": "chat.completion.chunk",
                "created": 1234567890,
                "model": "model_name",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": content},
                        "finish_reason": None,
                    }
                ],
            },
            ensure_ascii=False,
        ).encode()
        with respx.mock(base_url="https://api.fireworks.ai") as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(
                        stream=b"data: " + chunk + b"\n\ndata: [DONE]\n\n"
                    ),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            provider = ProviderConfig(
                name="provider_name",
                api_base="https://api.fireworks.ai/v1",
                api_key_env_var="API_KEY",
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(
                name="model_name", provider="provider_name", alias="model_alias"
            )

            results: list[LLMChunk] = []
            async for result in backend.complete_streaming(
                model=model,
                messages=[LLMMessage(role=Role.user, content="hi")],
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            ):
                results.append(result)

        assert [result.message.content for result in results] == [content]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base_url,backend_class,response",
        [
            (
                "https://api.fireworks.ai",
                GenericBackend,
                httpx.Response(status_code=500, text="Internal Server Error"),
            ),
            (
                "https://api.fireworks.ai",
                GenericBackend,
                httpx.Response(status_code=429, text="Rate Limit Exceeded"),
            ),
            (
                "https://api.mistral.ai",
                MistralBackend,
                httpx.Response(status_code=500, text="Internal Server Error"),
            ),
            (
                "https://api.mistral.ai",
                MistralBackend,
                httpx.Response(status_code=429, text="Rate Limit Exceeded"),
            ),
        ],
    )
    async def test_backend_complete_streaming_error(
        self,
        base_url: Url,
        backend_class: type[MistralBackend | GenericBackend],
        response: httpx.Response,
    ):
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(return_value=response)
            provider = ProviderConfig(
                name="provider_name",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
            )
            if issubclass(backend_class, MistralBackend):
                backend = backend_class(provider=provider)
                backend._retry_config = self._build_fast_retry_config()
            else:
                backend = backend_class(provider=provider, retry_max_elapsed_time=0.0)
            model = ModelConfig(
                name="model_name", provider="provider_name", alias="model_alias"
            )
            messages = [LLMMessage(role=Role.user, content="Just say hi")]
            with pytest.raises(BackendError) as e:
                async for _ in backend.complete_streaming(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                ):
                    pass
            assert e.value.status == response.status_code
            assert e.value.reason == response.reason_phrase
            assert e.value.parsed_error is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base_url,provider_name,expected_stream_options",
        [
            ("https://api.fireworks.ai", "fireworks", {"include_usage": True}),
            (
                "https://api.mistral.ai",
                "mistral",
                {"include_usage": True, "stream_tool_calls": True},
            ),
        ],
    )
    async def test_backend_streaming_payload_includes_stream_options(
        self, base_url: Url, provider_name: str, expected_stream_options: dict
    ):
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(
                        b'data: {"choices": [{"delta": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}\n\ndata: [DONE]\n\n'
                    ),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            provider = ProviderConfig(
                name=provider_name, api_base=f"{base_url}/v1", api_key_env_var="API_KEY"
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(
                name="model_name", provider=provider_name, alias="model_alias"
            )
            messages = [LLMMessage(role=Role.user, content="hi")]

            async for _ in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            ):
                pass

            assert route.called
            request = route.calls.last.request
            payload = json.loads(request.content)

            assert payload["stream"] is True
            assert payload["stream_options"] == expected_stream_options

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend_type", [Backend.MISTRAL, Backend.GENERIC])
    async def test_backend_user_agent(self, backend_type: Backend):
        user_agent = get_user_agent(backend_type)
        base_url = "https://api.example.com"
        json_response = {
            "id": "fake_id_1234",
            "created": 1234567890,
            "model": "devstral-latest",
            "usage": {
                "prompt_tokens": 100,
                "total_tokens": 300,
                "completion_tokens": 200,
            },
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "tool_calls": None,
                        "content": "Hey",
                    },
                }
            ],
        }
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(
                return_value=httpx.Response(status_code=200, json=json_response)
            )

            provider = ProviderConfig(
                name="provider_name",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
            )
            backend = BACKEND_FACTORY[backend_type](provider=provider)
            model = ModelConfig(
                name="model_name", provider="provider_name", alias="model_alias"
            )
            messages = [LLMMessage(role=Role.user, content="Just say hi")]

            await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers={"user-agent": user_agent},
            )

            assert mock_api.calls.last.request.headers["user-agent"] == user_agent

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend_type", [Backend.MISTRAL, Backend.GENERIC])
    async def test_backend_user_agent_when_streaming(self, backend_type: Backend):
        user_agent = get_user_agent(backend_type)

        base_url = "https://api.example.com"
        with respx.mock(base_url=base_url) as mock_api:
            chunks = [
                rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"role":"assistant","content":"Hey"},"finish_reason":"stop"}]}'
            ]
            mock_response = httpx.Response(
                status_code=200,
                stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                headers={"Content-Type": "text/event-stream"},
            )
            mock_api.post(CHAT_COMPLETIONS_PATH).mock(return_value=mock_response)

            provider = ProviderConfig(
                name="provider_name",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
            )
            backend = BACKEND_FACTORY[backend_type](provider=provider)
            model = ModelConfig(
                name="model_name", provider="provider_name", alias="model_alias"
            )
            messages = [LLMMessage(role=Role.user, content="Just say hi")]

            async for _ in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers={"user-agent": user_agent},
            ):
                pass

            assert mock_api.calls.last.request.headers["user-agent"] == user_agent


def _mistral_client_mock() -> tuple[MagicMock, MagicMock]:
    """A Mistral stand-in, plus the hook registry basesdk reaches for."""
    client = MagicMock()
    hooks = MagicMock()
    client.sdk_configuration.__dict__["_hooks"] = hooks
    return client, hooks


class TestBackendFactory:
    def test_create_backend_passes_retry_budget_to_mistral_backend(self):
        provider = ProviderConfig(
            name="test_provider",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="API_KEY",
            backend=Backend.MISTRAL,
        )

        backend = create_backend(
            provider=provider, timeout=7200.0, retry_max_elapsed_time=1234.0
        )

        assert isinstance(backend, MistralBackend)
        assert backend._timeout == 7200.0
        assert backend._retry_config.backoff.max_elapsed_time == 1234000

    def test_create_backend_passes_enable_otel_to_generic_backend(self):
        provider = ProviderConfig(
            name="test_provider",
            api_base="https://api.example.com/v1",
            api_key_env_var="API_KEY",
            backend=Backend.GENERIC,
        )

        backend = create_backend(provider=provider, enable_otel=True)

        assert isinstance(backend, GenericBackend)
        assert backend._enable_otel is True

    def test_create_backend_passes_retry_budget_to_generic_backend(self):
        provider = ProviderConfig(
            name="test_provider",
            api_base="https://api.example.com/v1",
            api_key_env_var="API_KEY",
            backend=Backend.GENERIC,
        )

        backend = create_backend(
            provider=provider, timeout=7200.0, retry_max_elapsed_time=1234.0
        )

        assert isinstance(backend, GenericBackend)
        assert backend._timeout == 7200.0
        assert backend._retry_max_elapsed_time == 1234.0


class TestMistralRetry:
    @staticmethod
    def _create_test_backend(
        timeout: float = 720.0, retry_max_elapsed_time: float = 300.0
    ) -> MistralBackend:
        provider = ProviderConfig(
            name="test_provider",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="API_KEY",
        )
        return MistralBackend(
            provider=provider,
            timeout=timeout,
            retry_max_elapsed_time=retry_max_elapsed_time,
        )

    @staticmethod
    def _build_fast_http_retry_config() -> RetryConfig:
        return RetryConfig(
            strategy="backoff",
            backoff=BackoffStrategy(
                initial_interval=1, max_interval=1, exponent=1, max_elapsed_time=10000
            ),
            retry_connection_errors=True,
        )

    @pytest.mark.asyncio
    async def test_client_creation_includes_timeout_and_retry_config(self):
        backend = self._create_test_backend()

        with patch("vibe.core.llm.backend.mistral.Mistral") as mock_mistral_class:
            mock_mistral_class.return_value = _mistral_client_mock()[0]
            backend._get_client()
            call_kwargs = mock_mistral_class.call_args.kwargs
            assert call_kwargs["api_key"] == backend._api_key
            assert call_kwargs["server_url"] == backend._server_url
            assert call_kwargs["timeout_ms"] == 720000
            assert call_kwargs["retry_config"] is backend._retry_config
            assert "async_client" in call_kwargs

    @pytest.mark.asyncio
    async def test_client_creation_registers_both_retry_seams(self):
        """429/5xx are only visible to httpx; connection errors only to the SDK."""
        backend = self._create_test_backend()

        with patch("vibe.core.llm.backend.mistral.Mistral") as mock_mistral_class:
            client, hooks = _mistral_client_mock()
            mock_mistral_class.return_value = client
            backend._get_client()

            http_client = mock_mistral_class.call_args.kwargs["async_client"]
            assert backend._on_response in http_client.event_hooks["response"]
            hooks.register_after_error_hook.assert_called_once()

    def test_retry_budget_uses_explicit_config(self):
        backend = self._create_test_backend(
            timeout=7200.0, retry_max_elapsed_time=1234.0
        )

        assert backend._timeout == 7200.0
        assert backend._retry_config.backoff.max_elapsed_time == 1234000

    @pytest.mark.asyncio
    async def test_complete_retries_retryable_http_error(self):
        with respx.mock(base_url="https://api.mistral.ai") as mock_api:
            route = mock_api.post("/v1/chat/completions").mock(
                side_effect=[
                    httpx.Response(status_code=502, text="Bad Gateway"),
                    httpx.Response(
                        status_code=200, json=MISTRAL_SIMPLE_CONVERSATION_PARAMS[0][1]
                    ),
                ]
            )
            backend = self._create_test_backend()
            backend._retry_config = self._build_fast_http_retry_config()
            model = ModelConfig(
                name="model_name", provider="test_provider", alias="model_alias"
            )
            messages = [LLMMessage(role=Role.user, content="Just say hi")]

            result = await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            assert result.message.content == "Some content"
            assert route.call_count == 2

    @staticmethod
    def _rate_limited_stream() -> httpx.Response:
        """A 429 whose body is still on the wire, as a real transport returns it.

        respx pre-reads every mocked response, which is exactly the state this
        test needs to avoid, so these cases drive a plain httpx MockTransport.
        """
        return httpx.Response(
            status_code=429,
            stream=httpx.ByteStream(b'{"message": "service tier capacity exceeded"}'),
            headers={"content-type": "application/json", "retry-after": "0"},
        )

    @staticmethod
    def _serving(handler):
        return patch(
            "vibe.core.llm.backend.mistral.VibeAsyncHTTPClient",
            lambda **kwargs: VibeAsyncHTTPClient(
                **kwargs, transport=httpx.MockTransport(handler)
            ),
        )

    async def _drain_stream(self, backend: MistralBackend) -> None:
        model = ModelConfig(
            name="model_name", provider="test_provider", alias="model_alias"
        )
        messages = [LLMMessage(role=Role.user, content="Just say hi")]
        async for _ in backend.complete_streaming(
            model=model,
            messages=messages,
            temperature=0.2,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            extra_headers=None,
        ):
            pass

    @pytest.mark.asyncio
    async def test_retried_streaming_response_releases_its_connection(self):
        """A retried streaming response must not stay checked out of the pool.

        The SDK issues the request with the body unread and holds the failed
        response alive across the backoff without closing it, so nothing else
        ever returns its connection.
        """
        _, chunks, _ = MISTRAL_STREAMED_SIMPLE_CONVERSATION_PARAMS[0]
        served: list[httpx.Response] = []

        def handler(request: httpx.Request) -> httpx.Response:
            response = (
                httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(b"\n\n".join(chunks)),
                    headers={"content-type": "text/event-stream"},
                )
                if served
                else self._rate_limited_stream()
            )
            served.append(response)
            return response

        backend = self._create_test_backend()
        backend._retry_config = self._build_fast_http_retry_config()
        with self._serving(handler):
            await self._drain_stream(backend)

        assert len(served) == 2
        retried = served[0]
        assert retried.is_closed
        assert "service tier capacity exceeded" in retried.text

    @pytest.mark.asyncio
    async def test_an_unreadable_retryable_body_still_retries(self):
        """A body that cannot be read must not cost the turn its retry.

        Decoding and protocol errors are permanent to the SDK, so letting one
        escape the drain would turn a rate limit into a dead turn on the first
        attempt.
        """
        _, chunks, _ = MISTRAL_STREAMED_SIMPLE_CONVERSATION_PARAMS[0]
        served: list[httpx.Response] = []

        def handler(request: httpx.Request) -> httpx.Response:
            response = (
                httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(b"\n\n".join(chunks)),
                    headers={"content-type": "text/event-stream"},
                )
                if served
                else httpx.Response(
                    status_code=429,
                    stream=httpx.ByteStream(b"this is not gzip"),
                    headers={
                        "content-type": "application/json",
                        "content-encoding": "gzip",
                        "retry-after": "0",
                    },
                )
            )
            served.append(response)
            return response

        backend = self._create_test_backend()
        backend._retry_config = self._build_fast_http_retry_config()
        with self._serving(handler):
            await self._drain_stream(backend)

        assert len(served) == 2
        assert served[0].is_closed

    @pytest.mark.asyncio
    async def test_exhausted_retries_still_carry_the_streamed_error_body(self):
        served: list[httpx.Response] = []

        def handler(request: httpx.Request) -> httpx.Response:
            response = self._rate_limited_stream()
            served.append(response)
            return response

        backend = self._create_test_backend()
        backend._retry_config = TestBackend._build_fast_retry_config()
        with self._serving(handler), pytest.raises(BackendError) as raised:
            await self._drain_stream(backend)

        assert raised.value.status == 429
        assert "service tier capacity exceeded" in (raised.value.body_text or "")
        assert all(response.is_closed for response in served)


class TestMistralMapperPrepareMessage:
    """Tests for MistralMapper.prepare_message thinking-block handling.

    The Mistral API returns assistant messages with reasoning as a single
    ThinkChunk (no trailing TextChunk when there is no text content).  When
    the mapper rebuilds the message for the next request it must NOT append
    an empty TextChunk, otherwise the proxy's history-consistency check
    sees a content mismatch on every turn and creates spurious conversation
    segments.
    """

    @pytest.fixture
    def mapper(self) -> MistralMapper:
        return MistralMapper()

    def test_reasoning_only_no_empty_text_chunk(self, mapper: MistralMapper) -> None:
        """Assistant with reasoning_content but no text content should produce
        only a ThinkChunk — no trailing empty TextChunk.
        """
        msg = LLMMessage(
            role=Role.assistant,
            content=None,
            reasoning_content="Let me think step by step.",
        )
        result = mapper.prepare_message(msg)
        content = result.content
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0].type == "thinking"

    def test_reasoning_with_empty_string_content(self, mapper: MistralMapper) -> None:
        """content='' (empty string) should also not produce a trailing TextChunk."""
        msg = LLMMessage(
            role=Role.assistant, content="", reasoning_content="Thinking..."
        )
        result = mapper.prepare_message(msg)
        content = result.content
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0].type == "thinking"

    def test_reasoning_with_text_content(self, mapper: MistralMapper) -> None:
        """When there is actual text content, both ThinkChunk and TextChunk
        should be present.
        """
        msg = LLMMessage(
            role=Role.assistant,
            content="Here is the answer.",
            reasoning_content="Let me reason.",
        )
        result = mapper.prepare_message(msg)
        content = result.content
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0].type == "thinking"
        assert content[1].type == "text"
        assert content[1].text == "Here is the answer."

    def test_reasoning_with_tool_calls_no_text(self, mapper: MistralMapper) -> None:
        """Reasoning + tool_calls but no text content — only ThinkChunk."""
        msg = LLMMessage(
            role=Role.assistant,
            content=None,
            reasoning_content="I should run a command.",
            tool_calls=[
                ToolCall(
                    id="tc_1",
                    index=0,
                    function=FunctionCall(name="bash", arguments='{"cmd": "ls"}'),
                )
            ],
        )
        result = mapper.prepare_message(msg)
        assert isinstance(result, AssistantMessage)
        content = result.content
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0].type == "thinking"
        # Tool calls should still be present
        assert isinstance(result.tool_calls, list)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function.name == "bash"

    def test_no_reasoning_plain_string(self, mapper: MistralMapper) -> None:
        """Without reasoning_content, content is a plain string."""
        msg = LLMMessage(role=Role.assistant, content="Hello!")
        result = mapper.prepare_message(msg)
        assert result.content == "Hello!"


class TestGenericBackendReasoningEffort:
    @pytest.mark.parametrize(
        ("thinking", "expect_in_payload"),
        [("off", False), ("low", True), ("medium", True), ("high", True)],
    )
    def test_build_payload_reasoning_effort(
        self, thinking: str, expect_in_payload: bool
    ) -> None:
        payload = build_chat_payload(
            model_name="test-model",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            thinking=thinking,
        )
        if expect_in_payload:
            assert payload["reasoning_effort"] == thinking
        else:
            assert "reasoning_effort" not in payload


class TestMistralBackendReasoningEffort:
    """Tests that MistralBackend correctly passes reasoning_effort to the SDK."""

    @pytest.fixture
    def backend(self) -> MistralBackend:
        provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="API_KEY",
        )
        return MistralBackend(provider=provider)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("thinking", "expected_effort"),
        [("off", None), ("low", "none"), ("medium", "high"), ("high", "high")],
    )
    async def test_complete_passes_reasoning_effort(
        self,
        backend: MistralBackend,
        thinking: Literal["off", "low", "medium", "high"],
        expected_effort: str | None,
    ) -> None:
        model = ModelConfig(
            name="mistral-small-latest",
            provider="mistral",
            alias="mistral-small",
            thinking=thinking,
        )
        messages = [LLMMessage(role=Role.user, content="hi")]

        with patch.object(backend, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "hello"
            mock_response.choices[0].message.tool_calls = None
            mock_response.usage.prompt_tokens = 10
            mock_response.usage.completion_tokens = 5
            mock_client.chat.complete_async = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            call_kwargs = mock_client.chat.complete_async.call_args.kwargs
            assert call_kwargs["reasoning_effort"] == expected_effort
            assert call_kwargs["temperature"] == 0.2

    @pytest.mark.asyncio
    async def test_complete_omits_reasoning_content_when_thinking_off(
        self, backend: MistralBackend
    ) -> None:
        model = ModelConfig(
            name="devstral-small-latest",
            provider="mistral",
            alias="devstral-small",
            thinking="off",
        )
        messages = [
            LLMMessage(role=Role.user, content="Hi"),
            LLMMessage(
                role=Role.assistant,
                content="Answer",
                reasoning_content="Hidden reasoning",
            ),
        ]

        with patch.object(backend, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "ok"
            mock_response.choices[0].message.tool_calls = None
            mock_response.usage.prompt_tokens = 10
            mock_response.usage.completion_tokens = 5
            mock_client.chat.complete_async = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            call_kwargs = mock_client.chat.complete_async.call_args.kwargs
            sent_messages = call_kwargs["messages"]
            assert len(sent_messages) == 2
            assert isinstance(sent_messages[1], AssistantMessage)
            assert sent_messages[1].content == "Answer"


class TestBuildHttpErrorBodyReading:
    _MESSAGES: ClassVar[list[LLMMessage]] = [LLMMessage(role=Role.user, content="hi")]
    _COMMON_KWARGS: ClassVar[dict] = dict(
        provider="test",
        endpoint="https://api.test.com",
        model="test-model",
        messages=_MESSAGES,
        temperature=0.2,
        has_tools=False,
        tool_choice=None,
    )

    def _make_sdk_error(self, response: httpx.Response) -> SDKError:
        return SDKError("sdk error", response)

    def _make_http_status_error(
        self, response: httpx.Response
    ) -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError(
            "http error", request=response.request, response=response
        )

    def test_sdk_error_readable_body(self) -> None:
        response = httpx.Response(
            400,
            json={"message": "invalid temperature"},
            request=httpx.Request("POST", "https://api.test.com"),
        )
        err = BackendErrorBuilder.build_http_error(
            error=self._make_sdk_error(response),
            response=response,
            **self._COMMON_KWARGS,
        )
        assert err.status == 400
        assert err.parsed_error == "invalid temperature"
        assert "invalid temperature" in err.body_text

    def test_http_status_error_readable_body(self) -> None:
        response = httpx.Response(
            400,
            json={"message": "invalid temperature"},
            request=httpx.Request("POST", "https://api.test.com"),
        )
        err = BackendErrorBuilder.build_http_error(
            error=self._make_http_status_error(response),
            response=response,
            **self._COMMON_KWARGS,
        )
        assert err.status == 400
        assert err.parsed_error == "invalid temperature"
        assert "invalid temperature" in err.body_text

    def test_sdk_error_stream_response_falls_back_to_read(self) -> None:
        response = httpx.Response(
            400,
            stream=httpx.ByteStream(b'{"message": "context too long"}'),
            request=httpx.Request("POST", "https://api.test.com"),
        )
        sdk_err = SDKError(
            "sdk error", response, body='{"message": "context too long"}'
        )
        err = BackendErrorBuilder.build_http_error(
            error=sdk_err, response=response, **self._COMMON_KWARGS
        )
        assert err.parsed_error == "context too long"
        assert "context too long" in err.body_text

    def test_http_status_error_stream_response_falls_back_to_read(self) -> None:
        response = httpx.Response(
            400,
            stream=httpx.ByteStream(b'{"message": "context too long"}'),
            request=httpx.Request("POST", "https://api.test.com"),
        )
        err = BackendErrorBuilder.build_http_error(
            error=self._make_http_status_error(response),
            response=response,
            **self._COMMON_KWARGS,
        )
        assert err.parsed_error == "context too long"
        assert "context too long" in err.body_text

    def test_sdk_error_unreadable_response_falls_back_to_str(self) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 400
        response.reason_phrase = "Bad Request"
        response.headers = {}
        type(response).text = property(lambda self: (_ for _ in ()).throw(Exception))
        response.read.side_effect = Exception("closed")

        sdk_err = SDKError("sdk msg", response, body='{"message": "context too long"}')
        err = BackendErrorBuilder.build_http_error(
            error=sdk_err, response=response, **self._COMMON_KWARGS
        )
        assert err.body_text == '{"message": "context too long"}'
        assert err.parsed_error == "context too long"

    def test_http_status_error_unreadable_response_falls_back_to_str(self) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 400
        response.reason_phrase = "Bad Request"
        response.headers = {}
        type(response).text = property(lambda self: (_ for _ in ()).throw(Exception))
        response.read.side_effect = Exception("closed")
        response.request = httpx.Request("POST", "https://api.test.com")

        http_err = httpx.HTTPStatusError(
            "http error with details", request=response.request, response=response
        )
        err = BackendErrorBuilder.build_http_error(
            error=http_err, response=response, **self._COMMON_KWARGS
        )
        assert "http error with details" in err.body_text


class TestCachedTokens:
    @pytest.fixture
    def provider(self) -> ProviderConfig:
        return ProviderConfig(
            name="provider_name",
            api_base="https://api.example.com/v1",
            api_key_env_var="API_KEY",
        )

    def test_openai_adapter_reads_cached_tokens(self, provider: ProviderConfig) -> None:
        data = {
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 128},
            },
        }
        chunk = OpenAIAdapter().parse_response(data, provider)
        assert chunk.usage is not None
        assert chunk.usage.cached_tokens == 128

    def test_openai_adapter_defaults_cached_tokens_to_zero(
        self, provider: ProviderConfig
    ) -> None:
        data = {
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 10},
        }
        chunk = OpenAIAdapter().parse_response(data, provider)
        assert chunk.usage is not None
        assert chunk.usage.cached_tokens == 0

    def test_mistral_helper_reads_dict_details(self) -> None:
        usage = MagicMock()
        usage.prompt_tokens_details = {"cached_tokens": 77}
        assert _cached_tokens(usage) == 77

    def test_mistral_helper_reads_object_details(self) -> None:
        details = MagicMock()
        details.cached_tokens = 55
        usage = MagicMock()
        usage.prompt_tokens_details = details
        assert _cached_tokens(usage) == 55

    def test_mistral_helper_handles_missing_details(self) -> None:
        usage = MagicMock()
        usage.prompt_tokens_details = None
        assert _cached_tokens(usage) == 0

    def test_mistral_helper_handles_none_usage(self) -> None:
        assert _cached_tokens(None) == 0

    def test_mistral_helper_tolerates_unparsable_value(self) -> None:
        usage = MagicMock()
        usage.prompt_tokens_details = {"cached_tokens": "60.0"}
        assert _cached_tokens(usage) == 0

    def test_mistral_helper_coerces_numeric_string(self) -> None:
        usage = MagicMock()
        usage.prompt_tokens_details = {"cached_tokens": "60"}
        assert _cached_tokens(usage) == 60

    @pytest.mark.asyncio
    async def test_mistral_backend_complete_flows_cached_tokens(self) -> None:
        provider = ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="API_KEY",
        )
        backend = MistralBackend(provider=provider)
        model = ModelConfig(
            name="mistral-small-latest", provider="mistral", alias="mistral-small"
        )

        with patch.object(backend, "_get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "hello"
            mock_response.choices[0].message.tool_calls = None
            mock_response.usage.prompt_tokens = 200
            mock_response.usage.completion_tokens = 5
            mock_response.usage.prompt_tokens_details = {"cached_tokens": 128}
            mock_client.chat.complete_async = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_client

            chunk = await backend.complete(
                model=model,
                messages=[LLMMessage(role=Role.user, content="hi")],
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

        assert chunk.usage is not None
        assert chunk.usage.prompt_tokens == 200
        assert chunk.usage.cached_tokens == 128
