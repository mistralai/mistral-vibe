"""A filter with no match shows a note; Esc leaves the search field, then clears the filter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/model\r", "/", "zzz", take_snapshot(), _ESCAPE, _ESCAPE]
screen_contains = ("Search models", "Default", "mistral-medium-3.5")
screen_excludes = ("zzz", "No matching models")
