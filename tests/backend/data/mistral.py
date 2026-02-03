from __future__ import annotations

from tests.backend.data import Chunk, JsonResponse, ResultData, Url

SIMPLE_CONVERSATION_PARAMS: list[tuple[Url, JsonResponse, ResultData]] = [
    (
        "https://api.mistral.ai",
        {
            "id": "fake_id_1234",
            "created": 1234567890,
            "model": "devstral-latest",
            "usage": {
                "prompt_tokens": 100,
                "total_tokens": 300,
                "completion_tokens": 200,
            },
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "tool_calls": None,
                        "content": "Some content",
                    },
                }
            ],
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
        "https://api.mistral.ai",
        {
            "id": "fake_id_1234",
            "created": 1234567890,
            "model": "devstral-latest",
            "usage": {
                "prompt_tokens": 100,
                "total_tokens": 300,
                "completion_tokens": 200,
            },
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "fake_id_5678",
                                "function": {
                                    "name": "some_tool",
                                    "arguments": '{"some_argument": "some_argument_value"}',
                                },
                                "index": 0,
                            }
                        ],
                        "content": "Some content",
                    },
                }
            ],
        },
        {
            "message": "Some content",
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
        "https://api.mistral.ai",
        [
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"content":"Some content"},"finish_reason":null}],"p":"abcde"}',
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"content":""},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"total_tokens":300,"completion_tokens":200},"p":"abcdefghijklmnopq"}',
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
        "https://api.mistral.ai",
        [
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"content":"Some content"},"finish_reason":null}],"p":"a"}',
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"tool_calls":[{"id":"fake_id_1234","function":{"name":"some_tool","arguments":""},"index":0}]},"finish_reason":null}],"p":"abcdef"}',
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"tool_calls":[{"function":{"name":"","arguments":"{\"some_argument\": "},"index":0}]},"finish_reason":null}],"p":"abcdefghijklmnopq"}',
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"tool_calls":[{"id":"null","function":{"name":"","arguments":"\"some_argument_value\"}"},"index":0}]},"finish_reason":null}],"p":"abcdefghijklmnopqrstuvwxyz0123456"}',
            rb'data: {"id":"fake_id_1234","object":"chat.completion.chunk","created":1234567890,"model":"devstral-latest","choices":[{"index":0,"delta":{"content":""},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":100,"total_tokens":300,"completion_tokens":200},"p":"abcdefghijklmnopq"}',
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
                "tool_calls": [{"name": "some_tool", "arguments": "", "index": 0}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
            {
                "message": "",
                "tool_calls": [
                    {"name": "", "arguments": '{"some_argument": ', "index": 0}
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
            {
                "message": "",
                "tool_calls": [
                    {"name": "", "arguments": '"some_argument_value"}', "index": 0}
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
            {"message": "", "usage": {"prompt_tokens": 100, "completion_tokens": 200}},
        ],
    )
]
