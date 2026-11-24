"""A click on a packed steer in the pin brings it just under the pin, never back inside it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline
from scenarios.sticky_prompt._turns import tall_reply

_STEER = user_msg("also check the docs", entry_id="steer-1")
_STEER["params"]["entry"]["source"] = "turn_steer"
_STEER["params"]["entry"]["followsUser"] = True
_CLICK_STEER = "\x1b[<0;30;2M\x1b[<0;30;2m"

timeline: Timeline = [
    "fix it\r",
    turn_started(),
    user_msg("fix it"),
    assistant_msg(tall_reply("a", 6)),
    _STEER,
    assistant_msg(tall_reply("b", 24)),
    turn_completed(),
    _CLICK_STEER,
]

# The pin shows the prompt alone above the steer it no longer covers.
screen_rows = {0: "> fix it", 1: "─" * 119, 3: "↳ also check the docs"}
