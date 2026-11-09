"""Select input and transcript text, then scroll the transcript."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}


def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {i:02d}."
        for i in range(1, lines + 1)
    )


_INPUT_SELECT = "\x1b[<0;4;36M\x1b[<32;12;36M\x1b[<0;12;36m"
_CHAT_SELECT = "\x1b[<0;4;18M\x1b[<32;40;18M\x1b[<0;40;18m"
_SCROLL_UP = "\x1b[1;2A"

timeline: Timeline = [
    "show me every UI element\r",
    turn_started(),
    user_msg("show me every UI element"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    "a draft in the composer",
    _INPUT_SELECT,
    _CHAT_SELECT,
    _SCROLL_UP,
]
