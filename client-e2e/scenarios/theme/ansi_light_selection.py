"""ANSI-light transcript selection uses Textual's bright foreground."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0", "VIBE_THEME": "ansi-light"}
handshake = {
    "config/read": {"config": {"theme": "ansi-light"}},
    "runtime/read": {"runtime": {"config": {"theme": "ansi-light"}}},
}


def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {index:02d}."
        for index in range(1, lines + 1)
    )


_SELECT = "\x1b[<0;4;18M\x1b[<32;40;21M\x1b[<32;80;24M\x1b[<0;80;24m"
timeline: Timeline = [
    "tell me a story\r",
    turn_started(),
    user_msg("tell me a story"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    _SELECT,
]
