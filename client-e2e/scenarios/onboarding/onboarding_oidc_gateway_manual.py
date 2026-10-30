"""Manual API-key entry after OIDC preserves the gateway configuration."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.onboarding.onboarding_oidc_gateway import (
    expected_actions as expected_actions,
    handshake as gateway_handshake,
    request_methods as request_methods,
)

client_args = ("--setup",)
capture_startup = False
capture_steps = {5}
exit_after_last_step = True
handshake = {
    **gateway_handshake,
    "setup/oidc-poll": {
        "authorizationUrl": gateway_handshake["setup/oidc-poll"]["authorizationUrl"],
        "status": "waiting",
    },
    "setup/oidc-cancel": {},
}

timeline: Timeline = [
    "\r",
    "\x1b[B",
    "\r",
    "\r",
    "\r",
    "m",
    "synthetic-gateway-key",
    "\r",
]
