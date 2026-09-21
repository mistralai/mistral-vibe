"""Underlined headings do not steal a later Markdown link hitbox."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Action, Timeline

_PROMPT = "show docs"
_URL = "https://docs.example.com"
_ANSWER = f"## Docs\n\nOpen [Docs]({_URL})."
_ROW, _COL = 32, 8
_CLICK = f"\x1b[<0;{_COL};{_ROW}M\x1b[<0;{_COL};{_ROW}m"

expected_actions = {
    "python": [Action("open_url", _URL)],
    "rust": [Action("open_url", _URL)],
}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _CLICK,
]
