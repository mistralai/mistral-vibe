"""`/mcp add` without a URL reports the usage as a plain error message."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/mcp add\r"]
