"""Enter on a server that needs OAuth hands over to the auth bottom-app."""

from __future__ import annotations

from typing import Any

from e2e.app_server.mcp import (
    SAMPLE_DISCOVERY_ERRORS,
    handshake as mcp_handshake,
    sample_sources,
    server,
    tool,
)
from e2e.app_server.scenario import Timeline


def _authenticated() -> list[dict[str, Any]]:
    """The catalog the server publishes once `weather` finished its login."""
    return [
        server("weather", transport="http", tools=[tool("forecast", "Read a forecast")])
        if source["name"] == "weather"
        else source
        for source in sample_sources()
    ]


handshake = mcp_handshake(sample_sources(), discovery_errors=SAMPLE_DISCOVERY_ERRORS)
# The login answers with the authenticated catalog, which every later read and
# refresh then serves, so the reopened browser shows the discovered tools.
handshake["mcp/login"] = {"runtime": {"mcp": {"sources": _authenticated()}}}

_DOWN = "\x1b[B"

timeline: Timeline = ["/mcp\r", _DOWN + _DOWN, "\r"]
