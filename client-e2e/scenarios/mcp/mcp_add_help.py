"""`/mcp add --help` prints the add usage without contacting the server."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/mcp add --help\r"]
