"""Model and thinking picks record `vibe.model_selection.*`; only the requests are pinned."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.model._pinned import pinned_handshake

handshake = pinned_handshake()
request_methods = {"telemetry/record"}

# On devstral-small: pick mistral-medium-3.5 and keep its preselected level, pick it
# again on its next level, pick Default, then /thinking on the current level. The
# replay answers every write with the pinned config, so each pick starts from it.
timeline: Timeline = [
    "/model\r",
    "\x1b[A",
    "\r",
    "\r",
    "/model\r",
    "\x1b[A",
    "\r",
    "\x1b[B",
    "\r",
    "/model\r",
    "\x1b[A",
    "\x1b[A",
    "\r",
    "\r",
    "/thinking\r",
    "\r",
]
