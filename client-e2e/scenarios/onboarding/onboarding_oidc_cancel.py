"""OIDC waits as soon as the poll URL opens, then manual entry cancels it."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline

client_args = ("--setup",)
request_methods = {
    "setup/status",
    "setup/oidc-start",
    "setup/oidc-poll",
    "setup/oidc-cancel",
    "setup/store-credential",
    "setup/submit-choices",
}
_AUTHORIZATION_URL = "https://auth.mistral.ai/authorize?state=replay"
handshake = {
    "setup/status": {"oidcEnabled": True},
    "setup/oidc-start": {"attemptId": "oidc-replay"},
    "setup/oidc-cancel": {},
    "setup/oidc-poll": {"authorizationUrl": _AUTHORIZATION_URL, "status": "waiting"},
}
expected_actions = [Action("open_url", _AUTHORIZATION_URL)]
screen_contains = ("Paste API key",)
timeline: Timeline = ["\r", "\r", "\r", "\r", "m"]
