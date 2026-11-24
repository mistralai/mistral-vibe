"""A terminal too short to spare a row pins only the prompt, leaving out the steer under it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot
from scenarios.sticky_prompt.steer import STEERED_TURN

timeline: Timeline = [resize(28, 120), *STEERED_TURN, take_snapshot()]

screen_contains = ("> word001",)
screen_excludes = ("↳ also check the docs",)
