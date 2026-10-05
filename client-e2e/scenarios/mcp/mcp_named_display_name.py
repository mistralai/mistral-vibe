"""`/mcp <display name>` opens the connector the browser shows under that name."""

from __future__ import annotations

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

handshake = mcp_handshake([
    server("filesystem", tools=[tool("read_file", "Read a file")]),
    connector("gm_9c1b", display_name="Gmail", tools=[tool("send_email")]),
])

timeline: Timeline = ["/mcp gmail\r"]

screen_contains = {"rust": ("Connector: Gmail", "send_email")}
