"""A startup validation warning lands in the startup warnings box (VIBE-5122, VIBE-4099)."""

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

screen_contains = ("Warnings", "Model 'toto' is not available")
