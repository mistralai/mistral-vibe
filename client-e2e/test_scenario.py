"""Test declarative scenario discovery and loading."""

from __future__ import annotations

import json
from pathlib import Path

from e2e.app_server.replay import replay_fixture
from e2e.app_server.scenario import load_scenario


def test_scenario_path_is_its_identity() -> None:
    scenario = load_scenario("conversation/say_hi")

    assert scenario.name == "conversation/say_hi"


def test_scenario_loads_terminal_responses() -> None:
    scenario = load_scenario("theme/auto_light")

    assert scenario.terminal_responses == {
        "\x1b]11;?\x07": "\x1b]11;rgb:ffff/ffff/ffff\x07"
    }


def test_scenario_loads_exit_contract() -> None:
    scenario = load_scenario("lifecycle/graceful_exit")

    assert scenario.exit_after_last_step
    assert scenario.request_methods == {"session/stop"}


def test_replay_fixture_serves_mock_server_version() -> None:
    scenario = load_scenario("conversation/say_hi")

    with replay_fixture(scenario) as path:
        fixture = json.loads(Path(path).read_text())

    version = fixture["handshake"]["initialize"]["serverInfo"]["version"]

    assert version == "0.0.0-eval"
