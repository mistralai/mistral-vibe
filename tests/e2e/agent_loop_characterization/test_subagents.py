from __future__ import annotations

from pathlib import Path

import pexpect
import pytest

from tests.e2e.agent_loop_characterization.support import (
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

SUBAGENT_TOOL_CALL_ID = "call_subagent_explore"
SUBAGENT_MARKER = "__E2E_SUBAGENT_DONE__"


def _explore_subagent_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        return single_tool_call_chunks(
            call_id=SUBAGENT_TOOL_CALL_ID,
            tool_name="task",
            arguments={
                "agent": "explore",
                "task": f"Find the marker {SUBAGENT_MARKER}.",
            },
            created=90,
        )
    if request_index == 1:
        return assistant_text_chunks(f"Subagent found {SUBAGENT_MARKER}.", created=100)

    return assistant_text_chunks("Parent used the subagent result.", created=110)


@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_explore_subagent_factory, id="explore-subagent")],
    indirect=True,
)
def test_explore_subagent_returns_result_to_parent_model(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Delegate exploration")
        child.send("\r")

        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=3,
            timeout=15,
        )
        wait_for_rendered_text(
            child, captured, needle="Parent used the subagent result.", timeout=10
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert streaming_mock_server.requests[1].get("stream") is not True
    assert_message_content_present(
        streaming_mock_server.requests[1],
        role="user",
        expected=f"Find the marker {SUBAGENT_MARKER}.",
    )
    assert_tool_result_contains(
        streaming_mock_server.requests[2],
        call_id=SUBAGENT_TOOL_CALL_ID,
        expected=f"Subagent found {SUBAGENT_MARKER}.",
    )
