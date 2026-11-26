"""A click on the group after a steer unfolds only that group, the one before it stays folded."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.turn_details import steer_outside

handshake = steer_outside.handshake

# The second `Worked for` header lands on SGR row 15.
_CLICK = "\x1b[<0;5;15M\x1b[<0;5;15m"

timeline: Timeline = [*steer_outside.timeline[:-1], _CLICK]

screen_rows = {
    10: "⏵ Worked for 10s · 1 message · 1 tool call",
    12: "↳ also check the docs",
    14: "⏷ Worked for 1m 2s · 1 tool call",
    16: "└ ⏵ Ran cat docs/auth.md",
    17: "",
    18: "",
    19: "  Fixed, and the docs match.",
}
