from __future__ import annotations

import os
from pathlib import Path

import pexpect
import pytest

from tests.e2e.agent_loop_characterization.support import (
    answer_approval,
    assert_tool_result_contains,
    assistant_text_chunks,
    set_tool_denylist,
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

WRITE_APPROVED_CALL_ID = "call_write_approved"
WRITE_REJECTED_CALL_ID = "call_write_rejected"
APPROVED_FILE = "approved-write.txt"
REJECTED_FILE = "rejected-write.txt"
SESSION_PERMISSION_FIRST_CALL_ID = "call_bash_session_1"
SESSION_PERMISSION_SECOND_CALL_ID = "call_bash_session_2"
SESSION_PERMISSION_OUTPUT = "__E2E_SESSION_PERMISSION__"


def _write_file_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        return single_tool_call_chunks(
            call_id=WRITE_APPROVED_CALL_ID,
            tool_name="write_file",
            arguments={"path": APPROVED_FILE, "content": "approved content\n"},
            created=120,
        )
    if request_index == 1:
        return assistant_text_chunks("Approved file was written.", created=130)
    if request_index == 2:
        return single_tool_call_chunks(
            call_id=WRITE_REJECTED_CALL_ID,
            tool_name="write_file",
            arguments={"path": REJECTED_FILE, "content": "rejected content\n"},
            created=140,
        )

    return assistant_text_chunks("Rejected file was not written.", created=150)


def _session_permission_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    arguments = {"command": f'printf "{SESSION_PERMISSION_OUTPUT}\\n"'}
    if request_index == 0:
        return single_tool_call_chunks(
            call_id=SESSION_PERMISSION_FIRST_CALL_ID,
            tool_name="bash",
            arguments=arguments,
            created=180,
        )
    if request_index == 1:
        return assistant_text_chunks("First command completed.", created=190)
    if request_index == 2:
        return single_tool_call_chunks(
            call_id=SESSION_PERMISSION_SECOND_CALL_ID,
            tool_name="bash",
            arguments=arguments,
            created=200,
        )

    return assistant_text_chunks(
        "Second command reused session permission.", created=210
    )


@pytest.mark.timeout(35)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_write_file_factory, id="write-file-approval-and-rejection")],
    indirect=True,
)
def test_write_file_approval_creates_file_and_rejection_leaves_file_absent(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    set_tool_denylist(
        Path(os.environ["VIBE_HOME"]),
        "write_file",
        [str((e2e_workdir / REJECTED_FILE).resolve())],
    )
    approved_path = e2e_workdir / APPROVED_FILE
    rejected_path = e2e_workdir / REJECTED_FILE

    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Create the approved file")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests), expected_count=1, timeout=10
        )
        answer_approval(child, captured, tool_name="write_file", key="y")
        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=10,
        )
        wait_for_rendered_text(
            child, captured, needle="Approved file was written.", timeout=10
        )

        child.send("Try the rejected file")
        child.send("\r")
        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=4,
            timeout=10,
        )
        wait_for_rendered_text(
            child, captured, needle="Rejected file was not written.", timeout=10
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert approved_path.read_text(encoding="utf-8") == "approved content\n"
    assert not rejected_path.exists()
    assert_tool_result_contains(
        streaming_mock_server.requests[1],
        call_id=WRITE_APPROVED_CALL_ID,
        expected="approved content",
    )
    assert_tool_result_contains(
        streaming_mock_server.requests[3],
        call_id=WRITE_REJECTED_CALL_ID,
        expected="permanently disabled",
    )


@pytest.mark.timeout(40)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_session_permission_factory, id="session-permission-memory")],
    indirect=True,
)
def test_allow_for_session_reuses_bash_permission_without_prompting_again(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Run the first shell command")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests), expected_count=1, timeout=10
        )
        answer_approval(child, captured, tool_name="bash", key="2")
        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=10,
        )
        wait_for_rendered_text(
            child, captured, needle="First command completed.", timeout=10
        )

        child.send("Run the same shell command again")
        child.send("\r")
        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=4,
            timeout=10,
        )
        wait_for_rendered_text(
            child,
            captured,
            needle="Second command reused session permission.",
            timeout=10,
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert_tool_result_contains(
        streaming_mock_server.requests[1],
        call_id=SESSION_PERMISSION_FIRST_CALL_ID,
        expected=SESSION_PERMISSION_OUTPUT,
    )
    assert_tool_result_contains(
        streaming_mock_server.requests[3],
        call_id=SESSION_PERMISSION_SECOND_CALL_ID,
        expected=SESSION_PERMISSION_OUTPUT,
    )
