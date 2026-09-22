from __future__ import annotations

from e2e.app_server.events import assistant_msg, reasoning, turn_started, user_msg
from e2e.app_server.scenario import Timeline

handshake = {
    "config/read": {"config": {"showThinkingNodes": True}},
    "runtime/read": {"runtime": {"config": {"showThinkingNodes": True}}},
}

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_steps = {1, 2, 3}
settle_per_key = True

# The in-progress reasoning header lands on SGR row 32 after the gated events.
_ROW, _COL = 32, 1
_CLICK = f"\x1b[<0;{_COL};{_ROW}M\x1b[<0;{_COL};{_ROW}m"

timeline: Timeline = [
    "think about it\r",
    turn_started(),
    user_msg("think about it"),
    reasoning(
        "Weighing the trade-offs before answering.", generation_status="in_progress"
    ),
    assistant_msg("Drafting the answer.", generation_status="in_progress"),
    {"release": 4},
    _CLICK,
    {"release": 1},
]
