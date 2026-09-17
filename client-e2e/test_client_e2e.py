"""Assert terminal parity for every Vibe client scenario."""

from __future__ import annotations

from collections.abc import Iterator
import os

from e2e.app_server.capture import Capture, capture_scenario
from e2e.app_server.config import CLIENTS
from e2e.app_server.parity import captures_match, requests_match
from e2e.app_server.report import report_hyperlink, write_report, write_request_report
from e2e.app_server.scenario import Scenario, available_scenarios, load_scenario
from e2e.app_server.zygote import Zygote, launcher, zygote
import pytest


@pytest.fixture(scope="session")
def python_zygote() -> Iterator[Zygote | None]:
    """Preload the Python client once per worker so captures only pay a fork."""
    with zygote() as running:
        yield running


def _assert_snapshot_expectations(
    client: str, captured: Capture, scenario: Scenario
) -> None:
    if not captured.snapshots:
        return
    snapshot = captured.snapshots[-1]
    screen = "\n".join(snapshot.rows)
    for text in scenario.screen_contains.get(client, ()):
        assert text in screen, f"{client} screen is missing {text!r}"
    for text in scenario.screen_excludes.get(client, ()):
        assert text not in screen, f"{client} screen contains {text!r}"
    for row, text in scenario.screen_rows.get(client, {}).items():
        assert snapshot.rows[row] == text, f"{client} screen row {row} differs"

    clipboard_expected = (
        scenario.expected_clipboard is not None
        or scenario.clipboard_contains
        or scenario.clipboard_excludes
    )
    if not clipboard_expected:
        return
    if (
        scenario.clipboard_clients is not None
        and client not in scenario.clipboard_clients
    ):
        return
    clipboard = snapshot.clipboard
    assert clipboard is not None, f"{client} did not emit an OSC 52 payload"
    if scenario.expected_clipboard is not None:
        assert clipboard == scenario.expected_clipboard
    for text in scenario.clipboard_contains:
        assert text in clipboard, f"{client} clipboard is missing {text!r}"
    for text in scenario.clipboard_excludes:
        assert text not in clipboard, f"{client} clipboard contains {text!r}"


@pytest.mark.parametrize("name", available_scenarios())
def test_client_parity(name: str, python_zygote: Zygote | None) -> None:
    scenario = load_scenario(name)
    if scenario.skip_reason is not None:
        pytest.skip(scenario.skip_reason)
    for command in CLIENTS.values():
        if not os.path.exists(command[0]):
            pytest.skip(f"CLI missing: {command[0]} (run `make build` / `uv sync`)")

    # A scenario that must not inherit the preloaded process execs instead.
    running = python_zygote if scenario.zygote else None
    captures = {
        client: capture_scenario(command, scenario, launcher(running, client))
        for client, command in CLIENTS.items()
    }
    for client, captured in captures.items():
        _assert_snapshot_expectations(client, captured, scenario)
        if (expected := scenario.expected_actions.get(client)) is not None:
            assert captured.actions == expected
    requests = {client: captured.requests for client, captured in captures.items()}
    if not requests_match(requests):
        report = write_request_report(name, requests)
        pytest.fail(
            f"{name}: client-to-server RPC mismatch\nReport: {report_hyperlink(report)}",
            pytrace=False,
        )
    if scenario.skip_terminal_parity_reason is not None:
        pytest.skip(scenario.skip_terminal_parity_reason)
    snapshots = {client: captured.snapshots for client, captured in captures.items()}
    if not captures_match(snapshots):
        report = write_report(name, snapshots)
        pytest.fail(
            f"{name}: terminal parity mismatch\nReport: {report_hyperlink(report)}",
            pytrace=False,
        )
