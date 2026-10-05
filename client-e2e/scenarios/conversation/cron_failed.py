"""A failed cancellation expands to its error instead of leaking raw tool output."""

from __future__ import annotations

from e2e.app_server.effect_fixtures import effect_settled, effect_started
from e2e.app_server.events import turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True
capture_steps = {2, 3, 4}

screen_contains = {
    "rust": [
        "Cancelled scheduled prompt missing",
        "Error: No scheduled loop found with ID missing",
    ]
}
screen_excludes = {"rust": ["vibe.cron", "interval_seconds", "next_fire_at"]}

timeline: Timeline = [
    "cancel the missing schedule\r",
    turn_started(),
    user_msg("cancel the missing schedule"),
    effect_started("cron_effects.json", "failed"),
    effect_settled("cron_effects.json", "failed"),
    turn_completed(),
    {"release": 4},
    "\x1b[<0;1;32M\x1b[<0;1;32m",
    {"release": 2},
    "\x0f",
]
