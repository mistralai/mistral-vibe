"""A table-cell selection scrolled under the pinned prompt never paints over it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

env = {"SSH_TTY": "/dev/pts/0"}

_PROMPT = "a table under the prompt"
_PREFACE = "\n\n".join(f"Paragraph {index:02d}." for index in range(1, 21))
_TABLE = "\n".join([
    "| Feature | Behavior |",
    "| --- | --- |",
    "| Pinned prompt | Selected cell text |",
])
# Ten wheel steps bring the "Pinned prompt" cell to SGR row 11; five more slide it under the prompt.
_SELECT = "\x1b[<0;6;11M\x1b[<32;18;11M\x1b[<0;18;11m"
_WHEEL_UP = "\x1b[<64;60;10M"
_WHEEL_DOWN = "\x1b[<65;60;10M"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(f"{_PREFACE}\n\n{_TABLE}\n\n{_PREFACE}"),
    turn_completed(),
    _WHEEL_UP * 10,
    _SELECT,
    take_snapshot(),
    _WHEEL_DOWN * 5,
    take_snapshot(),
]
