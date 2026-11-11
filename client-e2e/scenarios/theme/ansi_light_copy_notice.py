"""Under ANSI-light, the auth URL copy confirmation is an inline notice above the input."""

from __future__ import annotations

from typing import Any

from e2e.app_server.mcp import connector, handshake as mcp_handshake, server, tool
from e2e.app_server.scenario import Timeline, take_snapshot

_URL = "https://auth.example.invalid/connectors/gmail?state=e2e"


def _sources(gmail: dict[str, Any]) -> list[dict[str, Any]]:
    return [server("filesystem", tools=[tool("read_file", "Read a file")]), gmail]


handshake = mcp_handshake(_sources(connector("gmail", status="needs_auth")))
handshake["connectors/auth/read"] = {"url": _URL}
handshake["config/read"] = {"config": {"theme": "ansi-light"}}
handshake["runtime/read"]["runtime"]["config"] = {"theme": "ansi-light"}
env = {"VIBE_THEME": "ansi-light", "SSH_TTY": "/dev/pts/0"}
_DOWN = "\x1b[B"
timeline: Timeline = [
    "/mcp\r",
    take_snapshot(),
    _DOWN,
    take_snapshot(),
    "\r",
    take_snapshot(),
    _DOWN,
    take_snapshot(),
    "\r",
    take_snapshot(),
]
