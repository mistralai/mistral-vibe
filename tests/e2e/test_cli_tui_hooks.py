from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pexpect
import pytest
import tomli_w

from tests.e2e.common import (
    SpawnedVibeProcessFixture,
    poll_until,
    send_ctrl_c_until_quit_confirmation,
    wait_for_main_screen,
    wait_for_rendered_text,
    wait_for_request_count_while_draining_child_output,
)
from tests.e2e.mock_server import StreamingMockServer


def _setup_recording_hooks(
    vibe_home: Path, invocation_paths: dict[str, Path], *, match: bool = False
) -> None:
    script = vibe_home / "_record_hook.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_text("
        "json.dumps(json.load(sys.stdin)), encoding='utf-8')\n",
        encoding="utf-8",
    )
    hooks = [
        {
            "name": f"record-{name}",
            "type": "post_agent",
            "command": f"{sys.executable} {script} {path}",
            **({"match": name} if match else {}),
        }
        for name, path in invocation_paths.items()
    ]
    with (vibe_home / "hooks.toml").open("wb") as f:
        tomli_w.dump({"hooks": hooks}, f)


def _setup_recording_hook(vibe_home: Path, invocation_path: Path) -> None:
    _setup_recording_hooks(vibe_home, {"invocation": invocation_path})


@pytest.mark.unified_default
@pytest.mark.timeout(20)
def test_spawn_cli_runs_configured_hook_after_turn(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    vibe_home = Path(os.environ["VIBE_HOME"])
    invocation_path = vibe_home / "hook-invocation.json"
    _setup_recording_hook(vibe_home, invocation_path)

    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Run the configured hook")
        child.send("\r")

        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=1,
            timeout=10,
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
    assert invocation["hook_event_name"] == "post_agent"
    assert isinstance(invocation["cwd"], str) and invocation["cwd"]
    assert isinstance(invocation["session_id"], str) and invocation["session_id"]
    # New in the discriminated-union payload: top-level sessions have no
    # parent. Tool hook invocations in this test never fire (no tool calls
    # in the mock response), so we only assert the post_agent shape.
    assert invocation.get("parent_session_id") is None


@pytest.mark.unified_default
@pytest.mark.timeout(20)
def test_spawn_cli_runs_post_agent_hooks_scoped_to_the_main_agent(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    # Started without --agent, the session runs the default agent (accept-edits): the
    # hooks scoped to `main` and to that agent run, the one scoped to a subagent does not.
    vibe_home = Path(os.environ["VIBE_HOME"])
    # Hooks run in declaration order: `reviewer` goes first so that, were it wrongly
    # selected, its file would exist by the time the other two have run.
    invocation_paths = {
        name: vibe_home / f"hook-{name}.json"
        for name in ("reviewer", "main", "accept-edits")
    }
    _setup_recording_hooks(vibe_home, invocation_paths, match=True)

    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Run the scoped hooks")
        child.send("\r")

        wait_for_rendered_text(
            child, captured, needle="Hello from mock server", timeout=10
        )
        poll_until(
            lambda: (
                invocation_paths["main"].is_file()
                and invocation_paths["accept-edits"].is_file()
            ),
            timeout=10,
            message="Timed out waiting for the scoped hooks to run",
        )

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    assert not invocation_paths["reviewer"].exists()
    for name in ("main", "accept-edits"):
        invocation = json.loads(invocation_paths[name].read_text(encoding="utf-8"))
        assert invocation["hook_event_name"] == "post_agent"
        assert invocation["agent_name"] == "accept-edits"
