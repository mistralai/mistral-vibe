"""Voice settings remain modal and wrap within narrow terminals."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

screen_contains = {
    "rust": ("Voice Settings", "Voice mode: On", "Narrator (experimental): Off")
}
timeline: Timeline = ["/voice\r", resize(20, 35), resize(7, 19), resize(40, 120)]
