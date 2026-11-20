"""`p` in the goal window pauses the goal; the window then offers `p resume`."""

from __future__ import annotations

from e2e.app_server.events import goal, goal_updated
from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake as rail_handshake

_PAUSED = goal("paused", status_reason="user")

handshake = {**rail_handshake, "goal/set": {"goal": _PAUSED}}
on_request = {"goal/set": [goal_updated(_PAUSED)]}

timeline: Timeline = [*SETUP, CTRL_X, "g", "p"]

screen_contains = ("‖ goal paused", "p resume  e edit  c clear  Esc close")
