"""A read python file renders its body highlighted from the file's extension."""

from __future__ import annotations

from e2e.app_server.events import read_file, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show the app file"
_CONTENT = "   1→def greet(name):\n   2→    return f'hello {name}'"

# The settled disclosure header lands on SGR row 32.
_CLICK = "\x1b[<0;1;32M\x1b[<0;1;32m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file("app.py", _CONTENT, num_lines=2),
    turn_completed(),
    _CLICK,
]
