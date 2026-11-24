"""A click highlights a saved session and a second click opens it, like Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# Press and release on the saved session row (row 6 of the sessions column, under the title, search and filters).
_CLICK_FIRST = "\x1b[<0;40;6M\x1b[<0;40;6m"

timeline: Timeline = ["/resume", "\r", _CLICK_FIRST, _CLICK_FIRST, take_snapshot()]
request_methods = {"session/resume"}
