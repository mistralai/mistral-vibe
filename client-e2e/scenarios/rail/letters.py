"""A letter opens its rail item: Ctrl+X then `t` opens the todo window, and the letters go away."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "t"]

screen_contains = ("Todo 1/3", "▶ Run the parity suite", "▶ goal 4m · ≡ 1/3")
