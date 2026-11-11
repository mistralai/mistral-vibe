"""Scenario: a very long bare URL stays plain text so its terminal hyperlink escapes cannot flood the output."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

_PROMPT = "share the signed link"
_LONG_URL = "https://storage.example.com/report.pdf?sig=" + "a" * 600
_ANSWER = f"Download it from {_LONG_URL} or read [the docs](https://docs.mistral.ai)."

screen_hyperlinks = (("the docs", "https://docs.mistral.ai"),)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    take_snapshot(),
]
