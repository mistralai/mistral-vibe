"""Esc applies a session-only level in-process and reports it; reopening shows it effective."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

capture_startup = False
request_methods = {"config/write"}

# From WARNING, Down to ERROR, set the session badge, apply, then reopen.
timeline: Timeline = ["/log-level", "\r", "\x1b[B", "\r", _ESCAPE, "/log-level\r"]

screen_contains = (
    "session override → ERROR",
    "Effective: ERROR  (session override: ERROR)",
)
