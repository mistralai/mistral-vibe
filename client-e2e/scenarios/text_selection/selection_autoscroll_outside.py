"""A transcript drag keeps auto-scrolling after the pointer leaves its viewport."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
clipboard_contains = ("line 01.", "line 30.")


def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {index:02d}."
        for index in range(1, lines + 1)
    )


timeline: Timeline = [
    "select below the viewport\r",
    turn_started(),
    user_msg("select below the viewport"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    "\x1b[1;2A" * 8,
    "\x1b[<0;12;14M",
    "\x1b[<32;120;40M",
    "\x1b[<0;120;40m",
]
