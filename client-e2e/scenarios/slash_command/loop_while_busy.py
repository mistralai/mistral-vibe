"""Every loop form joins the ordinary prompt queue while a turn is running."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
handshake = {"workspace/prompt/prepare": {"prompt": {"promptText": None}}}
request_methods = {
    "workspace/prompt/prepare",
    "loops/create",
    "loops/list",
    "loops/delete",
    "loops/clear",
    "telemetry/record",
}
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/loop\r",
    "/loop list\r",
    "/loop cancel\r",
    "/loop 90s check build status\r",
]
screen_contains = {
    "rust": (
        "Queued",
        "/loop",
        "/loop list",
        "/loop cancel",
        "/loop 90s check build status",
    )
}
screen_excludes = {
    "rust": (
        "Slash commands cannot be queued",
        "No scheduled loops.",
        "not implemented",
    )
}
