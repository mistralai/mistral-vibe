"""Loop completion, bare input, and uppercase aliases remain model-facing prompts."""

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
    "/loo\t\r",
    turn_started(),
    user_msg("/loop"),
    assistant_msg("What would you like me to repeat?"),
    turn_completed(),
    "/LOOP LS\r",
    turn_started(),
    user_msg("/LOOP LS"),
    assistant_msg("I will check your scheduled tasks."),
    turn_completed(),
]
screen_contains = {"rust": ("/LOOP LS", "I will check your scheduled tasks.")}
screen_excludes = {"rust": ("No scheduled loops.", "Usage:", "not implemented")}
