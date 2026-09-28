"""ANSI-light config choice scrollbars use Textual's bright blue binding."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

models = [
    {
        "name": "mistral-vibe-cli",
        "alias": "mistral-medium-3.5",
        "thinking": "medium",
        "supportsImages": True,
        "displayName": "mistral-medium-3.5",
    },
    *[
        {
            "name": f"model-{index}",
            "alias": f"model-{index}",
            "thinking": "off",
            "supportsImages": False,
            "displayName": f"Model {index}",
        }
        for index in range(1, 13)
    ],
]

env = {"VIBE_THEME": "ansi-light"}
handshake = {
    "config/read": {"config": {"models": models, "theme": "ansi-light"}},
    "runtime/read": {"runtime": {"config": {"models": models, "theme": "ansi-light"}}},
}
timeline: Timeline = ["/config\r", "\r"]
