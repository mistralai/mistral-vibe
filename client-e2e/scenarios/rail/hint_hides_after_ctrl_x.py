"""Once Ctrl+X has reached the rail, its `Ctrl+X to navigate` hint stays hidden back in the prompt."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, ESCAPE, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, ESCAPE, "typed"]

screen_contains = ("> typed", "◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3")
screen_excludes = ("to navigate",)
