"""Exercise MCP argv dispatch through the Rust binary with a replay app server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from e2e.app_server.config import CLIENTS, REPLAY_BIN
import pytest
from scenarios._mcp_argv import SCENARIOS, MCPArgvScenario


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_mcp_argv_is_sessionless(scenario: MCPArgvScenario, tmp_path: Path) -> None:
    if not Path(CLIENTS["rust"][0]).is_file() or not REPLAY_BIN.is_file():
        pytest.skip("Rust binaries missing; run make build_test")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps({
            "handshake": {"initialize": {}, **scenario.responses},
            "events": [],
        })
    )
    requests = tmp_path / "requests.jsonl"
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("VIBE_")
    }
    env.update({
        "VIBE_HOME": str(tmp_path / "home"),
        "VIBE_REPLAY_BIN": str(REPLAY_BIN),
        "VIBE_REPLAY_FIXTURE": str(fixture),
        "VIBE_REPLAY_REQUEST_LOG": str(requests),
    })
    with subprocess.Popen(
        (*CLIENTS["rust"], "mcp", *scenario.args),
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            # Leave stdin open: MCP commands must not wait for a piped prompt.
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            raise
        stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == scenario.returncode, stderr
    assert stdout == scenario.stdout
    if scenario.stderr_contains:
        assert scenario.stderr_contains in stderr
    else:
        assert stderr == ""
    assert "\x1b" not in stdout + stderr
    sent = [json.loads(line) for line in requests.read_text().splitlines()]
    assert [frame["method"] for frame in sent[:2]] == ["initialize", "initialized"]
    assert [
        {"method": frame["method"], "params": frame["params"]} for frame in sent[2:]
    ] == list(scenario.requests)


@pytest.mark.parametrize("args", [("--help",), ("add", "--help"), ("remove", "--help")])
def test_mcp_help_needs_no_app_server(args: tuple[str, ...], tmp_path: Path) -> None:
    result = subprocess.run(
        (*CLIENTS["rust"], "mcp", *args),
        cwd=tmp_path,
        env={**os.environ, "VIBE_APP_SERVER_CMD": "missing-app-server"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0
    assert "Usage: vibe mcp" in result.stdout
    assert "vibe-rs" not in result.stdout
    assert result.stderr == ""
