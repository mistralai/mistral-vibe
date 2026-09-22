"""Double- and triple-clicking the transcript selects a word, then the whole row."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}


def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {i:02d}."
        for i in range(1, lines + 1)
    )


def _clicks(row: int, count: int) -> str:
    return f"\x1b[<0;16;{row}M\x1b[<0;16;{row}m" * count


timeline: Timeline = [
    "tell me a story\r",
    turn_started(),
    user_msg("tell me a story"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    _clicks(20, 2),
    _clicks(22, 3),
]
