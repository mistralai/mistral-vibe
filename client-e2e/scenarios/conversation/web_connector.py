"""Unified web connector calls render like the built-in web search and fetch."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
    web_connector_call,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "search peluche chat then open the first result"

# Keyed by opaque ids and listed out of rank order: rows follow `rank`.
_RESULTS = {
    "xNyQ0Yof": {
        "url": "https://www.example.org/peluches",
        "title": "Peluches chat - Example",
        "description": "This <strong>plush cat</strong> stares at you.",
        "snippets": ["Avec leurs grands yeux, les chats font fondre les coeurs."],
        "date": None,
        "rank": 1,
        "can_open": True,
        "metadata": {"lang": "fr", "source": "brave"},
    },
    "CyGcVA9F": {
        "url": "https://www.ma-peluche.fr/peluche-chat/",
        "title": "Peluche chat : nouvelle collection",
        "description": "La vraie question serait de savoir comment ne pas les aimer.",
        "snippets": [],
        "date": None,
        "rank": 0,
        "can_open": True,
        "metadata": {"lang": "fr", "source": "brave"},
    },
}
_PAGE = {
    "url": "https://www.ma-peluche.fr/peluche-chat/",
    "content": "\n# Peluche chat\n\nNotre collection de peluches chat.\n",
    "can_open": True,
    "title": "Peluche chat",
    "description": None,
    "date": None,
}
_BLOCKED = {
    "url": "https://google.com",
    "content": "This page could not be read: the site blocked automated access.",
    "can_open": False,
    "title": None,
    "description": None,
    "date": None,
}

# The settled tool group header, then each connector entry header once unfolded.
_EXPAND = "\x1b[<0;1;30M\x1b[<0;1;30m"
_EXPAND_SEARCH = "\x1b[<0;8;28M\x1b[<0;8;28m"
# The unreadable page first: unfolding it lifts the rows above it by one.
_EXPAND_BLOCKED = "\x1b[<0;8;30M\x1b[<0;8;30m"
_EXPAND_PAGE = "\x1b[<0;8;28M\x1b[<0;8;28m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    web_connector_call(
        "web_search",
        {"query": "peluche chat", "limit": 10},
        _RESULTS,
        label=("Searching", "the web", "Searched"),
    ),
    web_connector_call(
        "open_url", {"url": _PAGE["url"]}, _PAGE, label=("Opening", "a URL", "Opened")
    ),
    web_connector_call(
        "open_url",
        {"url": _BLOCKED["url"]},
        _BLOCKED,
        label=("Opening", "a URL", "Opened"),
    ),
    assistant_msg("Voici les peluches."),
    turn_completed(),
    _EXPAND,
    _EXPAND_SEARCH,
    _EXPAND_BLOCKED,
    _EXPAND_PAGE,
]
