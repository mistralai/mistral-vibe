from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
    web_search_completed,
    web_search_started,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "who is paul vezia? search web"
_ANSWER = (
    "Based on the most recent information available, Paul Vezia is a Senior AI "
    "Engineer at Publicis, as indicated by his LinkedIn profile."
)
_SOURCES = [
    {
        "title": "Paul VEZIA - Senior AI engineer @Publicis",
        "url": "https://www.linkedin.com/in/paul-vezia",
    }
]

# The settled web-search disclosure header lands on SGR row 29.
_CLICK = "\x1b[<0;1;29M\x1b[<0;1;29m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    web_search_started("Paul Vezia"),
    web_search_completed("Paul Vezia", _ANSWER, sources=_SOURCES),
    assistant_msg(_ANSWER),
    turn_completed(),
    _CLICK,
]
