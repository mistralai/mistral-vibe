"""Generic OpenAI-style content lists: thinking chunks must never leak as dicts."""

from __future__ import annotations

from typing import Any

from mistralai_vibe_local_harness.vibe.adapters.generic._model import (
    LLMMessage as HarnessLLMMessage,
    Role as HarnessRole,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._openai_chat import (
    OpenAIAdapter,
    ReasoningAdapter,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._provider import ProviderView
from vibe.core.types import LLMMessage

_MISTRAL = ProviderView(name="mistral", api_base="https://api.mistral.ai/v1")
_CUSTOM_FIELD = ProviderView(
    name="test", api_base="https://api.example.com/v1", reasoning_field_name="reasoning"
)


def _thinking_chunk(text: str) -> dict[str, Any]:
    return {
        "type": "thinking",
        "thinking": [{"type": "text", "text": text}],
        "closed": True,
    }


def _delta(delta: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta}]}


def test_openai_adapter_splits_thinking_and_text_in_streaming_delta() -> None:
    data = _delta({
        "content": [
            _thinking_chunk("Let me think..."),
            {"type": "text", "text": "The answer"},
            {"type": "text", "text": " is 42."},
        ]
    })

    chunk = OpenAIAdapter().parse_response(data, _MISTRAL)

    assert chunk.message.role == HarnessRole.assistant
    assert chunk.message.content == "The answer is 42."
    assert chunk.message.reasoning_content == "Let me think..."


def test_openai_adapter_thinking_only_delta_has_no_text() -> None:
    data = _delta({"content": [_thinking_chunk("Hmm...")]})

    chunk = OpenAIAdapter().parse_response(data, _MISTRAL)

    assert chunk.message.content == ""
    assert chunk.message.reasoning_content == "Hmm..."


def test_openai_adapter_splits_non_streamed_message() -> None:
    data = {
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": [
                        _thinking_chunk("Step one. "),
                        _thinking_chunk("Step two."),
                        {"type": "text", "text": "Done."},
                    ],
                },
            }
        ]
    }

    chunk = OpenAIAdapter().parse_response(data, _MISTRAL)

    assert chunk.message.content == "Done."
    assert chunk.message.reasoning_content == "Step one. Step two."


def test_openai_adapter_keeps_reasoning_already_in_its_own_field() -> None:
    data = _delta({
        "reasoning": "From the field.",
        "content": [_thinking_chunk("From the chunk."), {"type": "text", "text": "A"}],
    })

    chunk = OpenAIAdapter().parse_response(data, _CUSTOM_FIELD)

    assert chunk.message.content == "A"
    assert chunk.message.reasoning_content == "From the field."


def test_openai_adapter_custom_field_absent_uses_thinking_chunks() -> None:
    data = _delta({
        "content": [_thinking_chunk("From the chunk."), {"type": "text", "text": "A"}]
    })

    chunk = OpenAIAdapter().parse_response(data, _CUSTOM_FIELD)

    assert chunk.message.content == "A"
    assert chunk.message.reasoning_content == "From the chunk."


def test_reasoning_adapter_still_splits_thinking_and_text() -> None:
    data = _delta({
        "content": [_thinking_chunk("Let me think..."), {"type": "text", "text": "A"}]
    })

    chunk = ReasoningAdapter().parse_response(data, _MISTRAL)

    assert chunk.message.content == "A"
    assert chunk.message.reasoning_content == "Let me think..."


def test_content_never_falls_back_to_dict_repr() -> None:
    thinking = _thinking_chunk("Let me think...")
    image: dict[str, Any] = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,"},
    }

    for model in (HarnessLLMMessage, LLMMessage):
        assert (
            model.model_validate({
                "role": "assistant",
                "content": [thinking, image],
            }).content
            == ""
        )
        assert (
            model.model_validate({"role": "assistant", "content": thinking}).content
            == ""
        )


def test_content_list_keeps_only_text_parts() -> None:
    data = {
        "role": "assistant",
        "content": [
            _thinking_chunk("Let me think..."),
            {"type": "text", "text": "Answer"},
        ],
    }

    assert LLMMessage.model_validate(data).content == "Answer"
    assert HarnessLLMMessage.model_validate(data).content == "Answer"
