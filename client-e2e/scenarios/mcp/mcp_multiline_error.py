"""A multi-line bootstrap error keeps its line breaks in the detail view."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, server
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake([
    server(
        "broken",
        status="unavailable",
        error="spawn failed: command not found\nhint: install the server binary",
    )
])

timeline: Timeline = ["/mcp broken\r"]

screen_contains = {"rust": ("hint: install the server binary",)}
screen_excludes = {"rust": ("foundhint",)}
