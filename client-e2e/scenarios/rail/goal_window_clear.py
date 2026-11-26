"""`c` in the goal window asks to confirm, then clears the goal; its chip and window go with it."""

from __future__ import annotations

from e2e.app_server.events import goal_updated
from e2e.app_server.scenario import Timeline
from scenarios.rail._fixtures import CTRL_X, SETUP, handshake as rail_handshake

handshake = {**rail_handshake, "goal/clear": {"cleared": True}}
on_request = {"goal/clear": [goal_updated(None)]}

timeline: Timeline = [*SETUP, CTRL_X, "g", "c", "\r"]

screen_excludes = ("Goal active", "Make the parity suite pass")
