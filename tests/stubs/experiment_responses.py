from __future__ import annotations

from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.models import EvalResponse


def behavior_eval_response(behavior: str) -> EvalResponse:
    """An eval that forces the system prompt behavior experiment to *behavior*."""
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT_BEHAVIOR.value: {
                "defaultValue": "",
                "rules": [{"force": behavior, "tracks": []}],
            }
        }
    })
