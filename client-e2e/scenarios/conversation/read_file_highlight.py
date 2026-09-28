"""A read python file renders its body highlighted from the file's extension."""

from __future__ import annotations

from e2e.app_server.events import read_file, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show the app file"
_CONTENT = (
    "old\r\x1b[31m   1→def greet(name):\x1b[0m\r\n"
    "   2→    \x1b]8;;https://example.com\x1b\\return f'hello {name}'\x1b]8;;\x1b\\\x00\x08\x7f"
)

# Open the group, then its read result.
_EXPAND_GROUP = "\x1b[<0;1;32M\x1b[<0;1;32m"
_EXPAND_RESULT = "\x1b[<0;8;32M\x1b[<0;8;32m"

screen_contains = {"rust": ("def greet(name):", "return f'hello {name}'")}
screen_excludes = {"rust": ("[31m", "https://example.com", "old")}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file("app.py", _CONTENT, num_lines=2),
    turn_completed(),
    _EXPAND_GROUP,
    _EXPAND_RESULT,
]
