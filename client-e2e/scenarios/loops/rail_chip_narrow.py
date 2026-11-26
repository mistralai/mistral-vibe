"""On a narrow terminal the loops chip, leftmost on the rail, shrinks first and drops before any other chip."""

from __future__ import annotations

import copy

from e2e.app_server.events import scheduled_loop
from e2e.app_server.scenario import Timeline, resize
from scenarios.rail._fixtures import SETUP, handshake as _rail_handshake

handshake = copy.deepcopy(_rail_handshake)
handshake["session/start"]["state"]["session"]["loops"] = [
    scheduled_loop("soon", "PR watch #42\nx", in_seconds=290, interval_seconds=1_200)
]

timeline: Timeline = [*SETUP, resize(40, 74)]

screen_contains = ("Esc/Ctrl+C to interrupt)", "◐ 1 · ▪ 2 · ▶ 4m · ≡ 1/3")
screen_excludes = ("◷",)
