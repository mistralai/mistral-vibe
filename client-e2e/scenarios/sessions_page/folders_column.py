"""Left focuses the folders column: arrows pick a folder, its sessions list, and right comes back."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

handshake = listed(
    session(1, "Current work"),
    session(2, "Docs refresh", "/projects/website"),
    session(3, "Website plan", "/projects/website"),
    session(4, "Failed run", "/projects/tools", status={"type": "failed"}),
    session(5, "Unread answer", "/projects/tools", isUnseen=True),
)
screen_contains = ("→/Enter open", "← folders", "Unread answer")
screen_excludes = ("Docs refresh",)

_LEFT = "\x1b[D"
_DOWN = "\x1b[B"
_RIGHT = "\x1b[C"

timeline: Timeline = [
    "/sessions",
    "\r",
    _LEFT,
    take_snapshot("folders_focused"),
    _DOWN,
    _DOWN,
    _DOWN,
    take_snapshot("tools_folder"),
    _RIGHT,
    "j",
]
