"""A click on the `Worked for` header shows the turn's steps behind a rail, a blank row before the answer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details._turns import HANDSHAKE, worked_turn

handshake = HANDSHAKE

# The `Worked for` header lands on SGR row 11.
_CLICK = "\x1b[<0;5;11M\x1b[<0;5;11m"

timeline: Timeline = [*worked_turn(), _CLICK, take_snapshot()]

screen_rows = {
    10: "⏷ Worked for 1m 12s · 2 messages · 3 tool calls",
    12: "│   Let me look at the test first.",
    14: "│ ⏵ Ran 2 commands",
    18: "└ ⏵ Ran pytest test_auth.py",
    19: "",
    20: "",
    21: "  Fixed: the token now refreshes before the retry.",
}
