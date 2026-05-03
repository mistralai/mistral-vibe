from __future__ import annotations

from unittest.mock import patch

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from tests.stubs.fake_mcp_registry import (
    FakeMCPRegistry,
    FakeMCPRegistryWithBrokenServer,
)
from vibe.core.config import MCPHttp, MCPStdio
from vibe.core.tools.connectors import CONNECTORS_ENV_VAR
from vibe.core.tools.mcp.tools import RemoteTool

_MCP_PATCH = "vibe.core.agent_loop.MCPRegistry"

_FAKE_CONNECTORS = {
    "gmail": [
        RemoteTool(name="gmail_search", description="Search emails in Gmail"),
        RemoteTool(name="gmail_draft_create", description="Draft an email in Gmail"),
        RemoteTool(name="gmail_open_message", description="Open a mail on Gmail"),
    ],
    "slack": [
        RemoteTool(name="search_messages", description="Search Slack messages"),
        RemoteTool(name="send_message", description="Send a Slack message"),
    ],
}

_FAKE_CONNECTORS_MIXED_CONNECTION = {
    "zeta": [],
    "alpha": [RemoteTool(name="lookup", description="Lookup Alpha records")],
    "beta": [],
}


class SnapshotTestAppNoMcpServers(BaseSnapshotTestApp):
    def __init__(self) -> None:
        super().__init__(config=default_config())


class SnapshotTestAppWithBrokenMcpServer(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        config.mcp_servers = [
            MCPStdio(name="filesystem", transport="stdio", command="npx"),
            MCPStdio(
                name="broken-server", transport="stdio", command="nonexistent-cmd"
            ),
            MCPHttp(name="search", transport="http", url="http://localhost:8080"),
        ]
        super().__init__(config=config)


class SnapshotTestAppWithMcpServers(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        config.mcp_servers = [
            MCPStdio(name="filesystem", transport="stdio", command="npx"),
            MCPHttp(name="search", transport="http", url="http://localhost:8080"),
        ]
        super().__init__(config=config)


async def _run_mcp_command(pilot: Pilot, command: str) -> None:
    await pilot.pause(0.1)
    await pilot.press(*command)
    await pilot.press("enter")
    await pilot.pause(0.1)
    pilot.app.set_focus(None)
    await pilot.pause(0.1)


def test_snapshot_mcp_no_servers(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")

    assert snap_compare(
        "test_ui_snapshot_mcp_command.py:SnapshotTestAppNoMcpServers",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_mcp_broken_server(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")

    with patch(_MCP_PATCH, FakeMCPRegistryWithBrokenServer):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithBrokenMcpServer",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_mcp_overview(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithMcpServers",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_mcp_overview_navigate_down(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        await pilot.press("down")
        await pilot.pause(0.1)

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithMcpServers",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_mcp_enter_drills_into_server(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("down")
        await pilot.pause(0.1)
        await pilot.press("enter")

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithMcpServers",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_mcp_server_arg(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp filesystem")
        await pilot.pause(0.1)

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithMcpServers",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_mcp_backspace_returns_to_overview(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp filesystem")
        await pilot.press("backspace")
        await pilot.pause(0.1)

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithMcpServers",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_mcp_escape_closes(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        await pilot.press("escape")
        await pilot.pause(0.2)

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithMcpServers",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_mcp_refresh_shortcut(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        await pilot.press("r")
        await pilot.pause(0.2)

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithMcpServers",
            terminal_size=(120, 36),
            run_before=run_before,
        )


# ---------------------------------------------------------------------------
# Apps with connectors
# ---------------------------------------------------------------------------


class SnapshotTestAppWithConnectors(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        config.mcp_servers = [
            MCPStdio(name="filesystem", transport="stdio", command="npx")
        ]
        super().__init__(config=config)
        registry = FakeConnectorRegistry(connectors=_FAKE_CONNECTORS)
        self.agent_loop.connector_registry = registry
        self.agent_loop.tool_manager._connector_registry = registry
        self.agent_loop.tool_manager.integrate_connectors()


class SnapshotTestAppConnectorsOnly(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        config.mcp_servers = []
        super().__init__(config=config)
        registry = FakeConnectorRegistry(connectors=_FAKE_CONNECTORS)
        self.agent_loop.connector_registry = registry
        self.agent_loop.tool_manager._connector_registry = registry
        self.agent_loop.tool_manager.integrate_connectors()


class SnapshotTestAppConnectorsMixedState(BaseSnapshotTestApp):
    def __init__(self) -> None:
        config = default_config()
        config.mcp_servers = []
        super().__init__(config=config)
        registry = FakeConnectorRegistry(connectors=_FAKE_CONNECTORS_MIXED_CONNECTION)
        self.agent_loop.connector_registry = registry
        self.agent_loop.tool_manager._connector_registry = registry
        self.agent_loop.tool_manager.integrate_connectors()


# ---------------------------------------------------------------------------
# Connector snapshot tests
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_mcp_with_connectors_overview(snap_compare: SnapCompare) -> None:

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithConnectors",
            terminal_size=(120, 36),
            run_before=run_before,
        )


# ---------------------------------------------------------------------------
# Connector auth app snapshot tests
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_connector_auth_opens_on_disconnected(
    snap_compare: SnapCompare,
) -> None:
    """Clicking a disconnected connector opens the auth app."""

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        # In mixed state: alpha (connected) is first, then beta, zeta (disconnected).
        # Navigate to a disconnected connector and press enter.
        await pilot.press("down")  # beta
        await pilot.pause(0.1)
        await pilot.press("enter")  # opens auth app
        await pilot.pause(0.5)  # wait for auth URL fetch worker

    assert snap_compare(
        "test_ui_snapshot_mcp_command.py:SnapshotTestAppConnectorsMixedState",
        terminal_size=(120, 36),
        run_before=run_before,
    )


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_connector_auth_show_url(snap_compare: SnapCompare) -> None:
    """Selecting 'Manually show the URL' reveals the auth URL."""

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        await pilot.press("down")  # beta (disconnected)
        await pilot.pause(0.1)
        await pilot.press("enter")  # opens auth app
        await pilot.pause(0.5)  # wait for auth URL fetch
        # Navigate to "Manually show the URL" (3rd selectable option)
        await pilot.press("down", "down")
        await pilot.pause(0.1)
        await pilot.press("enter")  # toggle URL display
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_mcp_command.py:SnapshotTestAppConnectorsMixedState",
        terminal_size=(120, 36),
        run_before=run_before,
    )


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_connector_auth_back_to_mcp(snap_compare: SnapCompare) -> None:
    """Pressing backspace in the auth app returns to the /mcp menu."""

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        await pilot.press("down")  # beta (disconnected)
        await pilot.pause(0.1)
        await pilot.press("enter")  # opens auth app
        await pilot.pause(0.5)  # wait for auth URL fetch
        await pilot.press("backspace")  # back to /mcp
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_mcp_command.py:SnapshotTestAppConnectorsMixedState",
        terminal_size=(120, 36),
        run_before=run_before,
    )


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_mcp_help_bar_shows_authenticate(snap_compare: SnapCompare) -> None:
    """Help bar shows 'Enter Authenticate' when a disconnected connector is highlighted."""

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        # Navigate to a disconnected connector (beta or zeta)
        await pilot.press("down")  # beta (disconnected)
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_mcp_command.py:SnapshotTestAppConnectorsMixedState",
        terminal_size=(120, 36),
        run_before=run_before,
    )


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_mcp_connectors_only(snap_compare: SnapCompare) -> None:

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")

    assert snap_compare(
        "test_ui_snapshot_mcp_command.py:SnapshotTestAppConnectorsOnly",
        terminal_size=(120, 36),
        run_before=run_before,
    )


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_mcp_connectors_sorted_by_status(snap_compare: SnapCompare) -> None:

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")

    assert snap_compare(
        "test_ui_snapshot_mcp_command.py:SnapshotTestAppConnectorsMixedState",
        terminal_size=(120, 36),
        run_before=run_before,
    )


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_mcp_drill_into_connector(snap_compare: SnapCompare) -> None:

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        # Navigate past the MCP server to the first connector
        await pilot.press("down")  # skip filesystem server
        await pilot.pause(0.1)
        await pilot.press("down")  # first connector
        await pilot.pause(0.1)
        await pilot.press("enter")  # drill in
        await pilot.pause(0.1)

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithConnectors",
            terminal_size=(120, 36),
            run_before=run_before,
        )


@patch.dict("os.environ", {CONNECTORS_ENV_VAR: "1"})
def test_snapshot_mcp_connector_back_to_overview(snap_compare: SnapCompare) -> None:

    async def run_before(pilot: Pilot) -> None:
        await _run_mcp_command(pilot, "/mcp")
        await pilot.press("down", "down")
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("backspace")
        await pilot.pause(0.1)

    with patch(_MCP_PATCH, FakeMCPRegistry):
        assert snap_compare(
            "test_ui_snapshot_mcp_command.py:SnapshotTestAppWithConnectors",
            terminal_size=(120, 36),
            run_before=run_before,
        )
