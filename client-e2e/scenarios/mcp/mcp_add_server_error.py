"""A rejected `/mcp add` shows the server's reason, not just the method name."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

MESSAGE = "MCP server URL is already configured as `linear`."
handshake = {"mcp/add": {"error": {"code": "invalid_params", "message": MESSAGE}}}
request_methods = frozenset({"mcp_catalog/add"})
screen_contains = (f"Error: {MESSAGE}",)
screen_excludes = ("mcp_catalog/add failed", "Added OAuth MCP server")
timeline: Timeline = [
    "/mcp add https://mcp.linear.app/mcp --name lin2 --scope read --no-login\r"
]
