"""Left on an empty prompt opens the Sessions page; Esc goes back to the session unchanged."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_LEFT = "\x1b[D"
_ESCAPE = "\x1b[27u"

timeline: Timeline = [_LEFT, take_snapshot("page"), _ESCAPE, take_snapshot("back")]
request_methods = {"session/list", "projects/list"}
