"""`/mcp add <url>` logs in to the added server by default and reports the result."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"mcp/add": {"name": "linear", "url": "https://mcp.linear.app/mcp"}}
request_methods = frozenset({"mcp_catalog/add", "mcp_catalog/login"})
screen_contains = (
    "Added MCP server linear.",
    "Checking authentication requirements...",
    "MCP server linear authenticated.",
)
screen_excludes = ("MCP and connectors opened",)
timeline: Timeline = ["/mcp add https://mcp.linear.app/mcp\r"]
