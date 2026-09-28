"""Remote-project busy parity."""

from __future__ import annotations

from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

request_methods = METHODS
from e2e.app_server.events import assistant_msg, turn_started, user_msg

handshake = project_handshake()
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_startup = False
capture_steps = {2}
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/remote-project",
    "\r",
]
