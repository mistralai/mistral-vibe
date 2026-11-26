from __future__ import annotations

import json
from typing import Any


def mistral_completion(
    content: str,
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": "cmpl_test",
        "created": 1234567890,
        "model": "devstral-latest",
        "object": "chat.completion",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                },
            }
        ],
    }


def mistral_sse_payload(
    content: str,
    tool_calls: list[dict[str, Any]] | None,
    *,
    finish_reason: str | None = None,
) -> bytes:
    """One streamed chat completion, the way the Mistral API sends it."""
    response = mistral_completion(content, tool_calls=tool_calls)
    response["model"] = "mistral-vibe-cli-latest"
    response["object"] = "chat.completion.chunk"
    choice = response["choices"][0]
    message = choice.pop("message")
    if tool_calls is None:
        message.pop("tool_calls")
    choice["delta"] = message
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return b"data: " + json.dumps(response).encode() + b"\n\ndata: [DONE]"
