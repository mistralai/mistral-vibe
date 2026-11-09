"""Dragging the transcript scrollbar thumb scrolls without selecting text."""

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


_PRESS = "\x1b[<0;120;30M"
_DRAG_MID = "\x1b[<32;120;23M"
_DRAG_END = "\x1b[<32;120;16M"
_RELEASE = "\x1b[<0;120;16m"

timeline: Timeline = [
    *_turn("hi", _tall_reply("a", 24)),
    *_turn("more", _tall_reply("b", 24)),
    _PRESS,
    _DRAG_MID,
    _DRAG_END,
    _RELEASE,
]

capture_steps = {0, 1, 5}
