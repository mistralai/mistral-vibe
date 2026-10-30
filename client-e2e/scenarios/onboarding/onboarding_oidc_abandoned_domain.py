"""Mistral OIDC discards a custom-domain attempt abandoned through manual entry."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline
from scenarios.onboarding.onboarding_oidc_complete import (
    client_args as client_args,
    expected_actions as oidc_actions,
    handshake as handshake,
    request_methods as request_methods,
)

capture_startup = False
capture_steps = set()
exit_after_last_step = True
expected_actions = [
    Action(
        "open_url",
        "https://console.mistral.ai/codestral/cli/authenticate?process_id=replay",
    ),
    *oidc_actions,
]
timeline: Timeline = [
    "\r",
    "\x1b[B",
    "\r",
    "\r",
    "\x1b[B\r",
    "https://abandoned.example",
    "\r",
    "m",
    "\x1b",
    "\r",
    "\x1b[A\r",
]
