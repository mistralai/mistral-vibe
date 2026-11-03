"""Scrolling up shows a jump-to-bottom chip; clicking it returns to the newest message and hides it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_WHEEL_UP = "\x1b[<64;60;20M"
_CHIP_CLICK = "\x1b[<0;60;32M\x1b[<0;60;32m"


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


timeline: Timeline = [
    *_turn("hi", _tall_reply("a", 24)),
    *_turn("more", _tall_reply("b", 24)),
    _WHEEL_UP * 6,
    _CHIP_CLICK,
]

capture_steps = {2, 3}
screen_contains = ("b24",)
screen_excludes = ("Jump to bottom",)
