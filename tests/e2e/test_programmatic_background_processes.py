"""The `enable_background_processes` switch, seen through a `vibe -p` process
driven against the mock model server, on the host and with its tools in a
sandbox behind `--agent-socket`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.e2e.agent_loop_characterization.support import (
    assistant_text_chunks,
    single_tool_call_chunks,
)
from tests.e2e.common import (
    VIBE_EXECUTABLE,
    read_export_config,
    run_vibe_headless,
    write_e2e_config,
)
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer
from tests.stubs.fake_agent_server import (
    FakeAgentServer,
    private_socket_path,
    serving_in_background,
)

pytestmark = [pytest.mark.timeout(90), pytest.mark.usefixtures("setup_e2e_env")]

_PROCESS_START = (
    "async function main() {"
    " return tools.process.start({command: 'echo started-in-background'}); }"
)


def _start_a_process_then_answer(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index > 0:
        return assistant_text_chunks("All done.")
    return single_tool_call_chunks(
        call_id="call_process",
        tool_name="run_typescript",
        arguments={"code": _PROCESS_START},
    )


def _tool_results(payload: ChatCompletionsRequestPayload) -> list[str]:
    return [
        str(message.get("content", ""))
        for message in payload.get("messages", [])
        if message.get("role") == "tool"
    ]


@pytest.mark.parametrize(
    "streaming_mock_server", [_start_a_process_then_answer], indirect=True
)
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("sandboxed", [False, True], ids=["host", "agent-socket"])
def test_background_process_surface_follows_the_switch(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    sandboxed: bool,
) -> None:
    """*Prepare*: `enable_background_processes` off or on, and a model that
    starts a background process through `run_typescript`.
    *Do*: Run `vibe -p --auto-approve`, on the host or with `--agent-socket`
    against an agent server that serves a sandbox.
    *Assert*: Off, the first request carries no process surface, the start fails
    as a tool error and the run still finishes. On, the surface is there and the
    process starts, in the sandbox for a socket run. The export shows the
    setting either way.
    """
    # Prepare
    write_e2e_config(
        Path(os.environ["VIBE_HOME"]),
        streaming_mock_server.api_base,
        settings=[f"enable_background_processes = {str(enabled).lower()}"],
    )
    output_dir = tmp_path / "out"
    args = ["-p", "hello", "--auto-approve", "--output-dir", str(output_dir)]
    # The sandbox's skills copies and process server state stay in the test's
    # own directories.
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    # Do
    server = None
    if not sandboxed:
        result = run_vibe_headless(e2e_workdir, args)
    else:
        # A socket run takes its workspace from the server, not `--workdir`.
        host_dir = tmp_path / "host"
        host_dir.mkdir()
        with private_socket_path() as socket_path:
            server = FakeAgentServer(socket_path, e2e_workdir)
            with serving_in_background(server):
                result = subprocess.run(
                    [VIBE_EXECUTABLE, *args, "--agent-socket", str(socket_path)],
                    cwd=host_dir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=os.environ.copy(),
                    check=False,
                )

    # Assert
    assert result.returncode == 0, result.stderr
    first_request = json.dumps(streaming_mock_server.requests[0])
    assert ("tools.process" in first_request) is enabled
    assert ("## Background processes" in first_request) is enabled
    # The system prompt's list of searchable tool groups; newlines are
    # escaped because the request was dumped to JSON.
    assert ("\\n- process\\n" in first_request) is enabled
    [process_result] = _tool_results(streaming_mock_server.requests[1])
    expected = '"processId"' if enabled else "run_typescript failed"
    assert expected in process_result, process_result
    assert read_export_config(output_dir)["enable_background_processes"] is enabled
    if server is not None:
        assert server.sandbox is not None
        assert ("process" in server.sandbox.helper_operations) is enabled
