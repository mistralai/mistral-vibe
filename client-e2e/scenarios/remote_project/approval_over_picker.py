"""Ctrl+C rejects a tool approval shown over a remote-project picker opened during a turn."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

request_methods = METHODS
handshake = {**project_handshake(), "callback/result": {"accepted": True}}
env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
}
settle_per_key = True
capture_startup = False
screen_contains = ("Vibe Code project",)
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    tool_approval_added("bash", "shell", {"command": "echo hello"}),
    tool_approval("bash", "shell", {"command": "echo hello"}),
    {"release": 4},
    "/remote-project",
    "\r",
    {"release": 2},
    "\x03",
]
