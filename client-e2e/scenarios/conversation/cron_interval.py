"""Scheduling an interval shows human headers and a plain-text expanded result."""

from __future__ import annotations

from e2e.app_server.effect_fixtures import effect_settled, effect_started
from e2e.app_server.events import turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_steps = {2, 3, 4}

screen_contains = {
    "rust": [
        "Scheduled every 1 minute 30 seconds: check CI",
        "Prompt: check CI",
        "Schedule: every 1 minute 30 seconds",
        "Next run: 2026-09-22 09:00:00 UTC",
        "ID: abc",
    ]
}
screen_excludes = {"rust": ["vibe.cron", "interval_seconds", "next_fire_at"]}

timeline: Timeline = [
    "schedule CI checks\r",
    turn_started(),
    user_msg("schedule CI checks"),
    effect_started("cron_effects.json", "interval"),
    effect_settled("cron_effects.json", "interval"),
    turn_completed(),
    {"release": 4},
    "\x1b[<0;1;32M\x1b[<0;1;32m",
    {"release": 2},
    "\x0f",
]
