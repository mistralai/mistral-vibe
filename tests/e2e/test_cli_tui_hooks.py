from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib

import pexpect
import pytest
import tomli_w

from tests.e2e.common import (
    SpawnedVibeProcessFixture,
    poll_until,
    send_ctrl_c_until_quit_confirmation,
    wait_for_main_screen,
    wait_for_rendered_text,
    wait_for_request_count,
)
from tests.e2e.mock_server import StreamingMockServer


def _enable_hooks(vibe_home: Path, invocation_path: Path) -> None:
    config_path = vibe_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config["enable_experimental_hooks"] = True
    config_path.write_bytes(tomli_w.dumps(config).encode())

    script = vibe_home / "_record_hook.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"Path({str(invocation_path)!r}).write_text("
        "json.dumps(json.load(sys.stdin)), encoding='utf-8')\n",
        encoding="utf-8",
    )
    with (vibe_home / "hooks.toml").open("wb") as f:
        tomli_w.dump(
            {
                "hooks": [
                    {
                        "name": "record-invocation",
                        "type": "post_agent_turn",
                        "command": f"uv run python {script}",
                    }
                ]
            },
            f,
        )


@pytest.mark.timeout(20)
def test_spawn_cli_runs_configured_hook_after_turn(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    vibe_home = Path(os.environ["VIBE_HOME"])
    invocation_path = vibe_home / "hook-invocation.json"
    _enable_hooks(vibe_home, invocation_path)

    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Run the configured hook")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests), expected_count=1, timeout=10
        )
        wait_for_rendered_text(
            child, captured, needle="Hello from mock server", timeout=10
        )
        poll_until(
            invocation_path.is_file,
            timeout=10,
            message=f"Timed out waiting for hook output file: {invocation_path}",
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert len(streaming_mock_server.requests) == 1
    invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
    assert invocation["hook_event_name"] == "post_agent_turn"
    assert isinstance(invocation["cwd"], str) and invocation["cwd"]
    assert isinstance(invocation["session_id"], str) and invocation["session_id"]
