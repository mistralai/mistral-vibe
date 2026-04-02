from __future__ import annotations

from pathlib import Path

import pexpect
import pytest

from tests.e2e.common import (
    SpawnedVibeProcessFixture,
    ansi_tolerant_pattern,
    wait_for_main_screen,
    wait_for_request_count,
)
from tests.e2e.mock_server import StreamingMockServer


@pytest.mark.timeout(15)
def test_spawn_cli_to_send_and_receive_message(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Greet")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests), expected_count=1, timeout=10
        )
        child.expect(ansi_tolerant_pattern("Hello from mock server"), timeout=10)

        child.sendcontrol("c")
        child.expect(pexpect.EOF, timeout=10)

    output = captured.getvalue()
    assert "Welcome to Mistral Vibe" not in output

    request_payload = streaming_mock_server.requests[-1]
    assert request_payload.get("stream") is True
    assert request_payload.get("model") == "mock-model"
    stream_options = request_payload.get("stream_options")
    assert stream_options is not None
    assert stream_options.get("include_usage") is True
    messages = request_payload.get("messages")
    assert messages is not None
    assert any(
        message.get("role") == "user" and message.get("content") == "Greet"
        for message in messages
    )
