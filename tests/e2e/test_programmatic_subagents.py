"""The `enable_subagents` switch, seen through a `vibe -p` process driven
against the mock model server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.e2e.agent_loop_characterization.support import (
    assistant_text_chunks,
    single_tool_call_chunks,
)
from tests.e2e.common import read_export_config, run_vibe_headless, write_e2e_config
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

pytestmark = [pytest.mark.timeout(90), pytest.mark.usefixtures("setup_e2e_env")]


def _search_for_subagents_then_answer(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index > 0:
        return assistant_text_chunks("All done.")
    return single_tool_call_chunks(
        call_id="call_search",
        tool_name="search_tool_functions",
        arguments={"mode": "best_match", "query": "subagent spawn"},
    )


def _tool_results(payload: ChatCompletionsRequestPayload) -> list[str]:
    return [
        str(message.get("content", ""))
        for message in payload.get("messages", [])
        if message.get("role") == "tool"
    ]


@pytest.mark.parametrize(
    "streaming_mock_server", [_search_for_subagents_then_answer], indirect=True
)
@pytest.mark.parametrize("enabled", [False, True])
def test_subagent_surface_follows_the_switch(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    tmp_path: Path,
    enabled: bool,
) -> None:
    """*Prepare*: `enable_subagents` off or on, and a model that searches the
    tool functions for subagents.
    *Do*: Run `vibe -p --auto-approve`.
    *Assert*: Off, the system prompt has no Subagents section and the search
    finds no subagent function; on, both are there. The export shows the
    setting either way.
    """
    # Prepare
    write_e2e_config(
        Path(os.environ["VIBE_HOME"]),
        streaming_mock_server.api_base,
        settings=[f"enable_subagents = {str(enabled).lower()}"],
    )
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir, ["-p", "hello", "--auto-approve", "--output-dir", str(output_dir)]
    )

    # Assert
    assert result.returncode == 0, result.stderr
    first_request = json.dumps(streaming_mock_server.requests[0])
    assert ("## Subagents" in first_request) is enabled
    assert ("tools.subagent" in first_request) is enabled
    [search_result] = _tool_results(streaming_mock_server.requests[1])
    expected = "subagent.spawn" if enabled else "No matching tool functions found"
    assert expected in search_result, search_result
    assert ("subagent." in search_result) is enabled, search_result
    assert read_export_config(output_dir)["enable_subagents"] is enabled
