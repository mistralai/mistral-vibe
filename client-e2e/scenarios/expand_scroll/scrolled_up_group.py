"""Expanding a group near the viewport bottom scrolls it into view; collapsing keeps its header."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

screen_rows = {"rust": {23: "⏵ Ran 8 commands"}}

_WHEEL_UP = "\x1b[<64;60;10M"


def _click(row: int, column: int) -> str:
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


def _reply(tag: str, lines: int) -> str:
    return "\n\n".join(f"{tag} {index:02d}" for index in range(1, lines + 1))


timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg(_reply("Greeting line", 20)),
    turn_completed(),
    "list files\r",
    turn_started(),
    user_msg("list files"),
    *(bash(f"ls dir{index}", f"file{index}.txt") for index in range(1, 9)),
    assistant_msg(_reply("Listed", 3)),
    turn_completed(),
    "summarize\r",
    turn_started(),
    user_msg("summarize"),
    assistant_msg(_reply("Summary line", 30)),
    turn_completed(),
    _WHEEL_UP * 33,
    # SGR row 28 hits the group header, near the viewport bottom.
    _click(28, 1),
    # The reveal lifted the header to SGR row 24; another column avoids a double-click.
    _click(24, 3),
]
