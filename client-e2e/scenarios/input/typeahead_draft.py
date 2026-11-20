"""Text typed before the TUI starts stays a draft, line breaks kept, without a trailing Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance

typeahead = "fix the\rtests"

screen_contains = ("> fix the", "tests")
screen_excludes = ("fix the tests",)

timeline: Timeline = [advance(100)]
