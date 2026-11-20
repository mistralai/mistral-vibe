"""Turning `collapse_turn_details` off mid-session shows every turn's steps again, as without the setting."""

from __future__ import annotations

from e2e.app_server.events import runtime_updated
from e2e.app_server.scenario import Timeline
from scenarios.turn_details._turns import HANDSHAKE, worked_turn

handshake = HANDSHAKE

# The update carries the fixture runtime, where `collapseTurnDetails` is absent: false.
timeline: Timeline = [*worked_turn(), runtime_updated()]

screen_contains = ("Let me look at the test first.", "Fixed: the token now")
screen_excludes = ("Worked for",)
