"""`/model`'s `/` focuses fuzzy search; the filtered list keeps only the matching model."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = ["/model\r", "/", "devstral", "\x1b[B", take_snapshot()]
screen_contains = ("/  devstral", "devstral-small")
screen_excludes = ("Default",)
