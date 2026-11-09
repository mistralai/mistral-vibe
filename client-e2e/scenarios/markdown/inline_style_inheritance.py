from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show nested markdown styles"
_ANSWER = "\n\n".join([
    "## The consequence for `/log-level`",
    "# [Bold heading link](https://example.com)",
    "*Italic `code`* and **bold [link](https://example.com)**.",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
