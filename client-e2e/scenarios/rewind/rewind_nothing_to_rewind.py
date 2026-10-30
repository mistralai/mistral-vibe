"""`/rewind` on an empty transcript says there is no message to rewind to."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
request_methods = {"session/rewind/read", "session/rewind"}
screen_contains = ("⚠ No message to rewind to.",)

timeline: Timeline = ["/rewind\r"]
