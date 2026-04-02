from __future__ import annotations

from collections.abc import Callable
import io
from pathlib import Path
import re
import time

import pexpect
import pytest

from tests.e2e.common import (
    SpawnedVibeProcessFixture,
    ansi_tolerant_pattern,
    strip_ansi,
    wait_for_main_screen,
    wait_for_request_count,
)
from tests.e2e.mock_server import StreamingMockServer


def _usage_by_run_factory(
    request_index: int, _payload: object
) -> list[dict[str, object]]:
    return [
        StreamingMockServer.build_chunk(
            created=123,
            delta={"role": "assistant", "content": f"Reply {request_index + 1}"},
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=124,
            delta={},
            finish_reason="stop",
            usage=(
                {"prompt_tokens": 11, "completion_tokens": 7}
                if request_index == 0
                else {"prompt_tokens": 2, "completion_tokens": 1}
            ),
        ),
    ]


def _finish_turn(
    child: pexpect.spawn,
    captured: io.StringIO,
    expected_reply: str,
    expected_request_count: int,
    request_count_getter: Callable[[], int],
) -> None:
    wait_for_request_count(
        request_count_getter, expected_count=expected_request_count, timeout=10
    )
    child.expect(ansi_tolerant_pattern(expected_reply), timeout=10)

    start = time.monotonic()
    last_change = start
    last_size = len(captured.getvalue())

    while time.monotonic() - start < 5:
        try:
            child.expect(r"\S", timeout=0.05)
        except pexpect.TIMEOUT:
            pass

        current_size = len(captured.getvalue())
        if current_size != last_size:
            last_size = current_size
            last_change = time.monotonic()
            continue

        if time.monotonic() - last_change >= 0.3:
            return

    rendered_tail = strip_ansi(captured.getvalue())[-1200:]
    raise AssertionError(
        f"Timed out waiting for the turn to finish.\n\nRendered tail:\n{rendered_tail}"
    )


@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_usage_by_run_factory, id="fresh-usage-after-resume")],
    indirect=True,
)
def test_resumed_session_prints_only_fresh_token_usage_on_exit(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("First run")
        child.send("\r")

        _finish_turn(
            child,
            captured,
            expected_reply="Reply 1",
            expected_request_count=1,
            request_count_getter=lambda: len(streaming_mock_server.requests),
        )

        child.sendcontrol("c")
        child.expect(pexpect.EOF, timeout=10)

    first_output = strip_ansi(captured.getvalue())
    resume_match = re.search(r"Or: vibe --resume ([0-9a-f-]+)", first_output)
    assert resume_match is not None
    session_id = resume_match.group(1)
    assert (
        "Total tokens used this session: input=11 output=7 (total=18)" in first_output
    )

    with spawned_vibe_process(e2e_workdir, extra_args=["--resume", session_id]) as (
        resumed_child,
        resumed_captured,
    ):
        wait_for_main_screen(resumed_child, timeout=15)
        resumed_child.send("Second run")
        resumed_child.send("\r")

        _finish_turn(
            resumed_child,
            resumed_captured,
            expected_reply="Reply 2",
            expected_request_count=2,
            request_count_getter=lambda: len(streaming_mock_server.requests),
        )

        resumed_child.sendcontrol("c")
        resumed_child.expect(pexpect.EOF, timeout=10)

        second_output = strip_ansi(resumed_captured.getvalue())

    assert "Total tokens used this session: input=2 output=1 (total=3)" in second_output
