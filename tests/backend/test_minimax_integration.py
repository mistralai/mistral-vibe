"""Integration tests for MiniMax provider.

These tests verify end-to-end MiniMax integration through the GenericBackend,
including error handling, tool calls, and config-driven provider resolution.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tests.conftest import build_test_vibe_config
from vibe.core.config import (
    DEFAULT_MODELS,
    DEFAULT_PROVIDERS,
    ModelConfig,
    ProviderConfig,
)
from vibe.core.llm.backend.factory import BACKEND_FACTORY
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.exceptions import BackendError
from vibe.core.types import LLMMessage, Role


class TestMiniMaxEndToEndFlow:
    """Test MiniMax provider through the full config -> backend -> request pipeline."""

    @pytest.mark.asyncio
    async def test_config_to_backend_to_completion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MINIMAX_API_KEY", "integration-test-key")
        base_url = "https://api.minimax.io"

        cfg = build_test_vibe_config(
            active_model="minimax-m2.7",
            providers=list(DEFAULT_PROVIDERS),
            models=list(DEFAULT_MODELS),
        )
        model = cfg.get_active_model()
        provider = cfg.get_provider_for_model(model)

        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    json={
                        "id": "integ-1",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "Integration test response"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                    },
                )
            )

            backend = BACKEND_FACTORY[provider.backend](provider=provider)
            assert isinstance(backend, GenericBackend)

            result = await backend.complete(
                model=model,
                messages=[LLMMessage(role=Role.user, content="Test prompt")],
                temperature=model.temperature,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            assert result.message.content == "Integration test response"
            assert result.usage is not None
            assert result.usage.prompt_tokens == 20

    @pytest.mark.asyncio
    async def test_minimax_tool_call_flow(self) -> None:
        base_url = "https://api.minimax.io"
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=200,
                    json={
                        "id": "tool-1",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_abc123",
                                            "type": "function",
                                            "function": {
                                                "name": "read_file",
                                                "arguments": '{"path": "src/main.py"}',
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 50, "completion_tokens": 25},
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

            result = await backend.complete(
                model=model,
                messages=[LLMMessage(role=Role.user, content="Read main.py")],
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            assert result.message.tool_calls is not None
            assert len(result.message.tool_calls) == 1
            assert result.message.tool_calls[0].function.name == "read_file"
            assert '"path": "src/main.py"' in result.message.tool_calls[0].function.arguments

    @pytest.mark.asyncio
    async def test_minimax_error_handling(self) -> None:
        base_url = "https://api.minimax.io"
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post("/v1/chat/completions").mock(
                return_value=httpx.Response(
                    status_code=401,
                    json={
                        "error": {
                            "message": "Invalid API key",
                            "type": "authentication_error",
                        }
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

            with pytest.raises(BackendError) as exc_info:
                await backend.complete(
                    model=model,
                    messages=[LLMMessage(role=Role.user, content="test")],
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                )

            assert exc_info.value.status == 401
