"""Transcript and composer selections stay isolated when a drag crosses their boundary."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}


def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {i:02d}."
        for i in range(1, lines + 1)
    )


# Every gesture first selects within its owning surface, then moves through the
# other surface. The owner must not change and copy must use that owner only.
_CHAT_TO_INPUT = "\x1b[<0;4;18M\x1b[<32;30;18M\x1b[<32;40;36M\x1b[<0;40;36m"
_INPUT_TO_CHAT = "\x1b[<0;10;36M\x1b[<32;22;36M\x1b[<32;40;18M\x1b[<0;40;18m"
_INPUT_TO_CHAT_TO_INPUT = (
    "\x1b[<0;10;36M\x1b[<32;22;36M\x1b[<32;40;18M\x1b[<32;30;36M\x1b[<0;30;36m"
)

timeline: Timeline = [
    "show cross-surface selection\r",
    turn_started(),
    user_msg("show cross-surface selection"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    "a draft in the composer",
    _CHAT_TO_INPUT,
    _INPUT_TO_CHAT,
    _INPUT_TO_CHAT_TO_INPUT,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
