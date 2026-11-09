"""A query with no match keeps the draft and says so; Esc restores the draft."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.reverse_search._history import VOICE_OFF, sent

handshake = VOICE_OFF

timeline: Timeline = [*sent("git status"), "my draft", "\x12", "zzz", "\x1b"]
capture_steps = {2, 3, 4}
screen_contains = ("my draft",)
screen_excludes = ("reverse-i-search",)
