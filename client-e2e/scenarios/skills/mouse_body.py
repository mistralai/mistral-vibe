"""The wheel over a page's body scrolls the body alone, and the rows stay in view."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, long_body_handshake
from e2e.pty.mouse import wheel_down

handshake = long_body_handshake()
request_methods = METHODS

_WHEEL_DOWN = wheel_down(20, 31)

timeline: Timeline = ["/skills\r", "\r", _WHEEL_DOWN * 3]

screen_contains = ("Global", "Remove")
screen_excludes = ("# deploy",)
