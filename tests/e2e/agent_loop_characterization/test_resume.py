from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time

import pexpect
import pytest

from tests.e2e.agent_loop_characterization.support import (
    assert_assistant_tool_call_present,
    assert_message_content_present,
    assert_tool_result_contains,
    assistant_text_chunks,
    single_tool_call_chunks,
    wait_for_request_count_while_draining_child_output,
)
from tests.e2e.common import (
    SpawnedVibeProcessFixture,
    send_ctrl_c_until_quit_confirmation,
    strip_ansi,
    wait_for_main_screen,
    wait_for_rendered_text,
)
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

RESUME_TODO_CALL_ID = "call_todo_resume"
RESUME_INITIAL_PROMPT = "Start tool history"
RESUME_CONTINUE_PROMPT = "Continue from prior tool history"
RESUME_FIRST_TURN_RESPONSE = "First turn with todo complete."
RESUME_RESUMED_TURN_RESPONSE = "Resumed turn saw prior tool history."
RESUME_TODO_RESULT_TEXT = "Updated 1 todos"


def _load_latest_session_messages() -> list[dict[str, object]]:
    session_root = Path(os.environ["VIBE_HOME"]) / "logs" / "session"
    messages_paths = list(session_root.glob("session_*/messages.jsonl"))
    if not messages_paths:
        return []

    messages_path = max(messages_paths, key=lambda path: path.stat().st_mtime)
    messages: list[dict[str, object]] = []
    try:
        for line in messages_path.read_text(encoding="utf-8").splitlines():
            messages.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return []
    return messages


def _has_message_content(
    messages: list[dict[str, object]], *, role: str, expected: str
) -> bool:
    return any(
        message.get("role") == role and expected in str(message.get("content", ""))
        for message in messages
    )


def _has_assistant_tool_call(
    messages: list[dict[str, object]], *, call_id: str
) -> bool:
    return any(
        message.get("role") == "assistant"
        and call_id in json.dumps(message.get("tool_calls", []))
        for message in messages
    )


def _has_tool_result(
    messages: list[dict[str, object]], *, call_id: str, expected: str
) -> bool:
    return any(
        message.get("role") == "tool"
        and message.get("tool_call_id") == call_id
        and expected in str(message.get("content", ""))
        for message in messages
    )


def _wait_for_persisted_resume_history(
    *,
    user_prompt: str,
    assistant_tool_call_id: str,
    tool_result_text: str,
    final_assistant_text: str,
    timeout: float,
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        messages = _load_latest_session_messages()
        if (
            _has_message_content(messages, role="user", expected=user_prompt)
            and _has_assistant_tool_call(messages, call_id=assistant_tool_call_id)
            and _has_tool_result(
                messages, call_id=assistant_tool_call_id, expected=tool_result_text
            )
            and _has_message_content(
                messages, role="assistant", expected=final_assistant_text
            )
        ):
            return
        time.sleep(0.05)

    persisted_roles = [
        message.get("role") for message in _load_latest_session_messages()
    ]
    raise AssertionError(
        f"Timed out waiting for persisted resume history. Persisted roles: {persisted_roles}"
    )


def _resume_tool_history_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        return single_tool_call_chunks(
            call_id=RESUME_TODO_CALL_ID,
            tool_name="todo",
            arguments={
                "action": "write",
                "todos": [
                    {
                        "id": "resume-history",
                        "content": "preserve tool history",
                        "status": "completed",
                        "priority": "high",
                    }
                ],
            },
            created=40,
        )
    if request_index == 1:
        return assistant_text_chunks(RESUME_FIRST_TURN_RESPONSE, created=50)

    return assistant_text_chunks(RESUME_RESUMED_TURN_RESPONSE, created=60)


@pytest.mark.timeout(35)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_resume_tool_history_factory, id="resume-tool-history")],
    indirect=True,
)
def test_resumed_session_sends_prior_tool_call_and_result_history_to_the_model(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send(RESUME_INITIAL_PROMPT)
        child.send("\r")

        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=10,
        )
        wait_for_rendered_text(
            child, captured, needle=RESUME_FIRST_TURN_RESPONSE, timeout=10
        )
        _wait_for_persisted_resume_history(
            user_prompt=RESUME_INITIAL_PROMPT,
            assistant_tool_call_id=RESUME_TODO_CALL_ID,
            tool_result_text=RESUME_TODO_RESULT_TEXT,
            final_assistant_text=RESUME_FIRST_TURN_RESPONSE,
            timeout=10,
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    first_output = strip_ansi(captured.getvalue())
    resume_match = re.search(r"Or: vibe --resume ([0-9a-f-]+)", first_output)
    assert resume_match is not None
    session_id = resume_match.group(1)

    with spawned_vibe_process(e2e_workdir, extra_args=["--resume", session_id]) as (
        resumed_child,
        resumed_captured,
    ):
        wait_for_main_screen(resumed_child, timeout=15)
        resumed_child.send(RESUME_CONTINUE_PROMPT)
        resumed_child.send("\r")

        wait_for_request_count_while_draining_child_output(
            resumed_child,
            resumed_captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=3,
            timeout=10,
        )
        wait_for_rendered_text(
            resumed_child,
            resumed_captured,
            needle=RESUME_RESUMED_TURN_RESPONSE,
            timeout=10,
        )

        send_ctrl_c_until_quit_confirmation(resumed_child, resumed_captured, timeout=5)
        resumed_child.expect(pexpect.EOF, timeout=10)

    resumed_payload = streaming_mock_server.requests[2]
    assert_message_content_present(
        resumed_payload, role="user", expected=RESUME_INITIAL_PROMPT
    )
    assert_assistant_tool_call_present(
        resumed_payload, call_id=RESUME_TODO_CALL_ID, tool_name="todo"
    )
    assert_tool_result_contains(
        resumed_payload, call_id=RESUME_TODO_CALL_ID, expected=RESUME_TODO_RESULT_TEXT
    )
    assert_message_content_present(
        resumed_payload, role="assistant", expected=RESUME_FIRST_TURN_RESPONSE
    )
    assert_message_content_present(
        resumed_payload, role="user", expected=RESUME_CONTINUE_PROMPT
    )
