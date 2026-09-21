"""Enter on a connector that needs auth, reveal its URL, then refresh with `r`."""

from __future__ import annotations

from typing import Any

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline

_URL = "https://auth.example.invalid/connectors/gmail?state=e2e"


def _sources(gmail: dict[str, Any]) -> list[dict[str, Any]]:
    return [server("filesystem", tools=[tool("read_file", "Read a file")]), gmail]


_PENDING = _sources(connector("gmail", status="needs_auth"))
_AUTHENTICATED = _sources(
    connector("gmail", tools=[tool("send_email", "Send an email")])
)

handshake = mcp_handshake(_PENDING)
# The auth app reads the URL, then `r` re-discovers the connector; that refresh
# publishes the authenticated catalog every later read then serves.
handshake["connectors/auth/read"] = {"url": _URL}
handshake["connectors/refresh"] = {
    "toolCount": 1,
    "runtime": {"mcp": {"sources": _AUTHENTICATED}},
}

_DOWN = "\x1b[B"

# Open the browser, walk to the connector, open its auth app, reveal the URL,
# then refresh once the sign-in is done. Enter on the first option is left out:
# the Python client hands that one straight to `webbrowser`.
timeline: Timeline = ["/mcp\r", _DOWN, "\r", _DOWN + _DOWN, "\r", "r"]
