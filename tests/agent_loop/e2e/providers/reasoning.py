from __future__ import annotations

import json
from typing import Any

from tests import constants as c
from tests.agent_loop.e2e.providers.base import ProviderAPI
from tests.backend.data import Chunk, JsonResponse
from tests.backend.data.reasoning import (
    reasoning_message,
    reasoning_text_stream,
    reasoning_thinking_message,
    reasoning_thinking_tool_use_stream,
    reasoning_tool_use,
)
from tests.conftest import build_test_vibe_config
from vibe.core.config import (
    ModelConfig,
    ProviderConfig,
    ThinkingLevel,
    VibeConfigSchema,
)
from vibe.core.types import Backend


def e2e_config(
    *, thinking: ThinkingLevel = "off", **overrides: Any
) -> VibeConfigSchema:
    provider = ProviderConfig(
        name="reasoning",
        api_base=c.REASONING_BASE_URL,
        api_key_env_var="REASONING_API_KEY",
        api_style="reasoning",
        backend=Backend.GENERIC,
    )
    models = [
        ModelConfig(
            name="reasoning-test",
            provider="reasoning",
            alias="reasoning",
            thinking=thinking,
        )
    ]
    return build_test_vibe_config(
        active_model="reasoning", models=models, providers=[provider], **overrides
    )


class API(ProviderAPI):
    base_url = c.REASONING_BASE_URL
    post_path = c.REASONING_COMPLETIONS_PATH


class Mocks:
    def answer(self, text: str) -> JsonResponse:
        return reasoning_message(text)

    def text_stream(self, text: str) -> list[Chunk]:
        return reasoning_text_stream(text)

    def tool_call(self, name: str, arguments: dict[str, Any]) -> JsonResponse:
        return reasoning_tool_use(name, json.dumps(arguments))

    def reasoning_answer(self, text: str, reasoning: str) -> JsonResponse:
        return reasoning_thinking_message(text, reasoning)

    def reasoning_tool_call_stream(
        self, name: str, arguments: dict[str, Any], *, reasoning: str
    ) -> list[Chunk]:
        return reasoning_thinking_tool_use_stream(
            name, json.dumps(arguments), reasoning=reasoning
        )
