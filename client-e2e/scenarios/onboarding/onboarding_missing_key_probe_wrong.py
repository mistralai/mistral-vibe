"""A keyed answer the local probe missed closes the preloop welcome into the chat."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The harness exports MISTRAL_API_KEY=fake-key; an empty override turns the
# local probe negative, so the pre-loop round paints the welcome screen and
# overlaps the boot behind it. The pinned status says the server IS keyed
# (a key the probe cannot see: a custom provider's env var, or a key the
# server resolves from its own layers), so the round is dropped before the
# welcome gate ever opens — no user choice can be lost — and the SAME child
# runs the session handshake. The golden pins the request contract: one
# initialize, one setup/status, the session/start on the same connection,
# and no setup/store-credential or setup/submit-choices ever sent.
env = {"MISTRAL_API_KEY": ""}
handshake = {
    "setup/status": {
        "provider": {
            "name": "custom",
            "apiBase": "https://my.example/v1",
            "apiKeyEnvVar": "CUSTOM_API_KEY",
            "browserAuthBaseUrl": "https://my.example",
            "browserAuthApiBaseUrl": "https://my.example/api",
            "browserAuthAllowOriginRewrite": False,
        },
        "consoleBaseUrl": "https://my.example",
        "vibeBaseUrl": "https://my.example",
        "activeModel": "custom-model",
        "theme": "ansi-dark",
        "supportsBrowserSignIn": False,
        "hasApiKey": True,
    }
}
request_methods = {"initialize", "setup/status", "session/start"}
timeline: Timeline = []
