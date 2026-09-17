"""Assert the preloaded zygote renders exactly what an exec'd client renders."""

from __future__ import annotations

import os

from e2e.app_server.capture import capture_scenario
from e2e.app_server.config import CLIENTS, ZYGOTE_CLIENT, ZYGOTE_ENV_VAR
from e2e.app_server.parity import normalize_requests, snapshots_match
from e2e.app_server.scenario import load_scenario
from e2e.app_server.zygote import zygote
from e2e.pty.capture import exec_launch
import pytest


def test_forked_client_matches_execed_client() -> None:
    command = CLIENTS[ZYGOTE_CLIENT]
    if os.environ.get(ZYGOTE_ENV_VAR) == "0":
        pytest.skip(f"the zygote is disabled by {ZYGOTE_ENV_VAR}=0")
    if not os.path.exists(command[0]):
        pytest.skip(f"CLI missing: {command[0]} (run `uv sync`)")
    scenario = load_scenario("conversation/say_hi")

    with zygote() as running:
        assert running is not None, "the zygote failed to preload the Python CLI"
        forked = capture_scenario(command, scenario, running.launch)
    execed = capture_scenario(command, scenario, exec_launch)

    assert len(forked.snapshots) == len(execed.snapshots)
    assert all(
        snapshots_match(a, b)
        for a, b in zip(forked.snapshots, execed.snapshots, strict=True)
    )
    assert normalize_requests(forked.requests) == normalize_requests(execed.requests)
    assert forked.actions == execed.actions
