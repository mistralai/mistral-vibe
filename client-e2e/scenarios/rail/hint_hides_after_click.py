"""A chip click counts as a rail use: the `Ctrl+X to navigate` hint goes and the chips stay put."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import SETUP, handshake

__all__ = ["handshake"]

_CLICK_TODO = "\x1b[<0;116;34M\x1b[<0;116;34m"

timeline: Timeline = [*SETUP, _CLICK_TODO, _CLICK_TODO, "typed"]

screen_contains = ("> typed", "◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3")
screen_excludes = ("to navigate", "Todo 1/3")
