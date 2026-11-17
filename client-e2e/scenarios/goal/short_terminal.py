"""A long goal's detail, edit and clear panels clip to a short terminal instead of crashing it."""

from __future__ import annotations

from e2e.app_server.events import goal
from e2e.app_server.scenario import Timeline, resize, take_snapshot
from scenarios.goal._fixtures import ESCAPE, started_with

_OBJECTIVE = "Make the parity suite pass on every platform. " * 12

handshake = started_with(goal(objective=_OBJECTIVE))

timeline: Timeline = [
    resize(10, 80),
    "/goal\r",
    "e",
    ESCAPE,
    "c",
    resize(8, 80),
    take_snapshot(),
]

screen_contains = ("Clear goal", "↑↓/jk navigate")
