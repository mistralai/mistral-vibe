"""A second steer replaces the first in the pin as soon as it slides under the pin, never hidden behind the old one."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline
from scenarios.sticky_prompt._turns import WHEEL_UP, tall_reply


def _steer(text: str, entry_id: str) -> dict:
    steer = user_msg(text, entry_id=entry_id)
    steer["params"]["entry"]["source"] = "turn_steer"
    return steer


_PROMPT = "start a background task which sleeps for 5min"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(tall_reply("a", 20)),
    _steer("coucou", "steer-1"),
    assistant_msg(tall_reply("b", 20)),
    _steer("recite the fable", "steer-2"),
    assistant_msg(tall_reply("c", 30)),
    turn_completed(),
    WHEEL_UP * 15,
]

# The second steer's text just left the viewport: the pin shows it right above the reply that follows it.
screen_rows = {0: f"> {_PROMPT}", 1: "↳ recite the fable", 3: "  c01"}
screen_excludes = ("↳ coucou",)
