"""`/branch` during a turn opens the resume command bottom app at once, and it stays open after the turn ends."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_delta,
    assistant_msg,
    complete_entry,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline
from scenarios.slash_command._branch_fixtures import REQUEST_METHODS, fork_handshake

handshake = {
    **fork_handshake("unified"),
    "workspace/prompt/prepare": {"prompt": {"promptText": None}},
}
request_methods = REQUEST_METHODS

_REPLY = "reply-1"

# The first release is the three events below plus the queue update the harness adds after `turn/started`.
timeline: Timeline = [
    "long task\r",
    turn_started(),
    user_msg("long task"),
    assistant_msg("Working on it.", entry_id=_REPLY, generation_status="in_progress"),
    assistant_delta(_REPLY, " Still going."),
    complete_entry(_REPLY),
    turn_completed(),
    {"release": 4},
    "/branch\r",
    {"release": 1},
    {"release": 2},
]
screen_contains = (
    "Working on it. Still going.",
    "Session forked",
    "vibe --resume 22222222-0000-4000-8000-000000000002",
)
screen_excludes = ("Slash commands cannot be queued", "Branched to a new session.")
