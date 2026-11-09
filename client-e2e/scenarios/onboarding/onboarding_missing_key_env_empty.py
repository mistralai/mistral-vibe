"""An empty env key defers to the boot's `setup/status`; the wizard overlaps it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The harness exports MISTRAL_API_KEY=fake-key; an empty override turns the
# local probe negative, so the wizard's pre-loop round opens the welcome
# screen BEFORE any spawn and overlaps the boot behind it (the flow's
# welcome gate holds every choice until the status seeds it). The pinned
# answer says the key is missing, so the wizard completes on the SAME
# already-initialized child — one spawn and one initialize for the whole
# run (pinned by the initialize request below) — and the retried
# handshake's session/start reaches Ready on the same connection.
env = {"MISTRAL_API_KEY": ""}
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
    }
}
request_methods = {
    "initialize",
    "setup/status",
    "setup/store-credential",
    "setup/submit-choices",
    "session/start",
}

timeline: Timeline = [
    "\r",  # welcome -> theme
    "\r",  # theme -> auth method
    "\x1b[B\r",  # auth: Use an API key -> api key screen
    "test-key-123",  # the masked card fills and validates
    "\r",  # submit: the retried handshake reaches a fresh Ready
]
