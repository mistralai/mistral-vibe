from __future__ import annotations

from typing import Any

from vibe.core.experiments import resolve
from vibe.core.experiments.active import DEFAULT_VARIANTS, ExperimentName
from vibe.core.experiments.models import EvalResponse


def _resp(features: dict[str, Any]) -> EvalResponse:
    return EvalResponse.model_validate({"features": features})


def test_variant_or_none_force_beats_default() -> None:
    r = _resp({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "explore"}],
        }
    })
    assert resolve.variant_or_none(r, ExperimentName.SYSTEM_PROMPT) == "explore"


def test_variant_or_none_falls_back_to_default() -> None:
    r = _resp({ExperimentName.SYSTEM_PROMPT.value: {"defaultValue": "explore"}})
    assert resolve.variant_or_none(r, ExperimentName.SYSTEM_PROMPT) == "explore"


def test_variant_or_none_absent_or_no_response_is_none() -> None:
    assert resolve.variant_or_none(_resp({}), ExperimentName.SYSTEM_PROMPT) is None
    assert resolve.variant_or_none(None, ExperimentName.SYSTEM_PROMPT) is None


def test_variant_falls_back_to_code_default() -> None:
    assert (
        resolve.variant(None, ExperimentName.SYSTEM_PROMPT)
        == DEFAULT_VARIANTS[ExperimentName.SYSTEM_PROMPT]
    )


def test_config_variants_drops_baseline_keeps_typed_deviation() -> None:
    r = _resp({
        ExperimentName.SYSTEM_PROMPT.value: {"defaultValue": "cli"},  # baseline
        ExperimentName.SMART_APPROVE.value: {"defaultValue": True},  # deviation (bool)
        ExperimentName.CLI_MODEL_ROUTING.value: {"defaultValue": {}},  # baseline ({})
    })
    assert resolve.config_variants(r) == {ExperimentName.SMART_APPROVE.value: True}


def test_config_variants_returns_typed_object() -> None:
    payload = {"active_model": "glm-5-2"}
    r = _resp({ExperimentName.CLI_MODEL_ROUTING.value: {"defaultValue": payload}})
    assert resolve.config_variants(r) == {
        ExperimentName.CLI_MODEL_ROUTING.value: payload
    }


def test_assignments_projects_in_experiment_track() -> None:
    r = _resp({
        ExperimentName.CLI_EXTRA_MODELS.value: {
            "defaultValue": {},
            "rules": [
                {
                    "force": {"models": []},
                    "tracks": [
                        {
                            "experiment": {"key": "exp-key"},
                            "result": {
                                "variationId": 1,
                                "value": {"models": []},
                                "inExperiment": True,
                                "hashAttribute": "userId",
                                "hashValue": "u1",
                                "featureId": ExperimentName.CLI_EXTRA_MODELS.value,
                            },
                        }
                    ],
                }
            ],
        }
    })
    a = resolve.assignments(r)
    assert len(a) == 1
    assert a[0].experiment_id == ExperimentName.CLI_EXTRA_MODELS.value
    assert a[0].experiment_name == "exp-key"
    assert a[0].in_experiment is True


def test_assignments_excludes_not_in_experiment() -> None:
    r = _resp({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "force": "explore",
                    "tracks": [
                        {"experiment": {"key": "e"}, "result": {"inExperiment": False}}
                    ],
                }
            ],
        }
    })
    assert resolve.assignments(r) == []


def test_filter_to_known_drops_unknown_features() -> None:
    r = _resp({
        "unknown_feature": {"defaultValue": 1},
        ExperimentName.SYSTEM_PROMPT.value: {"defaultValue": "cli"},
    })
    assert set(resolve.filter_to_known(r).features) == {
        ExperimentName.SYSTEM_PROMPT.value
    }
