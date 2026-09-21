"""A source title wider than the body wraps and stays a link on every row."""

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

_PROMPT = "web search zidane"
_ANSWER = "Zinedine Zidane is the France head coach."

# Wider than the bordered body: the title wraps, and every row of it stays a link.
_SOURCES = [
    {
        "title": (
            "Zinedine Zidane, the France head coach, on his playing career, his "
            "years at Real Madrid and what he expects from the next World Cup - "
            "Wikipedia, the free encyclopedia"
        ),
        "url": "https://en.wikipedia.org/wiki/Zidane",
    }
]

_EXPAND = "\x1b[<0;1;30M\x1b[<0;1;30m"
_HOVER = "\x1b[<35;12;29M"
# The wrapped tail of the same title still answers to the pointer.
_HOVER_TAIL = "\x1b[<35;12;30M"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    web_search_started("Zidane"),
    web_search_completed("Zidane", _ANSWER, sources=_SOURCES),
    assistant_msg(_ANSWER),
    turn_completed(),
    _EXPAND,
    _HOVER,
    _HOVER_TAIL,
]
