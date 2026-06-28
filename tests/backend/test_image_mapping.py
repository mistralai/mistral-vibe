from __future__ import annotations

import base64
from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any

import pytest

from vibe.core.llm.backend.anthropic import AnthropicAdapter
from vibe.core.llm.backend.base import APIAdapter
from vibe.core.llm.backend.generic import OpenAIAdapter
from vibe.core.llm.backend.mistral import MistralMapper
from vibe.core.llm.backend.openai_responses import OpenAIResponsesAdapter
from vibe.core.llm.backend.reasoning_adapter import ReasoningAdapter
from vibe.core.types import FileImageSource, ImageAttachment, LLMMessage, Role

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
EXPECTED_B64 = base64.b64encode(PNG_BYTES).decode("ascii")
EXPECTED_DATA_URI = f"data:image/png;base64,{EXPECTED_B64}"


@pytest.fixture()
def image_attachment(tmp_path: Path) -> ImageAttachment:
    path = tmp_path / "shot.png"
    path.write_bytes(PNG_BYTES)
    return ImageAttachment(
        source=FileImageSource(path=path), alias="shot.png", mime_type="image/png"
    )


def _user_message(image: ImageAttachment) -> LLMMessage:
    return LLMMessage(role=Role.user, content="describe this", images=[image])


class _FakeProvider:
    name = "mistral"
    reasoning_field_name = "reasoning_content"


def _adapter_payload(
    adapter: APIAdapter,
    messages: Sequence[LLMMessage],
    *,
    model: str = "gpt-4o",
    **overrides: Any,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model_name": model,
        "messages": list(messages),
        "temperature": 0.0,
        "tools": None,
        "max_tokens": None,
        "tool_choice": None,
        "enable_streaming": False,
        "provider": _FakeProvider(),
        "api_key": "k",
    }
    kwargs.update(overrides)
    prepared = adapter.prepare_request(**kwargs)
    return json.loads(prepared.body.decode("utf-8"))


def test_mistral_mapper_emits_image_url_chunk(
    image_attachment: ImageAttachment,
) -> None:
    mapper = MistralMapper()
    prepared = mapper.prepare_message(_user_message(image_attachment))

    dumped = prepared.model_dump()
    assert dumped["role"] == "user"
    parts = dumped["content"]
    assert parts[0] == {"type": "text", "text": "describe this"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == EXPECTED_DATA_URI


def test_openai_adapter_emits_image_url_part(image_attachment: ImageAttachment) -> None:
    payload = _adapter_payload(OpenAIAdapter(), [_user_message(image_attachment)])
    msg = payload["messages"][0]
    assert msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "describe this"}
    assert msg["content"][1] == {
        "type": "image_url",
        "image_url": {"url": EXPECTED_DATA_URI},
    }
    assert "images" not in msg


def test_openai_responses_adapter_emits_input_image_part(
    image_attachment: ImageAttachment,
) -> None:
    payload = _adapter_payload(
        OpenAIResponsesAdapter(), [_user_message(image_attachment)]
    )
    user = payload["input"][0]
    assert user["role"] == "user"
    assert user["content"][0] == {"type": "input_text", "text": "describe this"}
    assert user["content"][1] == {"type": "input_image", "image_url": EXPECTED_DATA_URI}


def test_anthropic_adapter_emits_image_source(
    image_attachment: ImageAttachment,
) -> None:
    payload = _adapter_payload(
        AnthropicAdapter(), [_user_message(image_attachment)], model="claude-3-5-sonnet"
    )
    msg = payload["messages"][0]
    assert msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "describe this"}
    image_block = msg["content"][1]
    assert image_block["type"] == "image"
    assert image_block["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": EXPECTED_B64,
    }
    # prepare_request stamps cache_control on the last block of the last user turn
    assert image_block["cache_control"] == {"type": "ephemeral"}


def test_reasoning_adapter_emits_image_url_part(
    image_attachment: ImageAttachment,
) -> None:
    payload = _adapter_payload(ReasoningAdapter(), [_user_message(image_attachment)])
    msg = payload["messages"][0]
    assert msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "describe this"}
    assert msg["content"][1] == {
        "type": "image_url",
        "image_url": {"url": EXPECTED_DATA_URI},
    }


def test_text_only_user_message_keeps_string_content() -> None:
    text_msg = LLMMessage(role=Role.user, content="hi")

    mistral_prepared = MistralMapper().prepare_message(text_msg)
    assert mistral_prepared.model_dump()["content"] == "hi"

    anthropic_payload = _adapter_payload(
        AnthropicAdapter(), [text_msg], model="claude-3-5-sonnet"
    )
    # Anthropic emits parts even for text-only; keep as wrapped text part
    text_block = anthropic_payload["messages"][0]["content"][0]
    assert text_block["type"] == "text"
    assert text_block["text"] == "hi"
