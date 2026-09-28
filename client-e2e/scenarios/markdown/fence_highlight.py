from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show me python code"
# Space-indented: Textual misplaces its own highlight spans after a tab.
_ANSWER = "\n".join([
    "```python",
    "class G:",
    "    def hi(self, name: str) -> str:",
    "        return name  # done",
    "```",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
