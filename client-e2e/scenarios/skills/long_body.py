"""On a page with a long body the rows stay in view down to Remove; PageDown scrolls the body under them, and a move leaves it scrolled."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, long_body_handshake

handshake = long_body_handshake()
request_methods = METHODS

_PAGE_DOWN = "\x1b[6~"

timeline: Timeline = ["/skills\r", "\r", "jjj", _PAGE_DOWN, "k"]

screen_contains = ("Global", "Enabled", "Remove")
screen_excludes = ("# deploy",)
