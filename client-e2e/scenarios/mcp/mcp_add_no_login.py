"""`/mcp add <url> --no-login` confirms the URL-named server and hints `/mcp login` without logging in."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"mcp/add": {"name": "linear", "url": "https://mcp.linear.app/mcp"}}
request_methods = frozenset({"mcp_catalog/add", "mcp_catalog/login"})
screen_contains = ("Added OAuth MCP server linear.", "/mcp login linear")
screen_excludes = ("Starting OAuth login", "authenticated", "MCP and connectors opened")
timeline: Timeline = ["/mcp add https://mcp.linear.app/mcp --no-login\r"]
