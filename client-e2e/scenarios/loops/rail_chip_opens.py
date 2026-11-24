"""Ctrl+X then `o` opens the loop manager with its chip lit, Ctrl+X again letters the rail over it, and Shift+Tab wraps to the todo window."""

from __future__ import annotations

import copy

from e2e.app_server.events import scheduled_loop
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake as _rail_handshake

handshake = copy.deepcopy(_rail_handshake)
handshake["session/start"]["state"]["session"]["loops"] = [
    scheduled_loop(
        "soon",
        "PR watch #42\nCheck the PR's CI and reviews.",
        in_seconds=290,
        interval_seconds=1_200,
    )
]

timeline: Timeline = [
    *SETUP,
    CTRL_X,
    "o",
    take_snapshot(),
    CTRL_X,
    take_snapshot(),
    "\x1b[Z",
]

screen_contains = ("Todo 1/3", "◷ loop in 5m · ◐ 1 agent")
screen_excludes = ("Check the PR's CI",)
