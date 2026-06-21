from __future__ import annotations

import json
from typing import Any

from tests.backend.data import Chunk, JsonResponse


def _sse_event(data: dict[str, Any]) -> Chunk:
    return f"data: {json.dumps(data, separators=(',', ':'))}".encode()


def anthropic_request_content_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # Flatten every structured content block across a request's messages
    # (text/thinking/tool_use/tool_result blocks).
    return [
        block
        for message in payload["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
    ]


def anthropic_message(
    text: str,
    *,
    input_tokens: int = 12,
    output_tokens: int = 3,
    stop_reason: str = "end_turn",
) -> JsonResponse:
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "stop_reason": stop_reason,
    }


def anthropic_tool_use(
    name: str,
    tool_input: dict[str, Any],
    *,
    tool_id: str = "toolu_1",
    input_tokens: int = 20,
    output_tokens: int = 5,
) -> JsonResponse:
    return {
        "content": [
            {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}
        ],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "stop_reason": "tool_use",
    }


def anthropic_reasoning_tool_use_stream(
    name: str,
    arguments: str,
    *,
    reasoning: str = "thinking...",
    signature: str = "sig",
    tool_id: str = "toolu_1",
    input_tokens: int = 20,
    output_tokens: int = 5,
) -> list[Chunk]:
    return [
        _sse_event(e)
        for e in (
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": input_tokens}},
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": reasoning},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": signature},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": tool_id, "name": name},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": arguments},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": output_tokens},
            },
            {"type": "message_stop"},
        )
    ]


def anthropic_text_stream(
    text: str, *, input_tokens: int = 12, output_tokens: int = 3
) -> list[Chunk]:
    return [
        _sse_event(e)
        for e in (
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": input_tokens}},
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": output_tokens},
            },
            {"type": "message_stop"},
        )
    ]
