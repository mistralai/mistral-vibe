from __future__ import annotations

import httpx

from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer


def _split_tool_call_factory(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return [
        StreamingMockServer.build_chunk(
            created=10,
            delta={
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_split",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command":"echo'},
                    }
                ],
            },
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=11,
            delta={"tool_calls": [{"function": {"arguments": ' ok"}'}}]},
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=12,
            delta={},
            finish_reason="tool_calls",
            usage={"prompt_tokens": 7, "completion_tokens": 8},
        ),
    ]


def test_non_streaming_completion_merges_split_tool_call_deltas() -> None:
    server = StreamingMockServer(chunk_factory=_split_tool_call_factory)
    server.start()
    try:
        response = httpx.post(
            f"{server.api_base}/chat/completions",
            json={"model": "mock-model", "messages": [], "stream": False},
            timeout=5,
        )
    finally:
        server.stop()

    response.raise_for_status()
    response_json = response.json()
    choices = response_json["choices"]
    assert isinstance(choices, list)
    choice = choices[0]
    assert isinstance(choice, dict)
    message = choice["message"]
    assert isinstance(message, dict)
    tool_calls = message["tool_calls"]
    assert isinstance(tool_calls, list)
    assert tool_calls == [
        {
            "index": 0,
            "id": "call_split",
            "type": "function",
            "function": {"name": "bash", "arguments": '{"command":"echo ok"}'},
        }
    ]
    assert choice["finish_reason"] == "tool_calls"
    assert response_json["usage"] == {"prompt_tokens": 7, "completion_tokens": 8}
    assert server.requests == [{"model": "mock-model", "messages": [], "stream": False}]
