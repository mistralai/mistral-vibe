"""Scheduling, listing, and cancellation are verbatim prompts, never loop RPCs."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

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
    "/loop 90s check build status\r",
    turn_started(),
    user_msg("/loop 90s check build status"),
    assistant_msg("I will schedule the build check."),
    turn_completed(),
    "/loop list\r",
    turn_started(),
    user_msg("/loop list"),
    assistant_msg("I will look up the recurring tasks."),
    turn_completed(),
    "/loop cancel Build-Watch\r",
    turn_started(),
    user_msg("/loop cancel Build-Watch"),
    assistant_msg("I will cancel that task."),
    turn_completed(),
    "/loop cancel all\r",
    turn_started(),
    user_msg("/loop cancel all"),
    assistant_msg("I will cancel the remaining tasks."),
    turn_completed(),
]
screen_contains = {"rust": ("/loop cancel all", "I will cancel the remaining tasks.")}
screen_excludes = {"rust": ("Cancelled 2 scheduled loop(s).", "not implemented")}
