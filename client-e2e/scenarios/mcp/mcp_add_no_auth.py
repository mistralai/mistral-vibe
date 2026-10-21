"""`/mcp add` reports success when the server does not request OAuth."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "mcp/add": {"name": "linear", "url": "https://mcp.linear.app/mcp"},
    "mcp/login": {"authenticated": False},
}
request_methods = frozenset({"mcp_catalog/add", "mcp_catalog/login"})
screen_contains = (
    "Added MCP server linear.",
    "MCP server linear does not require OAuth authentication.",
)
screen_excludes = (
    "OAuth login completed.",
    "OAuth login failed",
    "MCP and connectors opened",
)
timeline: Timeline = ["/mcp add https://mcp.linear.app/mcp\r"]
