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
from unittest.mock import MagicMock, patch

import httpx
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
    STREAMED_SIMPLE_CONVERSATION_PARAMS as MISTRAL_STREAMED_SIMPLE_CONVERSATION_PARAMS,
    STREAMED_TOOL_CONVERSATION_PARAMS as MISTRAL_STREAMED_TOOL_CONVERSATION_PARAMS,
    TOOL_CONVERSATION_PARAMS as MISTRAL_TOOL_CONVERSATION_PARAMS,
)
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm.backend.factory import BACKEND_FACTORY
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.backend.mistral import MistralBackend, MistralMapper
from vibe.core.llm.exceptions import BackendError
from vibe.core.llm.types import BackendLike
from vibe.core.types import Backend, FunctionCall, LLMChunk, LLMMessage, Role, ToolCall
from vibe.core.utils import get_user_agent


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
            mock_api.post("/v1/chat/completions").mock(
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
        ],
    )
    async def test_backend_complete_streaming(
        self, base_url: Url, chunks: list[Chunk], result_data: list[ResultData]
    ):
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post("/v1/chat/completions").mock(
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
            mock_api.post("/v1/chat/completions").mock(return_value=response)
            provider = ProviderConfig(
                name="provider_name",
                api_base=f"{base_url}/v1",
                api_key_env_var="API_KEY",
            )
            backend = backend_class(provider=provider)
            if isinstance(backend, MistralBackend):
                backend._retry_config = self._build_fast_retry_config()
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
            route = mock_api.post("/v1/chat/completions").mock(
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
            mock_api.post("/v1/chat/completions").mock(
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
            mock_api.post("/v1/chat/completions").mock(return_value=mock_response)

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


class TestMistralRetry:
    @staticmethod
    def _create_test_backend() -> MistralBackend:
        provider = ProviderConfig(
            name="test_provider",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="API_KEY",
        )
        return MistralBackend(provider=provider)

    @pytest.mark.asyncio
    async def test_client_creation_includes_timeout_and_retry_config(self):
        backend = self._create_test_backend()

        with patch("vibe.core.llm.backend.mistral.Mistral") as mock_mistral_class:
            mock_mistral_class.return_value = MagicMock()
            backend._get_client()
            mock_mistral_class.assert_called_once_with(
                api_key=backend._api_key,
                server_url=backend._server_url,
                timeout_ms=720000,
                retry_config=backend._retry_config,
            )


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
