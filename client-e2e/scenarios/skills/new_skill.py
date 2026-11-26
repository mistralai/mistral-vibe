"""`n` closes /skills and leaves the skill-creator command in the composer, for the user to say what the skill does."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, installed, skill

handshake = {"skills/installed": installed([skill("lint")])}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "n"]

screen_contains = ("/ vibe:skill-creator Create a new skill:",)
screen_excludes = ("Search skills",)
