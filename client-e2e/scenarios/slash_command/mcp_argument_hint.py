"""`/mcp ` keeps its subcommands visible as an inline hint once the menu closes."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
screen_contains = ("mcp [name] | add <url> | status | login <alias> | logout <alias>",)
screen_excludes = ("Display available MCP servers",)
timeline: Timeline = ["/mcp", " "]
