"""With voice mode on, Ctrl+R still searches and says recording moved to Ctrl+T."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.reverse_search._history import sent

timeline: Timeline = [*sent("hello"), "\x12"]
screen_contains = ("Voice recording moved to Ctrl+T", "reverse-i-search:")
