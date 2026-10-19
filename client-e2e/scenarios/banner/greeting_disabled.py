"""With `show_greeting` off, a named identity gets no greeting."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.banner._greeting import PLAN, identity_handshake

handshake = {
    **identity_handshake("Ada"),
    "config/read": {"config": {"showGreeting": False}},
}

timeline: Timeline = []

screen_contains = (PLAN,)
screen_excludes = ("Hello Ada",)
