"""A paused goal reads `‖ goal paused` on the rail; its window offers `p resume`, sitting right on the rail."""

from __future__ import annotations

from e2e.app_server.events import goal
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with
from scenarios.rail._fixtures import CTRL_X, SETUP

handshake = started_with(goal("paused", status_reason="user"))

timeline: Timeline = [*SETUP, CTRL_X, "g", take_snapshot()]

screen_contains = ("‖ goal paused · ≡ 1/3", "p resume  e edit  c clear  Esc close")
