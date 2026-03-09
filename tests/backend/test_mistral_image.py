"""Tests for MistralMapper and MistralBackend image handling."""

from __future__ import annotations

import mistralai

from vibe.core.config import Backend, ModelConfig, ProviderConfig
from vibe.core.llm.backend.mistral import MistralBackend, MistralMapper
from vibe.core.types import ImageContentPart, LLMMessage, Role

# ── MistralMapper.prepare_message ─────────────────────────────────────────────


class TestMistralMapperImageMessages:
    def test_plain_user_message_unchanged(self):
        msg = LLMMessage(role=Role.user, content="hello")
        result = MistralMapper().prepare_message(msg)
        assert isinstance(result, mistralai.UserMessage)
        assert result.content == "hello"

    def test_user_message_with_text_and_image(self):
        msg = LLMMessage(
            role=Role.user,
            content="what is this?",
            image_parts=[
                ImageContentPart(
                    image_url="data:image/png;base64,abc", media_type="image/png"
                )
            ],
        )
        result = MistralMapper().prepare_message(msg)
        assert isinstance(result, mistralai.UserMessage)
        assert isinstance(result.content, list)
        text_chunks = [c for c in result.content if isinstance(c, mistralai.TextChunk)]
        image_chunks = [
            c for c in result.content if isinstance(c, mistralai.ImageURLChunk)
        ]
        assert len(text_chunks) == 1
        assert text_chunks[0].text == "what is this?"
        assert len(image_chunks) == 1
        assert image_chunks[0].image_url.url == "data:image/png;base64,abc"

    def test_user_message_image_only_omits_text_chunk(self):
        msg = LLMMessage(
            role=Role.user,
            content="",
            image_parts=[ImageContentPart(image_url="data:image/png;base64,abc")],
        )
        result = MistralMapper().prepare_message(msg)
        assert isinstance(result.content, list)
        text_chunks = [c for c in result.content if isinstance(c, mistralai.TextChunk)]
        assert text_chunks == []

    def test_user_message_multiple_images(self):
        msg = LLMMessage(
            role=Role.user,
            content="compare these",
            image_parts=[
                ImageContentPart(image_url="data:image/png;base64,aaa"),
                ImageContentPart(image_url="data:image/jpeg;base64,bbb"),
            ],
        )
        result = MistralMapper().prepare_message(msg)
        image_chunks = [
            c for c in result.content if isinstance(c, mistralai.ImageURLChunk)
        ]
        assert len(image_chunks) == 2
        assert image_chunks[0].image_url.url == "data:image/png;base64,aaa"
        assert image_chunks[1].image_url.url == "data:image/jpeg;base64,bbb"

    def test_image_chunk_order_text_before_images(self):
        msg = LLMMessage(
            role=Role.user,
            content="describe",
            image_parts=[ImageContentPart(image_url="data:image/png;base64,xyz")],
        )
        result = MistralMapper().prepare_message(msg)
        assert isinstance(result.content[0], mistralai.TextChunk)
        assert isinstance(result.content[1], mistralai.ImageURLChunk)


# ── MistralBackend._strip_image_parts_if_needed ───────────────────────────────


def _make_provider() -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    )


def _vision_model() -> ModelConfig:
    return ModelConfig(
        name="pixtral-large-2411",
        provider="mistral",
        alias="pixtral-large",
        supports_vision=True,
    )


def _non_vision_model() -> ModelConfig:
    return ModelConfig(
        name="mistral-vibe-cli-latest",
        provider="mistral",
        alias="devstral-2",
        supports_vision=False,
    )


def _msg_with_image() -> LLMMessage:
    return LLMMessage(
        role=Role.user,
        content="what is this?",
        image_parts=[ImageContentPart(image_url="data:image/png;base64,abc")],
    )


class TestStripImageParts:
    def test_vision_model_keeps_image_parts(self):
        messages = [_msg_with_image()]
        result = MistralBackend._strip_image_parts_if_needed(_vision_model(), messages)
        assert result[0].image_parts is not None
        assert len(result[0].image_parts) == 1

    def test_non_vision_model_strips_image_parts(self):
        messages = [_msg_with_image()]
        result = MistralBackend._strip_image_parts_if_needed(
            _non_vision_model(), messages
        )
        assert result[0].image_parts is None

    def test_non_vision_model_does_not_mutate_original(self):
        msg = _msg_with_image()
        messages = [msg]
        MistralBackend._strip_image_parts_if_needed(_non_vision_model(), messages)
        assert msg.image_parts is not None

    def test_messages_without_image_parts_unaffected(self):
        msg = LLMMessage(role=Role.user, content="hello")
        result = MistralBackend._strip_image_parts_if_needed(_non_vision_model(), [msg])
        assert result[0].content == "hello"

    def test_strips_only_image_parts_preserves_content(self):
        msg = _msg_with_image()
        result = MistralBackend._strip_image_parts_if_needed(_non_vision_model(), [msg])
        assert result[0].content == "what is this?"
