"""`/mcp` warns a shared_only API key and blocks connectors that need a login."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_SOURCES = [
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector("gh_4f2a", display_name="GitHub", tools=[tool("create_issue")]),
    connector("gm_9c1b", display_name="Gmail", status="needs_auth"),
]

handshake = mcp_handshake(
    _SOURCES,
    manage_connectors_url="https://console.mistral.ai/connectors",
    private_connectors_hidden=True,
)

_DOWN = "\x1b[B"

# Open the browser, walk past the Studio link and GitHub to the greyed Gmail row,
# then Enter shows the explanation instead of the connector auth app.
timeline: Timeline = ["/mcp\r", _DOWN + _DOWN + _DOWN, "\r"]

screen_contains = ("Connector: Gmail", "vibe --setup")
screen_excludes = ("This connector requires authentication",)
