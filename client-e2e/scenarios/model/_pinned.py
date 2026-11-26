"""A config pinned to devstral-small, whose display name differs from its alias."""

from __future__ import annotations

from typing import Any

MEDIUM: dict[str, Any] = {
    "name": "mistral-vibe-cli",
    "alias": "mistral-medium-3.5",
    "thinking": "medium",
    "supportsImages": True,
    "displayName": "mistral-medium-3.5",
    "thinkingLevels": ["off", "medium", "high"],
}
DEVSTRAL: dict[str, Any] = {
    "name": "devstral-small-latest",
    "alias": "devstral-small",
    "thinking": "off",
    "supportsImages": False,
    "displayName": "Devstral Small 2",
}


def pinned_config() -> dict[str, Any]:
    return {
        "activeModel": DEVSTRAL,
        "activeModelPinned": True,
        "defaultModelAlias": MEDIUM["alias"],
        "models": [MEDIUM, DEVSTRAL],
    }


def pinned_handshake() -> dict[str, Any]:
    return {
        "config/read": {"config": pinned_config()},
        "runtime/read": {"runtime": {"config": pinned_config()}},
    }
