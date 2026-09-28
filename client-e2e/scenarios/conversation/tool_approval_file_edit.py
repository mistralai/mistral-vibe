"""Render file-edit approvals as a contextual diff."""

from __future__ import annotations

from e2e.app_server.events import (
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "VIBE_TYPING_GRACE_PERIOD_MS": "0"}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False
capture_steps = {1}

_INPUT = {
    "filePath": "client-e2e/fixtures/tool_approval_context.fixture",
    "changes": [
        {"oldString": "beta", "newString": "BETA", "replaceAll": True},
        {"oldString": "gamma", "newString": "GAMMA", "replaceAll": False},
    ],
}

timeline: Timeline = [
    "edit the file\r",
    turn_started(),
    user_msg("edit the file"),
    tool_approval_added("edit", "file_edit", _INPUT),
    tool_approval("edit", "file_edit", _INPUT),
    {"release": 5},
]

settle_per_key = True
