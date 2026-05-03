from __future__ import annotations

import json
from typing import Any

from tests.backend.data import Chunk, JsonResponse, ResultData, Url

OPENAI_RESPONSES_TEST_BASE_URL = "https://api.openai.com"


def _sse_event(data: dict[str, Any] | str) -> Chunk:
    if data == "[DONE]":
        return b"data: [DONE]"
    return f"data: {json.dumps(data, separators=(',', ':'))}".encode()


def _usage(prompt_tokens: int = 0, completion_tokens: int = 0) -> dict[str, int]:
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


def _result(
    message: str = "",
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> ResultData:
    result: ResultData = {
        "message": message,
        "usage": _usage(prompt_tokens, completion_tokens),
    }
    if reasoning_content is not None:
        result["reasoning_content"] = reasoning_content
    if tool_calls is not None:
        result["tool_calls"] = tool_calls
    return result


def _tool_call_result(
    name: str | None, arguments: str, index: int | None
) -> dict[str, Any]:
    return {"name": name, "arguments": arguments, "index": index}


def _output_text(text: str) -> dict[str, Any]:
    return {"type": "output_text", "text": text, "annotations": [], "logprobs": []}


def _message_output_item(
    message_id: str, text: str, *, phase: str | None = None, status: str = "completed"
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": message_id,
        "type": "message",
        "status": status,
        "content": [_output_text(text)],
        "role": "assistant",
    }
    if phase is not None:
        item["phase"] = phase
    return item


def _stream_message_item(
    message_id: str, *, phase: str | None = None, status: str = "in_progress"
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": message_id,
        "type": "message",
        "status": status,
        "content": [],
        "role": "assistant",
    }
    if phase is not None:
        item["phase"] = phase
    return item


def _function_call_item(
    item_id: str, call_id: str, name: str, arguments: str, *, status: str = "completed"
) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": status,
    }


SIMPLE_CONVERSATION_PARAMS: list[tuple[Url, JsonResponse, ResultData]] = [
    (
        OPENAI_RESPONSES_TEST_BASE_URL,
        {
            "id": "resp_fake_id_1234",
            "object": "response",
            "created_at": 1234567890,
            "model": "gpt-4o-2024-08-06",
            "output": [
                _message_output_item(
                    "msg_fake_id_5678", "Hello! How can I help you today?"
                )
            ],
            "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
        },
        _result(
            "Hello! How can I help you today?", prompt_tokens=100, completion_tokens=200
        ),
    )
]

TOOL_CONVERSATION_PARAMS: list[tuple[Url, JsonResponse, ResultData]] = [
    (
        OPENAI_RESPONSES_TEST_BASE_URL,
        {
            "id": "resp_fake_id_9012",
            "object": "response",
            "created_at": 1234567890,
            "model": "gpt-4o-2024-08-06",
            "output": [
                _message_output_item("msg_fake_id_3456", ""),
                _function_call_item(
                    "fc_fake_id_7890",
                    "call_fake_id_1111",
                    "some_tool",
                    '{"some_argument": "some_argument_value"}',
                ),
            ],
            "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
        },
        _result(
            prompt_tokens=100,
            completion_tokens=200,
            tool_calls=[
                _tool_call_result(
                    "some_tool", '{"some_argument": "some_argument_value"}', 1
                )
            ],
        ),
    )
]

STREAMED_SIMPLE_CONVERSATION_PARAMS: list[tuple[Url, list[Chunk], list[ResultData]]] = [
    (
        OPENAI_RESPONSES_TEST_BASE_URL,
        [
            _sse_event({
                "type": "response.created",
                "response": {
                    "id": "resp_fake_id_1234",
                    "object": "response",
                    "created_at": 1234567890,
                    "model": "gpt-4o-2024-08-06",
                    "output": [],
                    "usage": None,
                },
            }),
            _sse_event({
                "type": "response.in_progress",
                "response": {"id": "resp_fake_id_1234"},
            }),
            _sse_event({
                "type": "response.output_item.added",
                "output_index": 0,
                "item": _stream_message_item("msg_fake_id_5678"),
            }),
            _sse_event({
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "part": _output_text(""),
            }),
            _sse_event({
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": "Hello",
            }),
            _sse_event({
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": "!",
            }),
            _sse_event({
                "type": "response.output_text.done",
                "output_index": 0,
                "content_index": 0,
                "text": "Hello!",
            }),
            _sse_event({
                "type": "response.content_part.done",
                "output_index": 0,
                "content_index": 0,
                "part": _output_text("Hello!"),
            }),
            _sse_event({
                "type": "response.output_item.done",
                "output_index": 0,
                "item": _message_output_item("msg_fake_id_5678", "Hello!"),
            }),
            _sse_event({
                "type": "response.completed",
                "response": {
                    "id": "resp_fake_id_1234",
                    "object": "response",
                    "created_at": 1234567890,
                    "model": "gpt-4o-2024-08-06",
                    "output": [_message_output_item("msg_fake_id_5678", "Hello!")],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 200,
                        "total_tokens": 300,
                    },
                },
            }),
            _sse_event("[DONE]"),
        ],
        [
            _result(),
            _result(),
            _result(),
            _result(),
            _result("Hello"),
            _result("!"),
            _result(),
            _result(),
            _result(),
            _result(prompt_tokens=100, completion_tokens=200),
        ],
    )
]

COMMENTARY_CONVERSATION_PARAMS: list[tuple[Url, JsonResponse, ResultData]] = [
    (
        OPENAI_RESPONSES_TEST_BASE_URL,
        {
            "id": "resp_thinking_1234",
            "object": "response",
            "created_at": 1234567890,
            "model": "gpt-5.4-2025-04-14",
            "output": [
                _message_output_item(
                    "msg_commentary_5678",
                    "The user said hello, I should respond warmly.",
                    phase="commentary",
                ),
                _message_output_item(
                    "msg_final_9012",
                    "Hello! How can I help you today?",
                    phase="final_answer",
                ),
            ],
            "usage": {"input_tokens": 150, "output_tokens": 250, "total_tokens": 400},
        },
        _result(
            "Hello! How can I help you today?",
            prompt_tokens=150,
            completion_tokens=250,
            reasoning_content="The user said hello, I should respond warmly.",
        ),
    )
]

STREAMED_COMMENTARY_CONVERSATION_PARAMS: list[
    tuple[Url, list[Chunk], list[ResultData]]
] = [
    (
        OPENAI_RESPONSES_TEST_BASE_URL,
        [
            _sse_event({
                "type": "response.created",
                "response": {
                    "id": "resp_thinking_1234",
                    "object": "response",
                    "created_at": 1234567890,
                    "model": "gpt-5.4-2025-04-14",
                    "output": [],
                    "usage": None,
                },
            }),
            _sse_event({
                "type": "response.in_progress",
                "response": {"id": "resp_thinking_1234"},
            }),
            _sse_event({
                "type": "response.output_item.added",
                "output_index": 0,
                "item": _stream_message_item("msg_commentary_5678", phase="commentary"),
            }),
            _sse_event({
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "part": _output_text(""),
            }),
            _sse_event({
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": "Thinking",
            }),
            _sse_event({
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": " about it...",
            }),
            _sse_event({
                "type": "response.output_text.done",
                "output_index": 0,
                "content_index": 0,
                "text": "Thinking about it...",
            }),
            _sse_event({
                "type": "response.content_part.done",
                "output_index": 0,
                "content_index": 0,
                "part": _output_text("Thinking about it..."),
            }),
            _sse_event({
                "type": "response.output_item.done",
                "output_index": 0,
                "item": _message_output_item(
                    "msg_commentary_5678", "Thinking about it...", phase="commentary"
                ),
            }),
            _sse_event({
                "type": "response.output_item.added",
                "output_index": 1,
                "item": _stream_message_item("msg_final_9012", phase="final_answer"),
            }),
            _sse_event({
                "type": "response.content_part.added",
                "output_index": 1,
                "content_index": 0,
                "part": _output_text(""),
            }),
            _sse_event({
                "type": "response.output_text.delta",
                "output_index": 1,
                "content_index": 0,
                "delta": "Hello",
            }),
            _sse_event({
                "type": "response.output_text.delta",
                "output_index": 1,
                "content_index": 0,
                "delta": "!",
            }),
            _sse_event({
                "type": "response.output_text.done",
                "output_index": 1,
                "content_index": 0,
                "text": "Hello!",
            }),
            _sse_event({
                "type": "response.content_part.done",
                "output_index": 1,
                "content_index": 0,
                "part": _output_text("Hello!"),
            }),
            _sse_event({
                "type": "response.output_item.done",
                "output_index": 1,
                "item": _message_output_item(
                    "msg_final_9012", "Hello!", phase="final_answer"
                ),
            }),
            _sse_event({
                "type": "response.completed",
                "response": {
                    "id": "resp_thinking_1234",
                    "object": "response",
                    "created_at": 1234567890,
                    "model": "gpt-5.4-2025-04-14",
                    "output": [
                        _message_output_item(
                            "msg_commentary_5678",
                            "Thinking about it...",
                            phase="commentary",
                        ),
                        _message_output_item(
                            "msg_final_9012", "Hello!", phase="final_answer"
                        ),
                    ],
                    "usage": {
                        "input_tokens": 150,
                        "output_tokens": 250,
                        "total_tokens": 400,
                    },
                },
            }),
            _sse_event("[DONE]"),
        ],
        [
            _result(),
            _result(),
            _result(),
            _result(),
            _result(reasoning_content="Thinking"),
            _result(reasoning_content=" about it..."),
            _result(),
            _result(),
            _result(),
            _result(),
            _result(),
            _result("Hello"),
            _result("!"),
            _result(),
            _result(),
            _result(),
            _result(prompt_tokens=150, completion_tokens=250),
        ],
    )
]

STREAMED_TOOL_CONVERSATION_PARAMS: list[tuple[Url, list[Chunk], list[ResultData]]] = [
    (
        OPENAI_RESPONSES_TEST_BASE_URL,
        [
            _sse_event({
                "type": "response.created",
                "response": {
                    "id": "resp_fake_id_9012",
                    "object": "response",
                    "created_at": 1234567890,
                    "model": "gpt-4o-2024-08-06",
                    "output": [],
                    "usage": None,
                },
            }),
            _sse_event({
                "type": "response.in_progress",
                "response": {"id": "resp_fake_id_9012"},
            }),
            _sse_event({
                "type": "response.output_item.added",
                "output_index": 0,
                "item": _function_call_item(
                    "fc_fake_id_7890",
                    "call_fake_id_1111",
                    "some_tool",
                    "",
                    status="in_progress",
                ),
            }),
            _sse_event({
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "call_id": "call_fake_id_1111",
                "delta": '{"some_argument": ',
            }),
            _sse_event({
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "call_id": "call_fake_id_1111",
                "delta": '"some_argument_value"}',
            }),
            _sse_event({
                "type": "response.function_call_arguments.done",
                "output_index": 0,
                "call_id": "call_fake_id_1111",
                "name": "some_tool",
                "arguments": '{"some_argument": "some_argument_value"}',
            }),
            _sse_event({
                "type": "response.output_item.done",
                "output_index": 0,
                "item": _function_call_item(
                    "fc_fake_id_7890",
                    "call_fake_id_1111",
                    "some_tool",
                    '{"some_argument": "some_argument_value"}',
                ),
            }),
            _sse_event({
                "type": "response.completed",
                "response": {
                    "id": "resp_fake_id_9012",
                    "object": "response",
                    "created_at": 1234567890,
                    "model": "gpt-4o-2024-08-06",
                    "output": [
                        _function_call_item(
                            "fc_fake_id_7890",
                            "call_fake_id_1111",
                            "some_tool",
                            '{"some_argument": "some_argument_value"}',
                        )
                    ],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 200,
                        "total_tokens": 300,
                    },
                },
            }),
            _sse_event("[DONE]"),
        ],
        [
            _result(),
            _result(),
            _result(tool_calls=[_tool_call_result("some_tool", "", 0)]),
            _result(),
            _result(),
            _result(
                tool_calls=[
                    _tool_call_result(
                        "some_tool", '{"some_argument": "some_argument_value"}', 0
                    )
                ]
            ),
            _result(),
            _result(prompt_tokens=100, completion_tokens=200),
        ],
    )
]
