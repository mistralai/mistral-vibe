"""An overflowing transcript copies across a tool group, keeping content blank lines only."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The promo sits above the entries; the overflow reserves a scrollbar column.
env = {"TERM_PROGRAM": "vscode", "SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}

_FIRST = "tell me a story"
_STORY = "\n\n".join(f"Story paragraph number {index:02d}." for index in range(1, 16))
_PROMPT = "run it"
# The answer opens with a code block whose own first line is blank: content, not spacing.
_ANSWER = "```\n\nhi\n```"
# Drag from the second prompt's first letter to the last transcript row (1-based SGR).
_SELECT = "\x1b[<0;3;26M\x1b[<32;119;34M\x1b[<0;119;34m"
# The collapsed group copies its label; the gaps around it and above the answer are layout.
expected_clipboard = f"{_PROMPT}\nRan 2 commands\n\nhi"

timeline: Timeline = [
    f"{_FIRST}\r",
    turn_started(),
    user_msg(_FIRST),
    assistant_msg(_STORY),
    turn_completed(),
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("echo hi", "hi"),
    bash("echo there", "there"),
    assistant_msg(_ANSWER),
    turn_completed(),
    _SELECT,
]
