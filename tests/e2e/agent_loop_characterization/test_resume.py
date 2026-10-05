from __future__ import annotations

import json
import os
from pathlib import Path
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


def _load_latest_session_entries() -> tuple[str, list[dict[str, object]]] | None:
    """Read the latest unified session's persisted public history entries.

    The unified store keeps one directory per session under
    ``logs/session/unified/<uuid>``; the durable public history lives in the
    latest generation's projection-state chunks (referenced from its
    manifest).
    """
    session_root = Path(os.environ["VIBE_HOME"]) / "logs" / "session" / "unified"
    session_dirs = [d for d in session_root.glob("*") if d.is_dir()]
    if not session_dirs:
        return None
    latest = max(session_dirs, key=lambda d: d.stat().st_mtime)
    try:
        metadata = json.loads((latest / "meta.json").read_text(encoding="utf-8"))
        manifests = sorted(latest.glob("generations/*/manifest.json"))
        if not manifests:
            return None
        # The generation naming contract lives in the runtime package; pick
        # the most recently written manifest rather than assuming the
        # directory names sort chronologically.
        manifest_path = max(
            manifests, key=lambda path: (path.stat().st_mtime, path.name)
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    session_id = metadata.get("session_id")
    if not isinstance(session_id, str):
        return None
    entries: list[dict[str, object]] = []
    for chunk in manifest.get("projection_state", {}).get("chunks", []):
        try:
            entries.extend(
                json.loads(
                    (latest / "chunks" / f"{chunk}.json").read_text(encoding="utf-8")
                )
            )
        except (OSError, json.JSONDecodeError):
            return None
    return session_id, entries


def _has_message_content(
    entries: list[dict[str, object]], *, role: str, expected: str
) -> bool:
    return any(
        entry.get("type") == "message"
        and entry.get("role") == role
        and expected in json.dumps(entry.get("content", []))
        for entry in entries
    )


def _has_tool_effect(entries: list[dict[str, object]], *, expected: str) -> bool:
    """The unified projection persists a tool call as an ``effect`` entry.

    The model-facing call id is not part of the public projection (the
    resumed-request assertions below cover the id replay); the entry carries
    the tool input and its result instead.
    """
    return any(
        entry.get("type") == "effect" and expected in json.dumps(entry)
        for entry in entries
    )


def _wait_for_persisted_resume_history(
    *,
    user_prompt: str,
    tool_result_text: str,
    final_assistant_text: str,
    timeout: float,
) -> str:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        persisted_session = _load_latest_session_entries()
        if persisted_session is None:
            time.sleep(0.05)
            continue
        session_id, entries = persisted_session
        if (
            _has_message_content(entries, role="user", expected=user_prompt)
            and _has_tool_effect(entries, expected=tool_result_text)
            and _has_message_content(
                entries, role="assistant", expected=final_assistant_text
            )
        ):
            return session_id
        time.sleep(0.05)

    persisted_session = _load_latest_session_entries()
    persisted_types = (
        [entry.get("type") for entry in persisted_session[1]]
        if persisted_session is not None
        else []
    )
    raise AssertionError(
        f"Timed out waiting for persisted resume history. Persisted types: {persisted_types}"
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


@pytest.mark.timeout(45)
@pytest.mark.unified_default
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
        session_id = _wait_for_persisted_resume_history(
            user_prompt=RESUME_INITIAL_PROMPT,
            tool_result_text=RESUME_TODO_RESULT_TEXT,
            final_assistant_text=RESUME_FIRST_TURN_RESPONSE,
            timeout=10,
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

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
            timeout=20,
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
