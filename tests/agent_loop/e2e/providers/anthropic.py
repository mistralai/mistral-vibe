from __future__ import annotations

import json
from typing import Any

from tests import constants as c
from tests.agent_loop.e2e.providers.base import ProviderAPI
from tests.backend.data import Chunk, JsonResponse
from tests.backend.data.anthropic import (
    anthropic_message,
    anthropic_reasoning_tool_use_stream,
    anthropic_text_stream,
    anthropic_tool_use,
)
from tests.conftest import build_test_vibe_config
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.types import Backend


def e2e_config(**overrides: Any) -> VibeConfig:
    provider = ProviderConfig(
        name="anthropic",
        api_base=c.ANTHROPIC_BASE_URL,
        api_key_env_var="ANTHROPIC_API_KEY",
        api_style="anthropic",
        backend=Backend.GENERIC,
    )
    models = [ModelConfig(name="claude-test", provider="anthropic", alias="anthropic")]
    return build_test_vibe_config(
        active_model="anthropic", models=models, providers=[provider], **overrides
    )


class API(ProviderAPI):
    base_url = c.ANTHROPIC_BASE_URL
    post_path = c.ANTHROPIC_MESSAGES_PATH


class Mocks:
    def answer(self, text: str) -> JsonResponse:
        return anthropic_message(text)

    def text_stream(self, text: str) -> list[Chunk]:
        return anthropic_text_stream(text)

    def tool_call(self, name: str, arguments: dict[str, Any]) -> JsonResponse:
        return anthropic_tool_use(name, arguments)

    def reasoning_answer(self, text: str, reasoning: str) -> JsonResponse:
        message = anthropic_message(text)
        message["content"].insert(
            0, {"type": "thinking", "thinking": reasoning, "signature": "sig"}
        )
        return message

    def reasoning_tool_call_stream(
        self, name: str, arguments: dict[str, Any], *, reasoning: str
    ) -> list[Chunk]:
        return anthropic_reasoning_tool_use_stream(
            name, json.dumps(arguments), reasoning=reasoning
        )
