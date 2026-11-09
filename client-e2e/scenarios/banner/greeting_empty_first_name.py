"""An identity with an empty first name gets no greeting once the post-ready reads land."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.banner._greeting import PLAN, identity_handshake

handshake = identity_handshake("")

timeline: Timeline = []

screen_contains = (PLAN,)
screen_excludes = ("Hello",)
