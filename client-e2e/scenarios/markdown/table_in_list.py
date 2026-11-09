from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show me a table in a list"
_ANSWER = "\n".join([
    "- cases.json holds 2 tickets.",
    "",
    "  | Family | Subset |",
    "  | --- | --- |",
    "  | T-001 | dev |",
    "  | T-002 | held-out |",
    "- fixtures.py loads them.",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
