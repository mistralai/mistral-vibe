"""Invalid-looking loop arguments reach the model without local validation."""

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
    "/loop invalid prompt\r",
    turn_started(),
    user_msg("/loop invalid prompt"),
    assistant_msg("How often should I run that prompt?"),
    turn_completed(),
    "/loop cancel\r",
    turn_started(),
    user_msg("/loop cancel"),
    assistant_msg("Which recurring task should I cancel?"),
    turn_completed(),
]
screen_contains = {"rust": ("/loop cancel", "Which recurring task should I cancel?")}
screen_excludes = {"rust": ("Missing loop id.", "Usage:", "not implemented")}
