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
                "value": "",
                "description": "",
                "path": "/active_model",
                "popular": True,
                "enumChoices": [],
                "valueLabels": {},
                "layerValues": [{"layer": "default", "value": ""}],
            }
        ],
        "targets": ["user-toml", "project-toml", "overrides"],
    },
}

screen_contains = {"rust": ("default (currently", "defaults")}
timeline: Timeline = ["/config\r", "\r"]
