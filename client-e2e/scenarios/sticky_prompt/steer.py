"""Past a steer, the pin shows the prompt and the steer on one row each, and a click on the steer jumps to it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sticky_prompt._turns import tall_reply

_PROMPT = " ".join(f"word{index:03d}" for index in range(1, 81))
_STEER = user_msg("also check the docs", entry_id="steer-1")
_STEER["params"]["entry"]["source"] = "turn_steer"
_CLICK_STEER = "\x1b[<0;30;2M\x1b[<0;30;2m"

STEERED_TURN: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(tall_reply("a", 6)),
    _STEER,
    assistant_msg(tall_reply("b", 24)),
    turn_completed(),
]

timeline: Timeline = [*STEERED_TURN, take_snapshot(), _CLICK_STEER]

# The click brings the steer just under the full prompt pinned above it.
screen_rows = {3: "─" * 119, 4: "", 5: "↳ also check the docs"}
