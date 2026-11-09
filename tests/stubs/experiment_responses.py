from __future__ import annotations

from vibe.core.experiments.models import EvalResponse


def routing_eval_response(alias: str) -> EvalResponse:
    """An eval that forces the routing experiment onto a model named *alias*."""
    return EvalResponse.model_validate({
        "features": {
            "vibe_cli_default_routing_model": {
                "defaultValue": {},
                "rules": [
                    {
                        "force": {
                            "active_model": alias,
                            "model_config": {
                                "name": f"{alias}-model",
                                "provider": "mistral",
                                "alias": alias,
                            },
                        },
                        "tracks": [],
                    }
                ],
            }
        }
    })
