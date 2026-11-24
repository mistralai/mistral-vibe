"""A rail used six days ago in an earlier session keeps its `Ctrl+X to navigate` hint hidden."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import SETUP, handshake, used_home

__all__ = ["handshake"]

env = {"VIBE_HOME": used_home(days_ago=6)}
timeline: Timeline = [*SETUP]

screen_contains = ("◐ 1 agent · ▪ 2 tasks · ▶ goal 4m · ≡ 1/3",)
screen_excludes = ("to navigate",)
