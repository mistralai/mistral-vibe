"""Unit tests for MiniMax provider integration.

Tests cover provider configuration, model resolution, backend selection,
and OpenAI-compatible API request formatting via the GenericBackend.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from vibe.core.config import (
    DEFAULT_MODELS,
    DEFAULT_PROVIDERS,
    Backend,
    ModelConfig,
    ProviderConfig,
)
from vibe.core.llm.backend.factory import BACKEND_FACTORY
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.types import LLMChunk, LLMMessage, Role


class TestMiniMaxDefaultProviderConfig:
    def test_minimax_in_default_providers(self) -> None:
        provider_names = [p.name for p in DEFAULT_PROVIDERS]
        assert "minimax" in provider_names

    def test_minimax_provider_api_base(self) -> None:
        minimax = next(p for p in DEFAULT_PROVIDERS if p.name == "minimax")
        assert minimax.api_base == "https://api.minimax.io/v1"

    def test_minimax_provider_uses_generic_backend(self) -> None:
        minimax = next(p for p in DEFAULT_PROVIDERS if p.name == "minimax")
        assert minimax.backend == Backend.GENERIC

    def test_minimax_provider_uses_openai_api_style(self) -> None:
        minimax = next(p for p in DEFAULT_PROVIDERS if p.name == "minimax")
        assert minimax.api_style == "openai"

    def test_minimax_provider_api_key_env_var(self) -> None:
        minimax = next(p for p in DEFAULT_PROVIDERS if p.name == "minimax")
        assert minimax.api_key_env_var == "MINIMAX_API_KEY"


class TestMiniMaxDefaultModelConfig:
    def test_minimax_m27_in_default_models(self) -> None:
        model_aliases = [m.alias for m in DEFAULT_MODELS]
        assert "minimax-m2.7" in model_aliases

    def test_minimax_m27_highspeed_in_default_models(self) -> None:
        model_aliases = [m.alias for m in DEFAULT_MODELS]
        assert "minimax-m2.7-highspeed" in model_aliases

    def test_minimax_m27_model_name(self) -> None:
        m27 = next(m for m in DEFAULT_MODELS if m.alias == "minimax-m2.7")
        assert m27.name == "MiniMax-M2.7"
        assert m27.provider == "minimax"

    def test_minimax_m27_highspeed_model_name(self) -> None:
        m27hs = next(m for m in DEFAULT_MODELS if m.alias == "minimax-m2.7-highspeed")
        assert m27hs.name == "MiniMax-M2.7-highspeed"
        assert m27hs.provider == "minimax"

    def test_minimax_model_pricing(self) -> None:
        m27 = next(m for m in DEFAULT_MODELS if m.alias == "minimax-m2.7")
        assert m27.input_price == 0.15
        assert m27.output_price == 0.45

    def test_minimax_highspeed_pricing(self) -> None:
        m27hs = next(m for m in DEFAULT_MODELS if m.alias == "minimax-m2.7-highspeed")
        assert m27hs.input_price == 0.05
        assert m27hs.output_price == 0.15


class TestMiniMaxBackendFactory:
    def test_generic_backend_created_for_minimax(self) -> None:
        provider = ProviderConfig(
            name="minimax",
            api_base="https://api.minimax.io/v1",
            api_key_env_var="MINIMAX_API_KEY",
        )
        backend = BACKEND_FACTORY[provider.backend](provider=provider)
        assert isinstance(backend, GenericBackend)


class TestMiniMaxRequestFormatting:
    @pytest.mark.asyncio
    async def test_minimax_complete_sends_correct_payload(self) -> None:
        base_url = "https://api.minimax.io"
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    json={
                        "id": "test-id",
                        "object": "chat.completion",
                        "created": 1234567890,
                        "model": "MiniMax-M2.7",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Hello!",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                        },
                    },
                )
            )

            provider = ProviderConfig(
                name="minimax",
                api_base=f"{base_url}/v1",
                api_key_env_var="MINIMAX_API_KEY",
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(
                name="MiniMax-M2.7",
                provider="minimax",
                alias="minimax-m2.7",
            )
            messages = [LLMMessage(role=Role.user, content="Hi")]

            result = await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            assert route.called
            request = route.calls.last.request
            payload = json.loads(request.content)

            assert payload["model"] == "MiniMax-M2.7"
            assert payload["temperature"] == 0.2
            assert payload["messages"][0]["role"] == "user"
            assert payload["messages"][0]["content"] == "Hi"

            assert result.message.content == "Hello!"
            assert result.usage is not None
            assert result.usage.prompt_tokens == 10
            assert result.usage.completion_tokens == 5

    @pytest.mark.asyncio
    async def test_minimax_bearer_auth_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
        base_url = "https://api.minimax.io"
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    json={
                        "id": "test-id",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "hi"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                )
            )

            provider = ProviderConfig(
                name="minimax",
                api_base=f"{base_url}/v1",
                api_key_env_var="MINIMAX_API_KEY",
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(
                name="MiniMax-M2.7", provider="minimax", alias="minimax-m2.7"
            )
            messages = [LLMMessage(role=Role.user, content="hi")]

            await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            assert route.called
            request = route.calls.last.request
            assert request.headers["Authorization"] == "Bearer test-minimax-key"

    @pytest.mark.asyncio
    async def test_minimax_streaming_complete(self) -> None:
        base_url = "https://api.minimax.io"
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(
                        b'data: {"choices": [{"delta": {"role": "assistant", "content": ""}, "finish_reason": null}], "usage": null}\n\n'
                        b'data: {"choices": [{"delta": {"content": "Hello"}, "finish_reason": null}], "usage": null}\n\n'
                        b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}\n\n'
                        b"data: [DONE]\n\n"
                    ),
                    headers={"Content-Type": "text/event-stream"},
                )
            )

            provider = ProviderConfig(
                name="minimax",
                api_base=f"{base_url}/v1",
                api_key_env_var="MINIMAX_API_KEY",
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(
                name="MiniMax-M2.7", provider="minimax", alias="minimax-m2.7"
            )
            messages = [LLMMessage(role=Role.user, content="Hi")]

            results: list[LLMChunk] = []
            async for chunk in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            ):
                results.append(chunk)

            assert len(results) == 3
            contents = [r.message.content for r in results if r.message.content]
            assert "Hello" in contents

    @pytest.mark.asyncio
    async def test_minimax_temperature_zero(self) -> None:
        base_url = "https://api.minimax.io"
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    json={
                        "id": "test-id",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                )
            )

            provider = ProviderConfig(
                name="minimax",
                api_base=f"{base_url}/v1",
                api_key_env_var="MINIMAX_API_KEY",
            )
            backend = GenericBackend(provider=provider)
            model = ModelConfig(
                name="MiniMax-M2.7", provider="minimax", alias="minimax-m2.7"
            )
            messages = [LLMMessage(role=Role.user, content="hi")]

            await backend.complete(
                model=model,
                messages=messages,
                temperature=0.0,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            assert route.called
            payload = json.loads(route.calls.last.request.content)
            assert payload["temperature"] == 0.0
