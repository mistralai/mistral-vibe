from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show me the remaining markdown constructs"
_ANSWER = "\n".join([
    "Above the rule.",
    "",
    "---",
    "",
    "Below the rule, with ~~struck-through~~ words.",
    "",
    "- [ ] an open task",
    "- [x] a done task",
    "",
    "An image: ![alt text](https://example.com/i.png) inline.",
    "",
    "Hard break line one,  ",
    "still line two.",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
