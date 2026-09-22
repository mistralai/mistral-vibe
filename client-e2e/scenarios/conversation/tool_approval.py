"""Show and navigate the tool approval bottom app."""

from __future__ import annotations

from e2e.app_server.events import (
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
capture_steps = {1, 2, 3, 4, 5}

_DOWN = "\x1b[B"
_CHILD_SESSION_ID = "00000000-0000-4000-8000-000000000002"
_PERMISSIONS = [
    {
        "scope": "command_pattern",
        "invocationPattern": "printf 'approved\\n'",
        "sessionPattern": "printf *",
        "label": "commands matching `printf *`",
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
        callback_id="callback-approve-once",
        session_id=_CHILD_SESSION_ID,
    ),
    tool_approval(
        "bash",
        "shell",
        {"command": "printf 'approved\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-approve-once",
        request_id=9001,
        session_id=_CHILD_SESSION_ID,
    ),
    tool_approval_added(
        "bash",
        "shell",
        {"command": "printf 'session\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-approve-session",
        session_id=_CHILD_SESSION_ID,
    ),
    tool_approval(
        "bash",
        "shell",
        {"command": "printf 'session\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-approve-session",
        request_id=9002,
        session_id=_CHILD_SESSION_ID,
    ),
    tool_approval_added(
        "bash",
        "shell",
        {"command": "printf 'permanent\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-approve-permanently",
        session_id=_CHILD_SESSION_ID,
    ),
    tool_approval(
        "bash",
        "shell",
        {"command": "printf 'permanent\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-approve-permanently",
        request_id=9003,
        session_id=_CHILD_SESSION_ID,
    ),
    tool_approval_added(
        "bash",
        "shell",
        {"command": "printf 'denied\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-deny",
        session_id=_CHILD_SESSION_ID,
    ),
    tool_approval(
        "bash",
        "shell",
        {"command": "printf 'denied\\n'"},
        required_permissions=_PERMISSIONS,
        callback_id="callback-deny",
        request_id=9004,
        session_id=_CHILD_SESSION_ID,
    ),
    {"release": 11},
    "\r",
    _DOWN,
    "\r",
    "3",
    "4",
]

settle_per_key = True
