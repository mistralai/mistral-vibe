from __future__ import annotations

import io
from pathlib import Path
import time

import pexpect
import pytest

from tests.e2e.agent_loop_characterization.support import (
    APPROVAL_INPUT_GRACE_PERIOD_S,
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
    wait_for_request_count,
)
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

QUESTION_CALL_ID = "call_question_mode"
QUESTION_TEXT = "Which mode should Vibe use?"


def _answer_first_question_option(child: pexpect.spawn, captured: io.StringIO) -> None:
    wait_for_rendered_text(child, captured, needle=QUESTION_TEXT, timeout=10)
    wait_for_rendered_text(child, captured, needle="Fast", timeout=10)
    time.sleep(APPROVAL_INPUT_GRACE_PERIOD_S)
    child.send("\r")


def _ask_user_question_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        return single_tool_call_chunks(
            call_id=QUESTION_CALL_ID,
            tool_name="ask_user_question",
            arguments={
                "questions": [
                    {
                        "question": QUESTION_TEXT,
                        "header": "Mode",
                        "options": [
                            {"label": "Fast", "description": "Move quickly"},
                            {"label": "Careful", "description": "Add checks"},
                        ],
                        "hide_other": True,
                    }
                ]
            },
            created=70,
        )

    return assistant_text_chunks("The selected mode was Fast.", created=80)


@pytest.mark.timeout(25)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_ask_user_question_factory, id="ask-user-question")],
    indirect=True,
)
def test_ask_user_question_waits_for_answer_and_reports_it_to_the_model(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Ask me for a mode")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests), expected_count=1, timeout=10
        )
        _answer_first_question_option(child, captured)
        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=10,
        )
        wait_for_rendered_text(
            child, captured, needle="The selected mode was Fast.", timeout=10
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert_tool_result_contains(
        streaming_mock_server.requests[1], call_id=QUESTION_CALL_ID, expected="Fast"
    )
