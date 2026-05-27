from __future__ import annotations

from tests.backend.data import Chunk, JsonResponse, ResultData, Url

SIMPLE_CONVERSATION_PARAMS: list[tuple[Url, JsonResponse, ResultData]] = [
    (
        "https://api.minimax.io",
        {
            "id": "fake_id_minimax_1234",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "MiniMax-M2.7",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Some content",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "total_tokens": 300,
                "completion_tokens": 200,
            },
        },
        {
            "message": "Some content",
            "usage": {
                "prompt_tokens": 100,
                "total_tokens": 300,
                "completion_tokens": 200,
            },
        },
    )
]

TOOL_CONVERSATION_PARAMS: list[tuple[Url, JsonResponse, ResultData]] = [
    (
        "https://api.minimax.io",
        {
            "id": "fake_id_minimax_1234",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "MiniMax-M2.7",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "fake_id_minimax_5678",
                                "type": "function",
                                "function": {
                                    "name": "some_tool",
                                    "arguments": '{"some_argument": "some_argument_value"}',
                                },
                                "name": None,
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 200,
            },
        },
        {
            "message": "",
            "tool_calls": [
                {
                    "name": "some_tool",
                    "arguments": '{"some_argument": "some_argument_value"}',
                    "index": 0,
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        },
    )
]

STREAMED_SIMPLE_CONVERSATION_PARAMS: list[tuple[Url, list[Chunk], list[ResultData]]] = [
    (
        "https://api.minimax.io",
        [
            rb'data: {"id":"fake_id_minimax_1234","object":"chat.completion.chunk","created":1234567890,"model":"MiniMax-M2.7","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}],"usage":null}',
            rb'data: {"id":"fake_id_minimax_1234","object":"chat.completion.chunk","created":1234567890,"model":"MiniMax-M2.7","choices":[{"index":0,"delta":{"content":"Some content"},"finish_reason":null}],"usage":null}',
            rb'data: {"id":"fake_id_minimax_1234","object":"chat.completion.chunk","created":1234567890,"model":"MiniMax-M2.7","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"total_tokens":300,"completion_tokens":200}}',
            rb"data: [DONE]",
        ],
        [
            {"message": "", "usage": {"prompt_tokens": 0, "completion_tokens": 0}},
            {
                "message": "Some content",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
            {"message": "", "usage": {"prompt_tokens": 100, "completion_tokens": 200}},
        ],
    )
]


STREAMED_TOOL_CONVERSATION_PARAMS: list[tuple[Url, list[Chunk], list[ResultData]]] = [
    (
        "https://api.minimax.io",
        [
            rb'data: {"id": "fake_id_minimax_1234","object": "chat.completion.chunk","created": 1234567890,"model": "MiniMax-M2.7","choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": null}],"usage": null}',
            rb'data: {"id": "fake_id_minimax_1234","object": "chat.completion.chunk","created": 1234567890,"model": "MiniMax-M2.7","choices": [{"index": 0,"delta": {"content": "Some content"},"finish_reason": null}],"usage": null}',
            rb'data: {"id": "fake_id_minimax_1234","object": "chat.completion.chunk","created": 1234567890,"model": "MiniMax-M2.7","choices": [{"index": 0,"delta": {"tool_calls": [{"index": 0,"id": "fake_id_minimax_151617","type": "function","function": {"name": "some_tool"}}]},"finish_reason": null}],"usage": null}',
            rb'data: {"id": "fake_id_minimax_1234","object": "chat.completion.chunk","created": 1234567890,"model": "MiniMax-M2.7","choices": [{"index": 0,"delta": {"tool_calls": [{"index": 0,"id": null,"type": "function","function": {"arguments": "{\"some_argument\": \"some_arguments_value\"}"}}]},"finish_reason": null}],"usage": null}',
            rb'data: {"id": "fake_id_minimax_1234","object": "chat.completion.chunk","created": 1234567890,"model": "MiniMax-M2.7","choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],"usage": {"prompt_tokens": 100,"total_tokens": 300,"completion_tokens": 200}}',
            rb"data: [DONE]",
        ],
        [
            {"message": "", "usage": {"prompt_tokens": 0, "completion_tokens": 0}},
            {
                "message": "Some content",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
            {
                "message": "",
                "tool_calls": [{"name": "some_tool", "arguments": None, "index": 0}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
            {
                "message": "",
                "tool_calls": [
                    {
                        "name": None,
                        "arguments": '{"some_argument": "some_arguments_value"}',
                        "index": 0,
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
            {"message": "", "usage": {"prompt_tokens": 100, "completion_tokens": 200}},
        ],
    )
]
