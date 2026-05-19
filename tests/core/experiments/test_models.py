from __future__ import annotations

from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.models import EvalResponse, FeatureDefinition, FeatureRule


class TestFeatureDefinition:
    def test_resolved_value_returns_force_when_present(self) -> None:
        feature = FeatureDefinition(
            defaultValue="cli", rules=[FeatureRule(force="cli_v2")]
        )
        assert feature.resolved_value() == "cli_v2"

    def test_resolved_value_falls_back_to_default(self) -> None:
        feature = FeatureDefinition(defaultValue="cli")
        assert feature.resolved_value() == "cli"

    def test_resolved_value_picks_first_rule_with_force(self) -> None:
        feature = FeatureDefinition(
            defaultValue="cli",
            rules=[FeatureRule(force=None), FeatureRule(force="cli_v2")],
        )
        assert feature.resolved_value() == "cli_v2"


class TestEvalResponse:
    def test_parses_real_proxy_payload(self) -> None:
        # A reduced version of the live response captured during dev probing.
        sp_key = ExperimentName.SYSTEM_PROMPT.value
        raw = {
            "features": {
                "rbac_enabled": {"defaultValue": True},
                sp_key: {
                    "defaultValue": "cli",
                    "rules": [
                        {
                            "force": "cli_v2",
                            "tracks": [
                                {
                                    "experiment": {
                                        "key": sp_key,
                                        "variations": [{}, "cli_v2"],
                                    },
                                    "result": {
                                        "key": "1",
                                        "variationId": 1,
                                        "value": "cli_v2",
                                        "inExperiment": True,
                                        "hashAttribute": "id",
                                        "hashValue": "abc",
                                    },
                                }
                            ],
                        }
                    ],
                },
            },
            "experiments": [],
            "dateUpdated": "2026-05-11T13:29:48.394Z",
        }
        response = EvalResponse.model_validate(raw)
        assert response.features["rbac_enabled"].defaultValue is True
        sp = response.features[sp_key]
        assert sp.resolved_value() == "cli_v2"
        track = sp.rules[0].tracks[0]
        assert track.experiment.key == sp_key
        assert track.result.variationId == 1
        assert track.result.inExperiment is True
