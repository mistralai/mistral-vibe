"""Connector titles, plugin owners and the Studio link render; plugin servers refuse toggles."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_SOURCES = [
    server(
        "filesystem", tools=[tool("read_file", "Read a file")], plugin_name="devtools"
    ),
    server("weather", transport="http", tools=[tool("forecast", "Read a forecast")]),
    connector("gh_4f2a", display_name="GitHub", tools=[tool("create_issue")]),
    connector("gm_9c1b", display_name="Gmail", status="disabled"),
]

handshake = mcp_handshake(
    _SOURCES, manage_connectors_url="https://console.mistral.ai/connectors"
)

_DOWN = "\x1b[B"

timeline: Timeline = ["/mcp\r", "d", "/gmail", _DOWN, _DOWN, "\r"]

screen_contains = {"rust": ("Connector: Gmail",)}
screen_excludes = {"rust": ("gm_9c1b",)}
