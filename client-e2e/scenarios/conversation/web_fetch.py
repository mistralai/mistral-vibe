"""Web-fetch results settle with their suffix and mark body-less results inert."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
    web_fetch,
    web_fetch_without_output,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "fetch the articles"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    web_fetch(
        "https://example.com/article", content="The article body.", was_truncated=True
    ),
    web_fetch_without_output("https://example.com/no-output"),
    assistant_msg("Done."),
    turn_completed(),
]
