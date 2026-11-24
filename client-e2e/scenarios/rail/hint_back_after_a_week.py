"""A rail last used a week ago shows its `Ctrl+X to navigate` hint again."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import SETUP, handshake, used_home

__all__ = ["handshake"]

env = {"VIBE_HOME": used_home(days_ago=7)}
timeline: Timeline = [*SETUP]

screen_contains = ("Ctrl+X to navigate · ◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3",)
