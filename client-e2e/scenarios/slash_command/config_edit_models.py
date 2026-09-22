"""Open the active-model editor with every model visible."""

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

handshake = {
    "config/read": {"config": {"models": models}},
    "runtime/read": {"runtime": {"config": {"models": models}}},
}

timeline: Timeline = ["/config\r", "\r"]
