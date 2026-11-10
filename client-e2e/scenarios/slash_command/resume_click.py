"""A click previews a saved session and a second click resumes it, like Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# Press and release on the first session row, in 1-based terminal coordinates.
_CLICK_FIRST = "\x1b[<0;10;35M\x1b[<0;10;35m"

timeline: Timeline = ["/resume", "\r", _CLICK_FIRST, _CLICK_FIRST, take_snapshot()]
