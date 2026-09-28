"""Scenario: hovering an assistant markdown link uses Textual's link style."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "give me Paul Vezia's profile"
_ANSWER = "• Paul Vezia — Senior AI Engineer at Publicis. [LinkedIn profile](https://www.linkedin.com)"

# SGR button 35 is pointer motion with no button held. LinkedIn starts at the
# one-based terminal cell (50, 32) after this turn settles.
_ROW, _COL = 32, 50
_HOVER = f"\x1b[<35;{_COL};{_ROW}M"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _HOVER,
]
