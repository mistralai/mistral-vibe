from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show me some markdown"
_ANSWER = "\n".join([
    "# Heading one",
    "## Heading two",
    "",
    "Some **bold** text, some *italic* text, and `inline code`.",
    "",
    "```",
    "plain code line one",
    "plain code line two",
    "```",
    "",
    "- first bullet",
    "- second bullet",
    "",
    "1. first step",
    "2. second step",
    "",
    "> a short blockquote",
    "",
    "A [link](https://example.com) to finish.",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
