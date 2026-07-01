from __future__ import annotations

import json

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.generic import OpenAIAdapter
from vibe.core.types import LLMMessage, Role


def _prepare_payload(*, provider_name: str, thinking: str) -> dict:
    adapter = OpenAIAdapter()
    provider = ProviderConfig(
        name=provider_name,
        api_base="https://openrouter.ai/api/v1",
        api_key_env_var="OPENROUTER_API_KEY",
    )
    request = adapter.prepare_request(
        model_name="mistral-medium-3.5",
        messages=[LLMMessage(role=Role.user, content="hello")],
        temperature=0.2,
        tools=None,
        max_tokens=None,
        tool_choice=None,
        enable_streaming=False,
        provider=provider,
        thinking=thinking,
    )
    return json.loads(request.body)


class TestOpenRouterReasoning:
    def test_maps_high_thinking_to_reasoning_effort(self):
        payload = _prepare_payload(provider_name="openrouter", thinking="high")

        assert payload["reasoning"] == {"effort": "high"}

    def test_maps_off_thinking_to_disabled_reasoning(self):
        payload = _prepare_payload(provider_name="openrouter", thinking="off")

        assert payload["reasoning"] == {"enabled": False}

    def test_maps_max_thinking_to_high_effort(self):
        payload = _prepare_payload(provider_name="openrouter", thinking="max")

        assert payload["reasoning"] == {"effort": "high"}

    def test_does_not_override_existing_reasoning_field(self):
        adapter = OpenAIAdapter()
        payload = {"model": "test", "reasoning": {"effort": "medium"}}

        result = adapter.apply_openrouter_reasoning(payload, thinking="high")

        assert result["reasoning"] == {"effort": "medium"}

    def test_other_providers_do_not_get_reasoning_field(self):
        payload = _prepare_payload(provider_name="fireworks", thinking="high")

        assert "reasoning" not in payload
