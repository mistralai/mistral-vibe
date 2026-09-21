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
# The answer repeats the first source's title, so a link must bind to the bullet.
_ANSWER = "Zinedine Zidane is the France head coach."
# The second source has no title: both clients label it with the bare URL.
_SOURCES = [
    {
        "title": "Zinedine Zidane - Wikipedia",
        "url": "https://en.wikipedia.org/wiki/Zidane",
    },
    {"title": "", "url": "https://www.bbc.com/sport/football/zidane"},
]

# The settled web-search disclosure header lands on SGR row 30.
_EXPAND = "\x1b[<0;1;30M\x1b[<0;1;30m"
# Hovering the first bullet repaints it with the link hover style.
_HOVER = "\x1b[<35;12;29M"

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
]
