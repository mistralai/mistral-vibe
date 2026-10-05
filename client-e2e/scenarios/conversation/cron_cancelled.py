"""Cancelling a scheduled prompt is a successful result without a next-run row."""

from __future__ import annotations

from e2e.app_server.effect_fixtures import effect_settled, effect_started
from e2e.app_server.events import turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_steps = {2, 3, 4}

screen_contains = {
    "rust": [
        "Cancelled check CI",
        "Prompt: check CI",
        "Schedule: every 1 minute 30 seconds",
        "ID: abc",
    ]
}
screen_excludes = {
    "rust": ["vibe.cron", "interval_seconds", "next_fire_at", "Next run:"]
}

timeline: Timeline = [
    "cancel the CI check\r",
    turn_started(),
    user_msg("cancel the CI check"),
    effect_started("cron_effects.json", "cancelled"),
    effect_settled("cron_effects.json", "cancelled"),
    turn_completed(),
    {"release": 4},
    "\x1b[<0;1;32M\x1b[<0;1;32m",
    {"release": 2},
    "\x0f",
]
