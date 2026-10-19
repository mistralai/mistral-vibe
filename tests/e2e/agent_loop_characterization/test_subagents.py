from __future__ import annotations

import json
from pathlib import Path

import pexpect
import pytest

from tests.e2e.agent_loop_characterization.support import (
    assert_message_content_present,
    assistant_text_chunks,
    single_tool_call_chunks,
    tool_result_content,
    wait_for_request_count_while_draining_child_output,
)
from tests.e2e.common import (
    SpawnedVibeProcessFixture,
    send_ctrl_c_until_quit_confirmation,
    wait_for_main_screen,
    wait_for_rendered_text,
)
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

PARENT_PROMPT = "Delegate exploration"
SUBAGENT_TOOL_CALL_ID = "call_subagent_spawn"
SUBAGENT_MARKER = "__E2E_SUBAGENT_DONE__"
SUBAGENT_NAME = "explorer-1"
SUBAGENT_SPAWN_MESSAGE = f"Find the marker {SUBAGENT_MARKER}."
SUBAGENT_ANSWER = f"Subagent found {SUBAGENT_MARKER}."
PARENT_ANSWER = "Parent used the subagent result."

# The unified runtime exposes subagents as the programmatic ``tools.agents``
# namespace, called from ``run_typescript``. There is no direct ``task`` tool
# and no declared builtin agent types: a bare spawn inherits the parent's
# prompt and tools (see ``workspace_agent_types``). Spawn admits the child's
# first message and returns at once; the child's result is pushed to the
# parent as a notification. ``wait`` is a direct tool, not a programmatic one,
# so this program lists its children until this one is done, which keeps the
# parent's Turn open for that notification. The ``wait`` variant is below.
SPAWN_CODE = (
    "async function main() {\n"
    f"  await tools.agents.spawn({{ name: '{SUBAGENT_NAME}', "
    f"message: '{SUBAGENT_SPAWN_MESSAGE}' }});\n"
    "  let child;\n"
    "  for (let attempt = 0; attempt < 100; attempt++) {\n"
    "    const { agents } = await tools.agents.list({});\n"
    f"    child = agents.find((agent) => agent.name === '{SUBAGENT_NAME}');\n"
    "    if (child && child.status !== 'running') break;\n"
    "    await tools.self.sleep({ seconds: 0.2 });\n"
    "  }\n"
    "  return child;\n"
    "}"
)


def _is_parent_request(payload: ChatCompletionsRequestPayload) -> bool:
    return any(
        message.get("role") == "user" and PARENT_PROMPT in str(message.get("content"))
        for message in payload.get("messages") or []
    )


def _carries_subagent_answer(payload: ChatCompletionsRequestPayload) -> bool:
    return any(
        message.get("role") == "user"
        and "Runtime notification:" in str(message.get("content"))
        and SUBAGENT_ANSWER in str(message.get("content"))
        for message in payload.get("messages") or []
    )


def _spawn_subagent_factory(
    _request_index: int, payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    # The child's request and the parent's can interleave, so answer by
    # content rather than by request order.
    if not _is_parent_request(payload):
        return assistant_text_chunks(SUBAGENT_ANSWER, created=100)
    if not any(
        message.get("role") == "tool" for message in payload.get("messages") or []
    ):
        return single_tool_call_chunks(
            call_id=SUBAGENT_TOOL_CALL_ID,
            tool_name="run_typescript",
            arguments={"code": SPAWN_CODE},
            created=90,
        )
    if _carries_subagent_answer(payload):
        return assistant_text_chunks(PARENT_ANSWER, created=110)
    # The notification may land while this request is pending; the runtime
    # then asks the model once more within the same Turn.
    return assistant_text_chunks("Waiting for the subagent.", created=120)


@pytest.mark.timeout(45)
@pytest.mark.unified_default
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_spawn_subagent_factory, id="spawn-subagent")],
    indirect=True,
)
def test_spawned_subagent_returns_result_to_parent_model(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send(PARENT_PROMPT)
        child.send("\r")

        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=3,
            timeout=30,
        )
        wait_for_rendered_text(child, captured, needle=PARENT_ANSWER, timeout=10)

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    requests = streaming_mock_server.requests
    parent_requests = [request for request in requests if _is_parent_request(request)]
    # The spawned child completes against the session's own provider, with
    # the spawn message as its initial prompt. Unlike the legacy ``task``
    # subagent, the unified child streams.
    [subagent_request] = [
        request for request in requests if not _is_parent_request(request)
    ]
    assert subagent_request.get("stream") is True
    assert_message_content_present(
        subagent_request, role="user", expected=SUBAGENT_SPAWN_MESSAGE
    )
    # The program saw the child finish its Turn.
    final_request = parent_requests[-1]
    listed_child = json.loads(
        tool_result_content(final_request, call_id=SUBAGENT_TOOL_CALL_ID)
    )
    assert listed_child["status"] == "idle"
    # The child's final message reaches the parent model as a runtime
    # notification in the request the parent answered from, within the
    # same Turn.
    assert _carries_subagent_answer(final_request)


WAITING_PARENT_PROMPT = "Delegate exploration and wait"
WAIT_TOOL_CALL_ID = "call_wait_for_subagent"
CHILD_PAUSE_CALL_ID = "call_child_pause"

# The parent spawns without polling, then calls the direct ``wait`` tool.
SPAWN_ONLY_CODE = (
    "async function main() {\n"
    f"  return await tools.agents.spawn({{ name: '{SUBAGENT_NAME}', "
    f"message: '{SUBAGENT_SPAWN_MESSAGE}' }});\n"
    "}"
)
# The child pauses first, so the parent is already waiting when it finishes.
CHILD_PAUSE_CODE = (
    "async function main() {\n  await tools.self.sleep({ seconds: 1 });\n}"
)


def _is_waiting_parent_request(payload: ChatCompletionsRequestPayload) -> bool:
    return any(
        message.get("role") == "user"
        and WAITING_PARENT_PROMPT in str(message.get("content"))
        for message in payload.get("messages") or []
    )


def _tool_call_ids(payload: ChatCompletionsRequestPayload) -> set[str]:
    return {
        str(message.get("tool_call_id"))
        for message in payload.get("messages") or []
        if message.get("role") == "tool"
    }


def _waiting_parent_factory(
    _request_index: int, payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    answered = _tool_call_ids(payload)
    if not _is_waiting_parent_request(payload):
        if CHILD_PAUSE_CALL_ID not in answered:
            return single_tool_call_chunks(
                call_id=CHILD_PAUSE_CALL_ID,
                tool_name="run_typescript",
                arguments={"code": CHILD_PAUSE_CODE},
                created=95,
            )
        return assistant_text_chunks(SUBAGENT_ANSWER, created=100)
    if SUBAGENT_TOOL_CALL_ID not in answered:
        return single_tool_call_chunks(
            call_id=SUBAGENT_TOOL_CALL_ID,
            tool_name="run_typescript",
            arguments={"code": SPAWN_ONLY_CODE},
            created=90,
        )
    if WAIT_TOOL_CALL_ID not in answered:
        return single_tool_call_chunks(
            call_id=WAIT_TOOL_CALL_ID,
            tool_name="wait",
            arguments={"timeout_ms": 20_000},
            created=105,
        )
    return assistant_text_chunks(PARENT_ANSWER, created=110)


@pytest.mark.timeout(45)
@pytest.mark.unified_default
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_waiting_parent_factory, id="wait-for-subagent")],
    indirect=True,
)
def test_subagent_result_wakes_a_waiting_parent_model(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send(WAITING_PARENT_PROMPT)
        child.send("\r")

        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=5,
            timeout=30,
        )
        wait_for_rendered_text(child, captured, needle=PARENT_ANSWER, timeout=10)

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    requests = streaming_mock_server.requests
    parent_requests = [
        request for request in requests if _is_waiting_parent_request(request)
    ]
    # Spawn, wait, answer: the parent model asks exactly three times.
    assert len(parent_requests) == 3
    waiting_request, final_request = parent_requests[1], parent_requests[2]
    # The child had not finished when the parent chose to wait.
    assert not _carries_subagent_answer(waiting_request)
    # The wait woke on the child's result, which the same Turn's next model
    # request carries as a runtime notification.
    assert json.loads(
        tool_result_content(final_request, call_id=WAIT_TOOL_CALL_ID)
    ) == {"reason": "input_available"}
    assert _carries_subagent_answer(final_request)
