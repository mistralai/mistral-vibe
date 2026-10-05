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

# The settled tool group header, then its first web-fetch entry one row up once unfolded.
_EXPAND = "\x1b[<0;1;30M\x1b[<0;1;30m"
_EXPAND_ENTRY = "\x1b[<0;8;29M\x1b[<0;8;29m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    # Newline-padded content: the body trims to the bare text, like Python.
    web_fetch(
        "https://example.com/article",
        content="\nThe article body.\n",
        was_truncated=True,
    ),
    web_fetch_without_output("https://example.com/no-output"),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND,
    _EXPAND_ENTRY,
]
