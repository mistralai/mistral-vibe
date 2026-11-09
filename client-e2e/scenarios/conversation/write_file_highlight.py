from __future__ import annotations

from e2e.app_server.events import turn_completed, turn_started, user_msg, write_file
from e2e.app_server.scenario import Timeline

_PROMPT = "write the file"

# A .py path, so the written body is syntax-highlighted like Python's fence.
_CONTENT = "def hi(name: str) -> str:\n    return name  # done\n"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    write_file("greet.py", _CONTENT),
    turn_completed(),
]
