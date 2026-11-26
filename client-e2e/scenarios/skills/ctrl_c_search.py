"""Ctrl+C while searching the list is the app's: it asks to quit and leaves the query in place."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, installed, skill

handshake = {"skills/installed": installed([skill("deploy"), skill("lint")])}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "/dep", "\x03"]

screen_contains = ("dep", "Press Ctrl+C again to quit")
