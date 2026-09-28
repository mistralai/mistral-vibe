"""Save the highlighted fallback when a configured enum value disappeared."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

models = [
    {
        "name": "mistral-vibe-cli",
        "alias": "available-model",
        "thinking": "off",
        "supportsImages": False,
        "displayName": "Available model",
    }
]
config = {
    "activeModel": models[0],
    "activeModelPinned": False,
    "defaultModelAlias": models[0]["alias"],
    "models": models,
}
handshake = {
    "config/read": {"config": config},
    "runtime/read": {"runtime": {"config": config}},
    "config/fields/read": {
        "fields": [
            {
                "name": "active_model",
                "kind": "str",
                "value": "removed-model",
                "description": "",
                "path": "/active_model",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [{"layer": "user-toml", "value": "removed-model"}],
            }
        ],
        "targets": ["user-toml", "overrides"],
    },
}

capture_startup = False
timeline: Timeline = ["/config\r", "\r", "\r"]
