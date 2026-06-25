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


def _block_start(index: int, block: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def _block_delta(index: int, delta: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_delta", "index": index, "delta": delta}


def _content_stream(
    blocks: list[dict[str, Any]], *, input_tokens: int, output_tokens: int, stop: str
) -> list[Chunk]:
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": input_tokens}}},
        *blocks,
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop},
            "usage": {"output_tokens": output_tokens},
        },
        {"type": "message_stop"},
    ]
    return [_sse_event(e) for e in events]


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
    input_tokens: int = ANSWER_PROMPT_TOKENS,
    output_tokens: int = ANSWER_COMPLETION_TOKENS,
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
    return _content_stream(
        [
            _block_start(0, {"type": "thinking", "thinking": reasoning}),
            _block_delta(0, {"type": "signature_delta", "signature": signature}),
            {"type": "content_block_stop", "index": 0},
            _block_start(1, {"type": "tool_use", "id": tool_id, "name": name}),
            _block_delta(1, {"type": "input_json_delta", "partial_json": arguments}),
            {"type": "content_block_stop", "index": 1},
        ],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop="tool_use",
    )


def anthropic_text_stream(
    text: str, *, input_tokens: int = 12, output_tokens: int = 3
) -> list[Chunk]:
    return _content_stream(
        [
            _block_start(0, {"type": "text", "text": ""}),
            _block_delta(0, {"type": "text_delta", "text": text}),
            {"type": "content_block_stop", "index": 0},
        ],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop="end_turn",
    )
