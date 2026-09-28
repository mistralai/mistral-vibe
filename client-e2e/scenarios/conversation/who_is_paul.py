from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    reasoning,
    turn_completed,
    turn_started,
    user_msg,
    web_search_completed,
    web_search_started,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "who is paul vezia? search web"
_ANSWER = (
    "Paul Vezia appears to be a Senior AI Engineer at Publicis, per his LinkedIn "
    "profile. There is limited other public information about him."
)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    reasoning("The user wants a web search for a person. I'll use web_search."),
    web_search_started("Paul Vezia"),
    web_search_completed("Paul Vezia", _ANSWER),
    assistant_msg(f"Based on the search, {_ANSWER}"),
    turn_completed(),
]
