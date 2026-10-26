"""Skills `retry` and `retro`: the built-in `/retry` is listed once, ranking is stable, and Enter runs the highlighted row."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "/retro"
_SKILLS = [
    {
        "name": name,
        "description": description,
        "prompt": "",
        "userInvocable": True,
        "source": "local",
        "scope": "global",
    }
    for name, description in (
        ("retry", "Retry the last failing check."),
        ("retro", "Write a sprint retrospective."),
    )
]

handshake = {
    "runtime/read": {"runtime": {"skills": _SKILLS}},
    "workspace/prompt/prepare": {
        "prompt": {
            "displayText": _PROMPT,
            "promptText": _PROMPT,
            "images": [],
            "autoTitle": None,
            "mentions": {"count": 0, "contextTypes": {}, "fileExtensions": {}},
        }
    },
}

request_methods = {"telemetry/record"}
capture_startup = False
screen_excludes = ("Retry the last failing check.",)

timeline: Timeline = [
    "/re",
    "tr",
    "\x1b[B",
    "\x1b[A",
    "\r",
    turn_started(),
    user_msg(_PROMPT, entry_id="user-retro"),
    assistant_msg("Retro drafted."),
    turn_completed(),
    "/retry",
]
