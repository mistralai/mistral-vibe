"""`/model` and `/config` list models in the server's source-grouped order, Default first."""

from __future__ import annotations

from typing import Any

from e2e.app_server.scenario import Timeline


def _model(alias: str, display_name: str, source: str) -> dict[str, Any]:
    return {
        "name": alias,
        "alias": alias,
        "thinking": "off",
        "supportsImages": False,
        "displayName": display_name,
        "source": source,
    }


_DEFAULT = _model("mistral-medium-3.5", "Mistral Medium 3.5", "local")
# Admin, GrowthBook, catalog in catalog order, then the user's own and built-ins.
_MODELS = [
    _model("org-model", "Org Model", "admin"),
    _model("experiment-model", "Experiment Model", "growthbook"),
    _model("zai-glm-5-3", "GLM 5.3", "catalog"),
    _model("mistral-large-3", "Mistral Large 3", "catalog"),
    _model("devstral", "Devstral (local)", "local"),
    _DEFAULT,
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
timeline: Timeline = ["/model", "\r", "\x1b", "/config\r", "\r"]
capture_steps = {1, 4}
