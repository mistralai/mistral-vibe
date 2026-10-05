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
# The answer repeats the first source's title, so a link must bind to the bullet.
_ANSWER = "Zinedine Zidane is the France head coach."
# The second source has no title: both clients label it with the bare URL.
# The third source is not http(s): both clients render it as a plain bullet.
_SOURCES = [
    {
        "title": "Zinedine Zidane - Wikipedia",
        "url": "https://en.wikipedia.org/wiki/Zidane",
    },
    {"title": "", "url": "https://www.bbc.com/sport/football/zidane"},
    {"title": "Local note", "url": "file:///tmp/zidane.txt"},
]

# The settled tool group header lands on SGR row 30, then its web-search entry header.
_EXPAND = "\x1b[<0;1;30M\x1b[<0;1;30m"
_EXPAND_ENTRY = "\x1b[<0;8;30M\x1b[<0;8;30m"
# Hovering the first bullet, under the group and body borders, repaints it as a hovered link.
_HOVER = "\x1b[<35;20;28M"
# Clicking the same bullet opens its source.
_CLICK = "\x1b[<0;20;28M\x1b[<0;20;28m"

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
    _EXPAND_ENTRY,
    _HOVER,
    _CLICK,
]
