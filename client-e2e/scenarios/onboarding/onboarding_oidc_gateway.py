"""OIDC is reachable from a non-browser provider without rebinding its config."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline

client_args = ("--setup",)
capture_startup = False
capture_steps = {2, 3}
exit_after_last_step = True
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
    "setup/status": {
        "provider": {
            "name": "internal-ai-gateway",
            "apiBase": "https://internal-ai-gateway.mistralai.com",
            "apiKeyEnvVar": "GATEWAY_API_KEY",
            "browserAuthBaseUrl": None,
            "browserAuthApiBaseUrl": None,
            "browserAuthAllowOriginRewrite": False,
        },
        "consoleBaseUrl": "https://console.gateway.invalid",
        "vibeBaseUrl": "https://chat.gateway.invalid",
        "supportsBrowserSignIn": False,
        "hasApiKey": True,
        "oidcEnabled": True,
    },
    "setup/oidc-start": {"attemptId": "oidc-replay"},
    "setup/oidc-poll": {"authorizationUrl": _AUTHORIZATION_URL, "status": "completed"},
}
expected_actions = [Action("open_url", _AUTHORIZATION_URL)]
screen_excludes = ("Paste API key",)

timeline: Timeline = ["\r", "\x1b[B", "\r", "\r", "\r"]
