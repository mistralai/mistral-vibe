"""A rate-limited turn re-reads the account at once, so the usage gauge can show the allowance ran out."""

from __future__ import annotations

from e2e.app_server.events import turn_failed, turn_started, user_msg
from e2e.app_server.scenario import Timeline
from scenarios.footer.usage_gauge import handshake as handshake

# The post-ready read, then the one the rate limit forces inside the minute.
request_methods = {"account/read"}

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    turn_failed(code="rate_limit", message="Too many requests"),
]
screen_contains = ("usage ██▍    40%",)
