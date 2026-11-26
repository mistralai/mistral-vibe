"""A narrow terminal keeps every source and tool row on one line, dropping columns first."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline, resize, take_snapshot

handshake = mcp_handshake([
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector(
        "analytics",
        display_name="Analytics Data Context With A Long Name",
        tools=[tool("query", "Run a query")],
    ),
    connector(
        "github_app",
        tools=[tool("add_comment_to_pending_review_of_the_latest_pull_request", "x")],
    ),
])

timeline: Timeline = [
    resize(40, 50),
    "/mcp\r",
    take_snapshot("list"),
    "\x1b",
    "/mcp github_app\r",
    take_snapshot("tools"),
]
