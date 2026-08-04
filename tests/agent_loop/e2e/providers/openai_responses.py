from __future__ import annotations

import json
from typing import Any

from tests import constants as c
from tests.agent_loop.e2e.providers.base import ProviderAPI
from tests.backend.data import Chunk, JsonResponse
from tests.backend.data.openai_responses import (
    openai_function_call_item,
    openai_message_item,
    openai_reasoning_tool_call_stream,
    openai_response,
    openai_text_stream,
)
from tests.conftest import build_test_vibe_config
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.types import Backend


def e2e_config(**overrides: Any) -> VibeConfigSchema:
    provider = ProviderConfig(
        name="openai",
        api_base=f"{c.OPENAI_BASE_URL}/v1",
        api_key_env_var="OPENAI_API_KEY",
        api_style="openai-responses",
        backend=Backend.GENERIC,
    )
    models = [ModelConfig(name="gpt-test", provider="openai", alias="openai")]
    return build_test_vibe_config(
        active_model="openai", models=models, providers=[provider], **overrides
    )


class API(ProviderAPI):
    base_url = c.OPENAI_BASE_URL
    post_path = c.OPENAI_RESPONSES_PATH


class Mocks:
    def answer(self, text: str) -> JsonResponse:
        return openai_response([openai_message_item(text)])

    def text_stream(self, text: str) -> list[Chunk]:
        return openai_text_stream(text)

    def tool_call(self, name: str, arguments: dict[str, Any]) -> JsonResponse:
        return openai_response([openai_function_call_item(name, json.dumps(arguments))])

    def reasoning_answer(self, text: str, reasoning: str) -> JsonResponse:
        return openai_response([
            openai_message_item(reasoning, phase="commentary"),
            openai_message_item(text, phase="final_answer"),
        ])

    def reasoning_tool_call_stream(
        self, name: str, arguments: dict[str, Any], *, reasoning: str
    ) -> list[Chunk]:
        return openai_reasoning_tool_call_stream(
            name, json.dumps(arguments), reasoning=reasoning
        )
