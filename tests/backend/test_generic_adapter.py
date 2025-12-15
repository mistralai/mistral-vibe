from __future__ import annotations

import pytest

from vibe.core.llm.backend.generic import OpenAIAdapter


class TestOpenAIAdapter:
    def test_parse_response_handles_message_without_choices(self) -> None:
        adapter = OpenAIAdapter()
        data = {
            "message": {"role": "assistant", "content": "Hello from LM Studio"},
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }

        result = adapter.parse_response(data)

        assert result.message.content == "Hello from LM Studio"
        assert result.finish_reason == "stop"
        assert result.usage is not None
        assert result.usage.prompt_tokens == 1
        assert result.usage.completion_tokens == 2

    def test_parse_response_surfaces_error_object(self) -> None:
        adapter = OpenAIAdapter()
        error_payload = {"message": "Upstream backend unavailable"}

        with pytest.raises(ValueError, match="Upstream backend unavailable"):
            adapter.parse_response({"error": error_payload})
