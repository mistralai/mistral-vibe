from __future__ import annotations

import json

from chefchat.core.types import LLMChunk, LLMMessage, LLMUsage, Role, ToolCall

MOCK_DATA_ENV_VAR = "VIBE_MOCK_LLM_DATA"


def mock_llm_chunk(
    content: str = "Hello!",
    role: Role = Role.assistant,
    tool_calls: list[ToolCall] | None = None,
    name: str | None = None,
    tool_call_id: str | None = None,
    finish_reason: str | None = None,
    usage: LLMUsage | None = None, # Allow explicit None for usage
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> LLMChunk:
    message = LLMMessage(
        role=role,
        content=content,
        tool_calls=tool_calls,
        name=name,
        tool_call_id=tool_call_id,
    )
    # Only create LLMUsage if not explicitly passed as None
    if usage is None and (prompt_tokens is not None or completion_tokens is not None):
        usage = LLMUsage(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )
    return LLMChunk(
        message=message,
        usage=usage,
        finish_reason=finish_reason,
    )


def get_mocking_env(mock_chunks: list[LLMChunk] | None = None) -> dict[str, str]:
    if mock_chunks is None:
        mock_chunks = [mock_llm_chunk()]

    mock_data = [LLMChunk.model_dump(mock_chunk) for mock_chunk in mock_chunks]

    return {MOCK_DATA_ENV_VAR: json.dumps(mock_data)}
