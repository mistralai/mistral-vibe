"""`--setup`: a shell-set key warns on every screen that saves a key; the override confirm takes its slot."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

client_args = ("--setup",)
handshake = {
    "setup/status": {
        "provider": {
            "name": "mistral",
            "apiBase": "https://api.mistral.ai/v1",
            "apiKeyEnvVar": "MISTRAL_API_KEY",
            "browserAuthBaseUrl": "https://console.acme.example",
            "browserAuthApiBaseUrl": "https://console.acme.example/api",
            "browserAuthAllowOriginRewrite": False,
        },
        "consoleBaseUrl": "https://console.mistral.ai",
        "vibeBaseUrl": "https://chat.mistral.ai",
        "activeModel": "mistral-medium-3.5",
        "theme": "ansi-dark",
        "supportsBrowserSignIn": True,
        "hasApiKey": True,
        # The server's /status answer: the shell's key is in use, so a saved key is shadowed.
        "activeCredentialSource": "process_env",
    }
}
request_methods = {"setup/status"}
screen_contains = ("MISTRAL_API_KEY is set in your shell",)

timeline: Timeline = [
    "\r",
    take_snapshot(),  # welcome -> theme
    "\r",
    take_snapshot(),  # theme -> auth method: the shell-key warning shows
    "\r",
    take_snapshot(),  # auth: Launch browser -> sign-in target, the shell-key warning shows
    "\r",
    take_snapshot(),  # Mistral AI: the override confirm arms and replaces the shell-key warning
    "\x1b[B",
    take_snapshot(),  # navigating disarms the confirm: the shell-key warning is back
    "\x1b",
    take_snapshot(),  # back to the auth method
    "\x1b[B\r",
    take_snapshot(),  # Use an API key: the shell-key warning shows under the key card
]
