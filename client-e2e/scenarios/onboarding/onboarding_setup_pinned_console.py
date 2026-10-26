"""`--setup` with an administrator-pinned console: the locked form submits the pin."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--setup",)
exit_after_last_step = True
# The client inherits the admin's env var; the lock must come from the server's setup/status answer.
env = {"VIBE_CUSTOM_CONSOLE_URL": "https://console.acme.internal"}
handshake = {
    "setup/status": {
        "provider": {
            "name": "mistral",
            "apiBase": "https://api.mistral.ai/v1",
            "apiKeyEnvVar": "MISTRAL_API_KEY",
            "browserAuthBaseUrl": "https://console.mistral.ai",
            "browserAuthApiBaseUrl": "https://console.mistral.ai/api",
            "browserAuthAllowOriginRewrite": False,
        },
        "consoleBaseUrl": "https://console.mistral.ai",
        "vibeBaseUrl": "https://chat.mistral.ai",
        "activeModel": "mistral-medium-3.5",
        "theme": "ansi-dark",
        "supportsBrowserSignIn": True,
        "hasApiKey": False,
        "pinnedConsoleUrl": "https://console.acme.internal",
    }
}
request_methods = {"setup/status", "setup/store-credential", "setup/submit-choices"}

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\r",  # theme -> auth method
    "\r",  # auth: Launch browser -> the locked console form (no target picker)
    "\r",  # the locked form's Enter starts the replayed browser sign-in
    "m",  # switch to the API key screen, cancelling the sign-in
    "test-key-123",  # the masked card fills and validates
    "\r",  # submit: the pinned provider lands through submit-choices, then exit
]
