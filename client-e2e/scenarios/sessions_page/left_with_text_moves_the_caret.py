"""Left inside a draft moves the caret and never opens the Sessions page."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_LEFT = "\x1b[D"

timeline: Timeline = ["draft", _LEFT, take_snapshot()]
screen_excludes = ("Search sessions",)
