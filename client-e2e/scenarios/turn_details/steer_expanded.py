"""Ctrl+O unfolds both groups of a steered turn, each rail closing before the steer or the answer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.turn_details import steer_outside

handshake = steer_outside.handshake

timeline: Timeline = [*steer_outside.timeline[:-1], "\x0f", take_snapshot()]

screen_rows = {
    10: "⏷ Worked for 10s · 1 message · 1 tool call",
    12: "│   Let me look at the test first.",
    15: "└ └ 1 failed",
    16: "",
    17: "",
    18: "↳ also check the docs",
    19: "",
    20: "⏷ Worked for 1m 2s · 1 tool call",
    23: "└ └ # Auth",
    24: "",
    25: "",
    26: "  Fixed, and the docs match.",
}
