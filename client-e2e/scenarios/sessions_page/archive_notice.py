"""`a` archives the highlighted session and says how to find it again; `p` and `d` do nothing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.sessions_page._sessions import listed, session

handshake = listed(session(1, "Current work"), session(2, "Docs refresh"))
request_methods = {"session/archive"}

# `p` and `d` once pinned and deleted: no request goes out for either.
timeline: Timeline = ["/sessions", "\r", "\x1b[B", "p", "d", "d", "a"]

screen_contains = ("Archived Docs refresh. Press . to see archived sessions.",)
screen_excludes = ("Pinned", "Press d again")
