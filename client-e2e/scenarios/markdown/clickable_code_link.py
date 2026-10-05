"""Scenario: clicking inline code inside a markdown link label opens the link."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Action, Timeline

_PROMPT = "link the tokio select docs"
_URL = "https://docs.rs/tokio/latest/tokio/macro.select.html"
_ANSWER = f"See [Official Tokio `select!` documentation](<{_URL}>)."

expected_actions = {
    "rust": [Action("open_url", _URL)],
    "python": [Action("open_url", _URL)],
}

# `select!` spans one-based columns 22-28 on the answer row.
_ROW, _COL = 32, 24
_CLICK = f"\x1b[<0;{_COL};{_ROW}M\x1b[<0;{_COL};{_ROW}m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _CLICK,
]
