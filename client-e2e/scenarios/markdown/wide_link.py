"""Scenario: a link label of wide glyphs keeps its columns once wrapped in a terminal hyperlink."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

_PROMPT = "where are the docs"
_ANSWER = "Read [文档链接](https://docs.mistral.ai/zh/) then come back."

screen_contains = ("Read 文档链接 then come back.",)
screen_hyperlinks = (("文档链接", "https://docs.mistral.ai/zh/"),)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    take_snapshot(),
]
