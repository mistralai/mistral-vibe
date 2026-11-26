"""A second click on the expanded `Worked for` header folds the steps back."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.turn_details._turns import HANDSHAKE, worked_turn

handshake = HANDSHAKE

_CLICK = "\x1b[<0;5;11M\x1b[<0;5;11m"

timeline: Timeline = [*worked_turn(), _CLICK, _CLICK]

screen_rows = {10: "⏵ Worked for 1m 12s · 2 messages · 3 tool calls"}
screen_excludes = ("Let me look at the test first.",)
