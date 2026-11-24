"""`vibe --model` with an alias the config lacks shows the server's error, which lists the aliases."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "session/start": {
        "error": {
            "code": "invalid_params",
            "message": "Unknown model: nope. Available: devstral-small, mistral-large",
        }
    }
}
client_args = ("--model", "nope")
request_methods = {"session/start"}
timeline: Timeline = []

screen_contains = ("Unknown model: nope. Available: devstral-small",)
