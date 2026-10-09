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
from e2e.app_server.scenario import Action, Timeline

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

# The lone web search stays ungrouped, so its own header row unfolds the sources.
_EXPAND = "\x1b[<0;1;15M\x1b[<0;1;15m"
# Hovering either row of the wrapped title highlights both; the tail row hangs past the bullet.
_HOVER = "\x1b[<35;14;19M"
_HOVER_TAIL = "\x1b[<35;8;20M"
# Clicking the wrapped tail opens the source.
_CLICK_TAIL = "\x1b[<0;8;20M\x1b[<0;8;20m"

expected_actions = {"rust": [Action("open_url", _SOURCES[0]["url"])]}

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
    _CLICK_TAIL,
]
