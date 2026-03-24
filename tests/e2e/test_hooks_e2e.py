"""E2E tests: verify lifecycle hooks fire during real Vibe sessions."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pexpect
import pytest

from tests import TESTS_ROOT
from tests.e2e.common import (
    SpawnedVibeProcessFixture,
    wait_for_main_screen,
    wait_for_request_count,
    write_e2e_config,
)
from tests.e2e.mock_server import StreamingMockServer


def _create_hook_helper(tmp_path: Path) -> Path:
    """Create a helper script that appends hook stdin JSON to an output file."""
    helper = tmp_path / "hook_helper.py"
    helper.write_text(
        'import sys, os\n'
        'out = os.environ.get("HOOK_OUTPUT_FILE", "/dev/null")\n'
        'with open(out, "a") as f:\n'
        '    f.write(sys.stdin.read() + "\\n")\n',
        encoding="utf-8",
    )
    return helper


def _hooks_toml(helper: Path, output_file: Path) -> str:
    """Generate TOML config for hooks that write payloads to output_file."""
    cmd = f'python3 {helper}'
    lines = []
    for event in ["session_start", "user_prompt_submit", "pre_tool_use", "post_tool_use", "turn_end"]:
        lines.append(f"[[hooks.{event}]]")
        lines.append(f'command = "{cmd}"')
        lines.append("")
    return "\n".join(lines)


def _read_payloads(output_file: Path) -> list[dict]:
    """Read newline-delimited JSON payloads from the output file."""
    if not output_file.exists():
        return []
    payloads = []
    for line in output_file.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if line:
            payloads.append(json.loads(line))
    return payloads


@pytest.mark.timeout(20)
def test_programmatic_mode_lifecycle_hooks(
    streaming_mock_server: StreamingMockServer,
    tmp_path: Path,
) -> None:
    """Spawn vibe -p with hooks and verify the full lifecycle fires."""
    vibe_home = tmp_path / "vibe-home"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    output_file = tmp_path / "hook_payloads.jsonl"
    helper = _create_hook_helper(tmp_path)

    write_e2e_config(
        vibe_home,
        streaming_mock_server.api_base,
        extra_toml=_hooks_toml(helper, output_file),
    )

    env = dict(os.environ)
    env["MISTRAL_API_KEY"] = "fake-key"
    env["VIBE_HOME"] = str(vibe_home)
    env["HOOK_OUTPUT_FILE"] = str(output_file)
    env["TERM"] = "xterm-256color"

    child = pexpect.spawn(
        "uv",
        ["run", "vibe", "-p", "Say hello", "--workdir", str(workdir)],
        cwd=str(TESTS_ROOT.parent),
        env=env,
        encoding="utf-8",
        timeout=15,
    )
    child.expect(pexpect.EOF, timeout=15)
    child.close()

    payloads = _read_payloads(output_file)
    event_names = [p["hook_event_name"] for p in payloads]

    # Verify all expected hooks fired
    assert "session_start" in event_names
    assert "user_prompt_submit" in event_names
    assert "turn_end" in event_names

    # Verify ordering
    assert event_names.index("session_start") < event_names.index("user_prompt_submit")
    assert event_names.index("user_prompt_submit") < event_names.index("turn_end")

    # Verify payload fields
    for p in payloads:
        assert "hook_event_name" in p
        assert "cwd" in p
        assert "session_id" in p

    # Verify user_prompt_submit has the prompt
    prompt_payloads = [p for p in payloads if p["hook_event_name"] == "user_prompt_submit"]
    assert len(prompt_payloads) == 1
    assert prompt_payloads[0]["prompt"] == "Say hello"

    # All payloads share the same session_id
    session_ids = {p["session_id"] for p in payloads}
    assert len(session_ids) == 1
