from __future__ import annotations

import json
from typing import Any

from tests.backend.data import (
    ANSWER_COMPLETION_TOKENS,
    ANSWER_PROMPT_TOKENS,
    Chunk,
    JsonResponse,
)


def _sse_event(data: dict[str, Any]) -> Chunk:
    return f"data: {json.dumps(data, separators=(',', ':'))}".encode()


def _thinking_block(reasoning: str) -> dict[str, Any]:
    return {"type": "thinking", "thinking": [{"type": "text", "text": reasoning}]}


def reasoning_message(
    text: str,
    *,
    prompt_tokens: int = ANSWER_PROMPT_TOKENS,
    completion_tokens: int = ANSWER_COMPLETION_TOKENS,
) -> JsonResponse:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def reasoning_thinking_message(
    text: str, reasoning: str, *, prompt_tokens: int = 12, completion_tokens: int = 5
) -> JsonResponse:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        _thinking_block(reasoning),
                        {"type": "text", "text": text},
                    ],
                }
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def reasoning_tool_use(
    name: str,
    arguments: str,
    *,
    reasoning: str | None = None,
    tool_id: str = "call_1",
    prompt_tokens: int = 20,
    completion_tokens: int = 5,
) -> JsonResponse:
    message: dict[str, Any] = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": tool_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }
    if reasoning is not None:
        message["content"] = [_thinking_block(reasoning)]
    return {
        "choices": [{"message": message}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def _delta_stream(
    deltas: list[dict[str, Any]], *, prompt_tokens: int, completion_tokens: int
) -> list[Chunk]:
    events = [
        {"choices": [{"delta": {"role": "assistant"}}]},
        *({"choices": [{"delta": delta}]} for delta in deltas),
        {
            "choices": [{"delta": {}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        },
    ]
    return [_sse_event(e) for e in events] + [b"data: [DONE]"]


def reasoning_text_stream(
    text: str, *, prompt_tokens: int = 10, completion_tokens: int = 3
) -> list[Chunk]:
    return _delta_stream(
        [{"content": text}],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def reasoning_thinking_tool_use_stream(
    name: str,
    arguments: str,
    *,
    reasoning: str = "thinking...",
    tool_id: str = "call_1",
    prompt_tokens: int = 20,
    completion_tokens: int = 5,
) -> list[Chunk]:
    tool_call = {
        "id": tool_id,
        "type": "function",
        "index": 0,
        "function": {"name": name, "arguments": arguments},
    }
    return _delta_stream(
        [{"content": [_thinking_block(reasoning)]}, {"tool_calls": [tool_call]}],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
