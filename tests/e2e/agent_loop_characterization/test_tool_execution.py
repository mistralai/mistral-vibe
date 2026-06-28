from __future__ import annotations

import os
from pathlib import Path

import pexpect
import pytest

from tests.e2e.agent_loop_characterization.support import (
    answer_approval,
    assert_assistant_tool_call_present,
    assert_tool_result_contains,
    assistant_text_chunks,
    multi_tool_call_chunks,
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

DENIED_BASH_CALL_ID = "call_bash_denied"
DENIED_BASH_FILE = "denied-side-effect.txt"
FAILING_BASH_CALL_ID = "call_bash_fails"
FAILING_BASH_STDERR = "__E2E_BASH_FAILURE__"
MULTI_TOOL_FIRST_CALL_ID = "call_todo_multi_1"
MULTI_TOOL_SECOND_CALL_ID = "call_todo_multi_2"


def _denied_bash_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        return single_tool_call_chunks(
            call_id=DENIED_BASH_CALL_ID,
            tool_name="bash",
            arguments={"command": f"touch {DENIED_BASH_FILE}"},
        )

    return assistant_text_chunks("Denied without creating the file.", created=20)


def _failing_bash_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        return single_tool_call_chunks(
            call_id=FAILING_BASH_CALL_ID,
            tool_name="bash",
            arguments={"command": f"printf {FAILING_BASH_STDERR} >&2; false"},
        )

    return assistant_text_chunks("Recovered after the shell failure.", created=30)


def _multi_tool_turn_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        return multi_tool_call_chunks(
            [
                (MULTI_TOOL_FIRST_CALL_ID, "todo", {"action": "read"}),
                (MULTI_TOOL_SECOND_CALL_ID, "todo", {"action": "read"}),
            ],
            created=160,
        )

    return assistant_text_chunks("Both todo reads completed.", created=170)


@pytest.mark.timeout(25)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_denied_bash_factory, id="denied-bash-tool")],
    indirect=True,
)
def test_denylisted_bash_tool_does_not_run_and_is_reported_to_the_model(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    set_tool_denylist(Path(os.environ["VIBE_HOME"]), "bash", ["touch"])
    denied_path = e2e_workdir / DENIED_BASH_FILE

    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Try a denied shell command")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests), expected_count=1, timeout=10
        )
        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=10,
        )
        wait_for_rendered_text(
            child, captured, needle="Denied without creating the file.", timeout=10
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert not denied_path.exists()
    assert_tool_result_contains(
        streaming_mock_server.requests[1],
        call_id=DENIED_BASH_CALL_ID,
        expected="Command denied:",
    )


@pytest.mark.timeout(40)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_failing_bash_factory, id="failing-bash-tool")],
    indirect=True,
)
def test_failed_bash_tool_result_is_reported_to_the_model_and_turn_recovers(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Run a failing shell command")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests), expected_count=1, timeout=10
        )
        answer_approval(child, captured, tool_name="bash", key="y")
        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=10,
        )
        wait_for_rendered_text(
            child, captured, needle="Recovered after the shell failure.", timeout=20
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert_tool_result_contains(
        streaming_mock_server.requests[1],
        call_id=FAILING_BASH_CALL_ID,
        expected="Return code: 1",
    )
    assert_tool_result_contains(
        streaming_mock_server.requests[1],
        call_id=FAILING_BASH_CALL_ID,
        expected=FAILING_BASH_STDERR,
    )


@pytest.mark.timeout(25)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_multi_tool_turn_factory, id="multi-tool-turn")],
    indirect=True,
)
def test_multiple_tool_calls_in_one_assistant_turn_return_distinct_results(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Run two todo reads")
        child.send("\r")

        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=10,
        )
        wait_for_rendered_text(
            child, captured, needle="Both todo reads completed.", timeout=10
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert_assistant_tool_call_present(
        streaming_mock_server.requests[1],
        call_id=MULTI_TOOL_FIRST_CALL_ID,
        tool_name="todo",
    )
    assert_assistant_tool_call_present(
        streaming_mock_server.requests[1],
        call_id=MULTI_TOOL_SECOND_CALL_ID,
        tool_name="todo",
    )
    assert_tool_result_contains(
        streaming_mock_server.requests[1],
        call_id=MULTI_TOOL_FIRST_CALL_ID,
        expected="Retrieved 0 todos",
    )
    assert_tool_result_contains(
        streaming_mock_server.requests[1],
        call_id=MULTI_TOOL_SECOND_CALL_ID,
        expected="Retrieved 0 todos",
    )
