"""Clicking the scrollbar track pages once; holding it pages until the thumb reaches the pointer."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline


def _tall_reply(tag: str, lines: int) -> str:
    return "\n\n".join(f"{tag}{index:02d}" for index in range(1, lines + 1))


def _turn(prompt: str, reply: str) -> Timeline:
    return [
        f"{prompt}\r",
        turn_started(),
        user_msg(prompt),
        assistant_msg(reply),
        turn_completed(),
    ]


def _press(row: int) -> str:
    return f"\x1b[<0;120;{row}M"


def _release(row: int) -> str:
    return f"\x1b[<0;120;{row}m"


timeline: Timeline = [
    *_turn("hi", _tall_reply("a", 24)),
    *_turn("more", _tall_reply("b", 24)),
    _press(3) + _release(3),
    _press(3),
    _release(3),
    _press(30) + _release(30),
]

capture_steps = {1, 2, 3, 5}
