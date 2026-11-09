"""A mid-session validation warning mounts one warning row (VIBE-5122, VIBE-4099)."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    runtime_updated,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The second identical update must not repeat it: one warning, one row.
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    runtime_updated(["Model 'toto' is not available — running 'Mistral Medium 3.5'."]),
    runtime_updated(["Model 'toto' is not available — running 'Mistral Medium 3.5'."]),
]

screen_contains = ("⚠ Model 'toto' is not available",)
