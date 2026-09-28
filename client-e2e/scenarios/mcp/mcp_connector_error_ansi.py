"""A connector error carrying ANSI escapes and CR redraws renders sanitized with the Error prefix."""

from __future__ import annotations

from e2e.app_server.mcp import handshake as mcp_handshake, sample_sources
from e2e.app_server.scenario import Timeline

# The server-fed error paints a red word, erases the line, and redraws a
# green word after a carriage return, so only the last write survives
# sanitization and the ANSI bytes never reach the terminal.
_RAW_CONNECTOR_ERROR = (
    "\x1b[31mconnector failed\x1b[0m\x1b[2K\r\x1b[32mconnectors unavailable\x1b[0m"
)

handshake = mcp_handshake(sample_sources(), connector_error=_RAW_CONNECTOR_ERROR)

screen_contains = {
    # The redrawed word must render sanitized, closing the error block's gutter.
    "rust": ("⎣ connectors unavailable",)
}


timeline: Timeline = ["/mcp\r"]
