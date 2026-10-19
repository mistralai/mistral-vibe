"""A startup validation warning surfaces as a toast at readiness (VIBE-5122)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The warning rides the startup runtime snapshot, not a server event.
handshake = {
    "runtime/read": {
        "runtime": {
            "config": {
                "validationWarnings": [
                    "Model 'toto' is not available — running 'Mistral Medium 3.5'."
                ]
            }
        }
    }
}

timeline: Timeline = []

# The toast box wraps at 56 cells; assert fragments that each fit on one wrapped row.
screen_contains = ("Model 'toto' is not available", "3.5'.")
