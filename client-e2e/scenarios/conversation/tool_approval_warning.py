"""A server warning renders once over the tool approval app."""

from __future__ import annotations

from e2e.app_server.events import (
    server_warning,
    tool_approval,
    tool_approval_added,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_INPUT_GRACE_PERIOD_MS": "0",
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
}
handshake = {"callback/result": {"accepted": True}}
capture_startup = False

skip_terminal_parity = (
    "python's Textual notify toast mounts async; rust draws it inline"
)

_CHILD_SESSION_ID = "00000000-0000-4000-8000-000000000002"
_PERMISSIONS = [
    {
        "scope": "command_pattern",
        "invocationPattern": "printf 'approved\\n'",
        "sessionPattern": "printf *",
        "label": "commands matching printf wildcard",
    }
]

timeline: Timeline = [
    "run the command\r",
    turn_started(),
    user_msg("run the command"),
    tool_approval_added(
        "bash",
        "shell",
        {"command": "printf 'approved\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-approval-warning",
        session_id=_CHILD_SESSION_ID,
    ),
    tool_approval(
        "bash",
        "shell",
        {"command": "printf 'approved\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-approval-warning",
        request_id=9001,
        session_id=_CHILD_SESSION_ID,
    ),
    server_warning("approval warning"),
]

screen_contains = {"rust": ("approval warning",)}
