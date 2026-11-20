"""At 80 columns the rail shrinks its leftmost chips, before dropping any, so the loading line keeps its whole hint."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize
from scenarios.rail._fixtures import SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, resize(40, 80)]

screen_contains = ("Esc/Ctrl+C to interrupt)", "◐ 1 · ▪ 2 · ▶ goal 4m · ≡ 1/3")
screen_excludes = ("1 agent",)
