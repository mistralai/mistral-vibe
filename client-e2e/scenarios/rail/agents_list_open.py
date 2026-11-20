"""Enter on the agents item opens the subagent list in the input box's place with Main highlighted."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake

__all__ = ["handshake"]

timeline: Timeline = [*SETUP, CTRL_X, "\r", take_snapshot()]

screen_contains = ("Main conversation", "Explore (Explore 1) [running] · 2.5k tokens")
