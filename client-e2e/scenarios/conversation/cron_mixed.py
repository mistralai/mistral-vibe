"""A mixed schedule list preserves calendar text and blank lines between prompts."""

from __future__ import annotations

from e2e.app_server.effect_fixtures import effect_settled, effect_started
from e2e.app_server.events import turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_steps = {2, 3, 4}

screen_contains = {
    "rust": [
        "Listed 2 scheduled prompts",
        "Prompt: check CI",
        "Schedule: every 1 minute 30 seconds",
        "Prompt: review weekday builds",
        "Schedule: cron 0 9 * * 1-5 (local time)",
        "Next run: 2026-09-23 09:00:00 UTC",
        "ID: weekdays",
    ]
}
screen_excludes = {"rust": ["vibe.cron", "interval_seconds", "next_fire_at"]}

timeline: Timeline = [
    "list all scheduled prompts\r",
    turn_started(),
    user_msg("list all scheduled prompts"),
    effect_started("cron_effects.json", "mixed"),
    effect_settled("cron_effects.json", "mixed"),
    turn_completed(),
    {"release": 4},
    "\x1b[<0;1;32M\x1b[<0;1;32m",
    {"release": 2},
    "\x0f",
]
