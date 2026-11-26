"""The /debug console renders live log entries, closes on Ctrl+backslash, and reopens."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Log timestamps render in local time; pin UTC so the golden is timezone-free.
env = {"TZ": "UTC"}
screen_contains = ("Debug Console", "session started")

# The replay server answers diagnostics/logs/read from the shared fixture.
timeline: Timeline = ["/debug", "\r", "\x1c", "/debug", "\r"]
