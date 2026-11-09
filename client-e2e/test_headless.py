"""Assert headless output against deterministic app-server replay."""

from __future__ import annotations

from e2e.app_server.capture import capture_headless_scenario
from e2e.app_server.config import CLIENT_BIN
from e2e.app_server.scenario import available_headless_scenarios, load_scenario
import pytest


@pytest.mark.parametrize("name", available_headless_scenarios())
def test_headless_scenario(name: str) -> None:
    scenario = load_scenario(name)
    if not CLIENT_BIN.exists():
        pytest.skip(f"CLI missing: {CLIENT_BIN} (run `make build_test`)")

    captured = capture_headless_scenario(scenario)

    assert captured.returncode == scenario.headless_returncode, captured.stderr
    assert captured.stdout_before_completion == list(scenario.stdout_before_completion)
    assert captured.stdout == list(scenario.stdout_before_completion)
    if scenario.headless_stderr is not None:
        assert captured.stderr == scenario.headless_stderr
    sent = {request.method for request in captured.requests}
    assert set(scenario.headless_requests) <= sent
