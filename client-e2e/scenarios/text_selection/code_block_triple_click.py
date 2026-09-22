"""Triple-clicking code selects its text without leading indentation."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = "return value;"

_PROMPT = "show one indented line"
_ANSWER = "```\n    return value;\n```"
_CLICK = "\x1b[<0;10;32M\x1b[<0;10;32m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _CLICK * 3,
]
