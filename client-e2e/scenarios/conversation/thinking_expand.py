from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    reasoning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = {
    "config/read": {"config": {"showThinkingNodes": True}},
    "runtime/read": {"runtime": {"config": {"showThinkingNodes": True}}},
}

# The collapsed `⏵ Thought` header lands on SGR row 30 once the turn settles.
_ROW, _COL = 30, 1
_CLICK = f"\x1b[<0;{_COL};{_ROW}M\x1b[<0;{_COL};{_ROW}m"

timeline: Timeline = [
    "think about it\r",
    turn_started(),
    user_msg("think about it"),
    reasoning("Weighing the trade-offs before answering."),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK,
]
