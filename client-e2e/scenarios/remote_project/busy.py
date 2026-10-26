"""The remote-project picker opens during a running turn."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.remote_project import METHODS, handshake as project_handshake
from e2e.app_server.scenario import Timeline

request_methods = METHODS
handshake = project_handshake()
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_startup = False
screen_contains = ("Vibe Code project", "Generating")
screen_excludes = ("Slash commands cannot be queued", "Loading Vibe Code projects")
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/remote-project",
    "\r",
]
