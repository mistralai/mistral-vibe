"""`/model` and `/config` leave out a model that requires an upgrade."""

from __future__ import annotations

from typing import Any

from e2e.app_server.scenario import Timeline, take_snapshot


def _model(alias: str, display_name: str, **fields: Any) -> dict[str, Any]:
    return {
        "name": alias,
        "alias": alias,
        "thinking": "off",
        "supportsImages": False,
        "displayName": display_name,
        "source": "catalog",
        **fields,
    }


_DEFAULT = _model("zai-glm-5-3-flash", "GLM 5.3 Flash")
_MODELS = [
    _DEFAULT,
    _model("mistral-large-4", "Mistral Large 4", requires={"kind": "higher_plan"}),
    _model("mistral-medium-3.5", "Mistral Medium 3.5"),
]


def _config() -> dict[str, Any]:
    return {
        "activeModel": _DEFAULT,
        "activeModelPinned": False,
        "defaultModelAlias": _DEFAULT["alias"],
        "models": _MODELS,
    }


handshake = {
    "config/read": {"config": _config()},
    "runtime/read": {"runtime": {"config": _config()}},
}

# Open /model, close it, then open the /config active_model choices.
timeline: Timeline = [
    "/model",
    "\r",
    take_snapshot(),
    "\x1b",
    "/config\r",
    "\r",
    take_snapshot(),
]
