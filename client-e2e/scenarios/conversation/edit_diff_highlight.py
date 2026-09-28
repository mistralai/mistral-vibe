from __future__ import annotations

from e2e.app_server.events import edit_file, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "edit the file"

# A .py path, so the diff bodies are syntax-highlighted per line.
_OLD = "def hi(name):\n    return None\n"
_NEW = "def hi(name: str) -> str:\n    return f'hi {name}'  # done\n"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("greet.py", [(3, _OLD, _NEW)]),
    turn_completed(),
]
