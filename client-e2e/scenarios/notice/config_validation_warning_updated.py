"""A mid-session validation warning surfaces as a toast once (VIBE-5122)."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    runtime_updated,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The second identical update must not re-toast: one warning, one toast.
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hello."),
    turn_completed(),
    runtime_updated(["Model 'toto' is not available — running 'Mistral Medium 3.5'."]),
    runtime_updated(["Model 'toto' is not available — running 'Mistral Medium 3.5'."]),
]

# The toast box wraps at 56 cells; assert fragments that each fit on one wrapped row.
screen_contains = ("Model 'toto' is not available", "3.5'.")
