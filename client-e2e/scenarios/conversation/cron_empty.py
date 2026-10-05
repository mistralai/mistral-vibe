"""Listing no schedules keeps the result expandable and human-readable."""

from __future__ import annotations

from e2e.app_server.effect_fixtures import effect_settled, effect_started
from e2e.app_server.events import turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_steps = {2, 3, 4}

screen_contains = {"rust": ["Listed 0 scheduled prompts", "No scheduled prompts."]}
screen_excludes = {"rust": ["vibe.cron", "interval_seconds", "next_fire_at"]}

timeline: Timeline = [
    "list scheduled prompts\r",
    turn_started(),
    user_msg("list scheduled prompts"),
    effect_started("cron_effects.json", "empty"),
    effect_settled("cron_effects.json", "empty"),
    turn_completed(),
    {"release": 4},
    "\x1b[<0;1;32M\x1b[<0;1;32m",
    {"release": 2},
    "\x0f",
]
