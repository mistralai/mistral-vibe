from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_SHIFT_UP = "\x1b[1;2A"

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True


def _tall_reply(tag: str, lines: int) -> str:
    """Build a deterministic reply from distinct Markdown paragraphs."""
    return "\n\n".join(f"{tag}{i:02d}" for i in range(1, lines + 1))


def _turn(prompt: str, reply: str) -> Timeline:
    return [
        f"{prompt}\r",
        turn_started(),
        user_msg(prompt),
        assistant_msg(reply),
        turn_completed(),
    ]


# Two tall turns create a small thumb; one Shift+Up exposes its partial tail.
timeline: Timeline = [
    *_turn("hi", _tall_reply("a", 24)),
    {"release": 5},
    *_turn("more", _tall_reply("b", 24)),
    {"release": 5},
    _SHIFT_UP * 1,
]

# The input-only frames are intentionally busy; compare the settled turn
# frames and the final manual scroll instead.
capture_steps = {1, 3, 4}
