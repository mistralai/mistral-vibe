from __future__ import annotations

from collections.abc import Callable
import io
import json
from pathlib import Path
import time
import tomllib
from typing import Any

import pexpect
import tomli_w

from tests.e2e.common import strip_ansi, wait_for_rendered_text
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

APPROVAL_INPUT_GRACE_PERIOD_S = 0.65


def assistant_text_chunks(text: str, *, created: int = 100) -> list[dict[str, object]]:
    return [
        StreamingMockServer.build_chunk(
            created=created,
            delta={"role": "assistant", "content": text},
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=created + 1,
            delta={},
            finish_reason="stop",
            usage={"prompt_tokens": 3, "completion_tokens": 4},
        ),
    ]


def single_tool_call_chunks(
    *, call_id: str, tool_name: str, arguments: dict[str, Any], created: int = 10
) -> list[dict[str, object]]:
    return [
        StreamingMockServer.build_chunk(
            created=created,
            delta=StreamingMockServer.build_tool_call_delta(
                call_id=call_id,
                tool_name=tool_name,
                arguments=json.dumps(arguments, separators=(",", ":")),
            ),
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=created + 1,
            delta={},
            finish_reason="tool_calls",
            usage={"prompt_tokens": 3, "completion_tokens": 4},
        ),
    ]


def multi_tool_call_chunks(
    tool_calls: list[tuple[str, str, dict[str, Any]]], *, created: int = 10
) -> list[dict[str, object]]:
    return [
        StreamingMockServer.build_chunk(
            created=created,
            delta={
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": index,
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(arguments, separators=(",", ":")),
                        },
                    }
                    for index, (call_id, tool_name, arguments) in enumerate(tool_calls)
                ],
            },
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=created + 1,
            delta={},
            finish_reason="tool_calls",
            usage={"prompt_tokens": 3, "completion_tokens": 4},
        ),
    ]


def _messages(payload: ChatCompletionsRequestPayload) -> list[dict[str, Any]]:
    raw_messages = payload.get("messages")
    assert raw_messages is not None
    return [dict(message) for message in raw_messages]


def assert_tool_result_contains(
    payload: ChatCompletionsRequestPayload, *, call_id: str, expected: str
) -> None:
    matching_messages = [
        message
        for message in _messages(payload)
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id
    ]
    assert len(matching_messages) == 1
    content = matching_messages[0].get("content")
    assert isinstance(content, str)
    assert expected in content, content


def assert_assistant_tool_call_present(
    payload: ChatCompletionsRequestPayload, *, call_id: str, tool_name: str
) -> None:
    for message in _messages(payload):
        if message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            if tool_call.get("id") == call_id and function.get("name") == tool_name:
                return
    raise AssertionError(f"Tool call {call_id!r} for {tool_name!r} was not present.")


def assert_message_content_present(
    payload: ChatCompletionsRequestPayload, *, role: str, expected: str
) -> None:
    assert any(
        message.get("role") == role and expected in str(message.get("content", ""))
        for message in _messages(payload)
    )


def wait_for_request_count_while_draining_child_output(
    child: pexpect.spawn,
    captured: io.StringIO,
    request_count_getter: Callable[[], int],
    *,
    expected_count: int,
    timeout: float,
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if request_count_getter() >= expected_count:
            return
        try:
            child.expect(r"\S", timeout=0.05)
        except pexpect.TIMEOUT:
            pass
    rendered_tail = strip_ansi(captured.getvalue())[-1200:]
    raise AssertionError(
        f"Timed out waiting for {expected_count} backend request(s).\n\n"
        f"Rendered tail:\n{rendered_tail}"
    )


def answer_approval(
    child: pexpect.spawn, captured: io.StringIO, *, tool_name: str, key: str
) -> None:
    wait_for_rendered_text(
        child, captured, needle=f"Permission for the {tool_name} tool", timeout=10
    )
    wait_for_rendered_text(child, captured, needle="Deny", timeout=10)
    time.sleep(APPROVAL_INPUT_GRACE_PERIOD_S)
    child.send(key)
    child.send("\r")


def set_tool_denylist(vibe_home: Path, tool_name: str, patterns: list[str]) -> None:
    config_path = vibe_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("tools", {}).setdefault(tool_name, {})["denylist"] = patterns
    config_path.write_bytes(tomli_w.dumps(config).encode())
