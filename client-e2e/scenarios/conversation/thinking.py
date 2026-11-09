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

timeline: Timeline = [
    "think about it\r",
    turn_started(),
    user_msg("think about it"),
    reasoning("Weighing the trade-offs before answering."),
    assistant_msg("Done."),
    turn_completed(),
]
