"""A prompt without images still goes out as raw text when its preparation crashes."""

from __future__ import annotations

from e2e.app_server.events import turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "what does @README.md say"
handshake = {
    "workspace/prompt/prepare": {
        "error": {"code": "internal_error", "message": "prompt preparation crashed"}
    }
}
request_methods = ("workspace/prompt/prepare",)
screen_excludes = ("prompt preparation crashed",)
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    turn_completed(),
]
