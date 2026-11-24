"""A catalog where the plan locks Mistral Large 4, and the same catalog once the plan includes it."""

from __future__ import annotations

from typing import Any

from e2e.app_server.events import AppServerEvent, runtime_updated


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
_LARGE = "Mistral Large 4"


def config(*, locked: bool = True) -> dict[str, Any]:
    requires = {"requires": {"kind": "higher_plan"}} if locked else {}
    return {
        "activeModel": _DEFAULT,
        "activeModelPinned": False,
        "defaultModelAlias": _DEFAULT["alias"],
        "models": [
            _DEFAULT,
            _model("mistral-large-4", _LARGE, **requires),
            _model("mistral-medium-3.5", "Mistral Medium 3.5"),
        ],
    }


def handshake(**pinned: Any) -> dict[str, Any]:
    return {
        "config/read": {"config": config()},
        "runtime/read": {"runtime": {"config": config()}},
        **pinned,
    }


def unlocked() -> AppServerEvent:
    """The runtime the session announces once the refetched catalog includes Large 4."""
    event = runtime_updated()
    event["params"]["runtime"]["config"].update(config(locked=False))
    return event


LARGE = _LARGE
