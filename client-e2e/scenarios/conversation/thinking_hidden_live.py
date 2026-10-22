"""Turning `show_thinking_nodes` off mid-session hides reasoning without a restart."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    reasoning,
    runtime_updated,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

handshake = {
    "config/read": {"config": {"showThinkingNodes": True}},
    "runtime/read": {"runtime": {"config": {"showThinkingNodes": True}}},
}

# The prompt step leaves the turn running, so settle on the busy frame.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_steps = {1, 2}

# The update carries the fixture runtime, where `showThinkingNodes` is false.
timeline: Timeline = [
    "think about it\r",
    turn_started(),
    user_msg("think about it"),
    reasoning("Weighing the trade-offs before answering."),
    assistant_msg("Done."),
    turn_completed(),
    runtime_updated(),
    {"release": 6},
    {"release": 1},
]

screen_contains = ("Done.",)
screen_excludes = ("Thought",)
