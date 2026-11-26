"""On a truecolor theme the focused rail item takes the hovered button tint, like the model name and the context gauge."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake as _rail_handshake

env = {"VIBE_THEME": "mistral-dark"}
handshake = {
    **_rail_handshake,
    "config/read": {"config": {"theme": "mistral-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "mistral-dark"}}},
}

timeline: Timeline = [*SETUP, CTRL_X, take_snapshot()]

screen_contains = ("s 1 agent · b 2 tasks · g goal 4m · t 1/3",)
