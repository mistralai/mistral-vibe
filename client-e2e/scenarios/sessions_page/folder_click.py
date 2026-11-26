"""A click on a folder lists its sessions; a click on a session hands the keys back to the list."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.sessions_page._sessions import listed, session

handshake = listed(
    session(1, "Current work"),
    session(2, "Docs refresh", "/projects/website"),
    session(3, "Website plan", "/projects/website"),
    session(4, "Failed run", "/projects/tools"),
)
screen_contains = ("Docs refresh", "Website plan", "← folders")
screen_excludes = ("Failed run", "Current work  ")


def _click(column: int, row: int) -> str:
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


# The folders are All, then workspace, website, tools on rows 6 to 9.
timeline: Timeline = ["/sessions", "\r", _click(8, 8), _click(40, 7)]
