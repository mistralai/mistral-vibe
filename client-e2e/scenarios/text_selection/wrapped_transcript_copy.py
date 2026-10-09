"""Soft-wrapped transcript rows copy as the lines they display, not as painted rows."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}

_PROMPT = (
    "please explain why a prompt this long wraps in the transcript while the copy keeps it"
    " on the single line the user typed, without the terminal row breaks"
)
_PARAGRAPH = (
    "A paragraph long enough to wrap across the terminal width, so the copy has to rejoin"
    " its soft-wrapped rows into the single line the assistant wrote in its answer."
)
_BULLET = (
    "A bullet item long enough to wrap onto a second row under its marker, whose hang"
    " indent is layout and must never reach the clipboard."
)
_NESTED = (
    "A nested item keeps its structural indent while its own long text wraps onto a"
    " continuation row, which rejoins the item it belongs to."
)
_CODE = (
    'echo "a code line longer than the terminal width comes back as one line when copied,'
    ' not split where the renderer folded it" && true'
)
# Drag from the prompt's first letter to the last transcript row (1-based SGR).
_SELECT_ALL = "\x1b[<0;3;12M\x1b[<32;120;25M\x1b[<0;120;25m"
_ANSWER = f"{_PARAGRAPH}\n\n- {_BULLET}\n  - {_NESTED}\n\n```sh\n{_CODE}\n```"
# Bullets keep their displayed marker and nesting indent, like Python's MarkdownBullet.
# The margin between the prompt and the answer is layout; the answer's own blank lines are content.
expected_clipboard = f"{_PROMPT}\n{_PARAGRAPH}\n\n• {_BULLET}\n  ▪ {_NESTED}\n\n{_CODE}"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _SELECT_ALL,
]
