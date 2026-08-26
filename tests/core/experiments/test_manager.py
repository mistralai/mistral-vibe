from __future__ import annotations

import json
from typing import Any

import pytest

from vibe.core.experiments.active import DEFAULT_VARIANTS, ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import (
    ExperimentManager,
    config_variants_from_response,
    hash_api_key,
)
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes


class _StubClient(RemoteEvalClient):
    def __init__(self, response: EvalResponse | None) -> None:
        self._response = response
        self.calls: list[ExperimentAttributes] = []

    async def evaluate(self, attributes: ExperimentAttributes) -> EvalResponse | None:
        self.calls.append(attributes)
        return self._response

    async def aclose(self) -> None:
        pass


def _attrs() -> ExperimentAttributes:
    return ExperimentAttributes(
        userId="x", entrypoint="cli", agent_version="0", os="darwin"
    )


def _response(features: dict[str, Any]) -> EvalResponse:
    return EvalResponse.model_validate({"features": features})


def test_hash_api_key_is_stable_and_anonymous() -> None:
    a = hash_api_key("sk-abc")
    b = hash_api_key("sk-abc")
    assert a == b
    assert "sk-" not in a
    assert len(a) == 32


def test_hash_api_key_differs_per_key() -> None:
    assert hash_api_key("sk-abc") != hash_api_key("sk-def")


@pytest.mark.asyncio
async def test_get_variant_returns_default_when_uninitialized() -> None:
    manager = ExperimentManager(client=_StubClient(None))
    assert manager.get_variant(ExperimentName.SYSTEM_PROMPT) == "cli"
    assert manager.get_variant(ExperimentName.MANAGED_SHELL_TOOLS) == "legacy"


@pytest.mark.asyncio
async def test_get_variant_or_none_returns_none_when_unassigned() -> None:
    manager = ExperimentManager(client=_StubClient(None))
    assert manager.get_variant_or_none(ExperimentName.SYSTEM_PROMPT) is None


@pytest.mark.asyncio
async def test_managed_shell_tools_remote_variant_enables_rollout() -> None:
    response = _response({
        ExperimentName.MANAGED_SHELL_TOOLS.value: {
            "defaultValue": "legacy",
            "rules": [{"force": "managed", "tracks": []}],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))

    await manager.initialize(_attrs())

    assert manager.get_variant(ExperimentName.MANAGED_SHELL_TOOLS) == "managed"


@pytest.mark.asyncio
async def test_get_variant_or_none_returns_resolved_value() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "explore", "tracks": []}],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assert manager.get_variant_or_none(ExperimentName.SYSTEM_PROMPT) == "explore"


@pytest.mark.asyncio
async def test_get_variant_serializes_json_object_value() -> None:
    payload = {
        "active_model": "target-testing-model-alias",
        "fallbacks": {"multimodal": "mistral-medium-3.5"},
    }
    response = _response({
        ExperimentName.CLI_MODEL_ROUTING.value: {"defaultValue": payload, "rules": []}
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())

    variant = manager.get_variant(ExperimentName.CLI_MODEL_ROUTING)
    assert json.loads(variant) == payload


@pytest.mark.asyncio
async def test_assignments_serializes_json_object_arm() -> None:
    payload = {
        "active_model": "target-testing-model-alias",
        "fallbacks": {"multimodal": "mistral-medium-3.5"},
    }
    response = _response({
        ExperimentName.CLI_MODEL_ROUTING.value: {
            "defaultValue": payload,
            "rules": [
                {
                    "tracks": [
                        {
                            "experiment": {
                                "key": ExperimentName.CLI_MODEL_ROUTING.value
                            },
                            "result": {
                                "variationId": 1,
                                "value": payload,
                                "inExperiment": True,
                            },
                        }
                    ]
                }
            ],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())

    assignments = manager.assignments()
    assert len(assignments) == 1
    assert assignments[0].experiment_id == ExperimentName.CLI_MODEL_ROUTING.value
    assert json.loads(assignments[0].variation_name) == payload
    assert assignments[0].variation_id == 1


@pytest.mark.asyncio
async def test_assignments_dedupes_multiple_tracks_for_one_feature() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "tracks": [
                        {
                            "experiment": {"key": ExperimentName.SYSTEM_PROMPT.value},
                            "result": {
                                "variationId": 0,
                                "value": "cli",
                                "inExperiment": True,
                            },
                        },
                        {
                            "experiment": {"key": ExperimentName.SYSTEM_PROMPT.value},
                            "result": {
                                "variationId": 1,
                                "value": "explore",
                                "inExperiment": True,
                            },
                        },
                    ]
                }
            ],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())

    assignments = manager.assignments()
    assert len(assignments) == 1
    assert assignments[0].experiment_id == ExperimentName.SYSTEM_PROMPT.value
    assert assignments[0].variation_id == 1
    assert assignments[0].variation_name == "explore"


@pytest.mark.asyncio
async def test_assignments_returns_empty_without_confirmed_assignment() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "explore", "tracks": []}],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assert manager.assignments() == []


@pytest.mark.asyncio
async def test_config_variants_include_forced_rule_without_assignment() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "explore", "tracks": []}],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assert manager.assignments() == []
    assert manager.config_variants() == {ExperimentName.SYSTEM_PROMPT.value: "explore"}


@pytest.mark.asyncio
async def test_config_variants_exclude_default_value_without_forced_rule() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {"defaultValue": "explore", "rules": []}
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assert manager.config_variants() == {}


@pytest.mark.asyncio
async def test_assignments_returns_confirmed_assignment() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "force": "explore",
                    "tracks": [
                        {
                            "experiment": {"key": ExperimentName.SYSTEM_PROMPT.value},
                            "result": {
                                "key": "1",
                                "variationId": 1,
                                "inExperiment": True,
                                "hashAttribute": "userId",
                                "hashValue": "user-abc",
                                "featureId": ExperimentName.SYSTEM_PROMPT.value,
                            },
                        }
                    ],
                }
            ],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assignments = manager.assignments()
    assert len(assignments) == 1
    assert assignments[0].experiment_id == ExperimentName.SYSTEM_PROMPT.value
    assert assignments[0].experiment_name == ExperimentName.SYSTEM_PROMPT.value
    assert assignments[0].variation_name == "explore"
    assert assignments[0].variation_id == 1
    # GrowthBook result payload is captured for exposure analysis.
    assert assignments[0].in_experiment is True
    assert assignments[0].hash_attribute == "userId"
    assert assignments[0].hash_value == "user-abc"
    assert assignments[0].feature_id == ExperimentName.SYSTEM_PROMPT.value


@pytest.mark.asyncio
async def test_get_variant_returns_resolved_value() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "cli_v2", "tracks": []}],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assert manager.get_variant(ExperimentName.SYSTEM_PROMPT) == "cli_v2"


@pytest.mark.asyncio
async def test_get_variant_falls_back_when_remote_returns_no_match() -> None:
    response = _response({"some_other_feature": {"defaultValue": True}})
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assert manager.get_variant(ExperimentName.SYSTEM_PROMPT) == "cli"


@pytest.mark.asyncio
async def test_assignments_uses_resolved_variant_value() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "force": "cli_v2",
                    "tracks": [
                        {
                            "experiment": {"key": ExperimentName.SYSTEM_PROMPT.value},
                            "result": {
                                "key": "1",
                                "variationId": 1,
                                "inExperiment": True,
                            },
                        }
                    ],
                }
            ],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assignments = manager.assignments()
    assert len(assignments) == 1
    assert assignments[0].experiment_id == ExperimentName.SYSTEM_PROMPT.value
    assert assignments[0].variation_name == "cli_v2"
    assert assignments[0].variation_id == 1


@pytest.mark.asyncio
async def test_assignments_prefers_track_result_value() -> None:
    # When the GrowthBook payload carries an explicit per-arm value, it wins
    # over the rule's force value — it's the most precise label for the user.
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "force": "cli_v2",
                    "tracks": [
                        {
                            "experiment": {"key": ExperimentName.SYSTEM_PROMPT.value},
                            "result": {
                                "key": "1",
                                "variationId": 1,
                                "value": "cli_v3",
                                "inExperiment": True,
                            },
                        }
                    ],
                }
            ],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assignments = manager.assignments()
    assert len(assignments) == 1
    assert assignments[0].experiment_id == ExperimentName.SYSTEM_PROMPT.value
    assert assignments[0].variation_name == "cli_v3"
    assert assignments[0].variation_id == 1


@pytest.mark.asyncio
async def test_assignments_keys_on_feature_id_when_experiment_key_differs() -> None:
    # GrowthBook feature ID and experiment key can diverge. Telemetry must
    # key on the feature ID (matches ExperimentName) so lookups and downstream
    # analysis stay consistent.
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "force": "cli_v2",
                    "tracks": [
                        {
                            "experiment": {"key": "vibe-code-cli-system-prompt"},
                            "result": {
                                "key": "1",
                                "variationId": 1,
                                "inExperiment": True,
                                "featureId": ExperimentName.SYSTEM_PROMPT.value,
                            },
                        }
                    ],
                }
            ],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assignments = manager.assignments()
    assert len(assignments) == 1
    assert assignments[0].experiment_id == ExperimentName.SYSTEM_PROMPT.value
    assert assignments[0].experiment_name == "vibe-code-cli-system-prompt"
    assert assignments[0].variation_name == "cli_v2"
    assert assignments[0].variation_id == 1


@pytest.mark.asyncio
async def test_initialize_does_nothing_on_failed_eval() -> None:
    manager = ExperimentManager(client=_StubClient(None))
    await manager.initialize(_attrs())
    assert manager.assignments() == []
    for name in ExperimentName:
        assert manager.get_variant(name) == DEFAULT_VARIANTS[name]


def test_attributes_none_before_initialize() -> None:
    manager = ExperimentManager(client=_StubClient(None))
    assert manager.attributes() is None


@pytest.mark.asyncio
async def test_attributes_retains_snapshot_even_on_failed_eval() -> None:
    # The snapshot is retained regardless of eval outcome so telemetry can emit
    # the same plan attributes GrowthBook buckets on.
    manager = ExperimentManager(client=_StubClient(None))
    attrs = _attrs()
    await manager.initialize(attrs)
    assert manager.attributes() is attrs


def test_experiment_attributes_default_custom_system_prompt_to_false() -> None:
    attrs = ExperimentAttributes(
        userId="x", entrypoint="cli", agent_version="0", os="darwin"
    )
    assert attrs.custom_system_prompt is False
    assert attrs.model_dump(exclude_none=True)["custom_system_prompt"] is False


def test_experiment_attributes_serializes_custom_system_prompt() -> None:
    attrs = ExperimentAttributes(
        userId="x",
        entrypoint="cli",
        agent_version="0",
        os="darwin",
        custom_system_prompt=True,
    )
    assert attrs.model_dump(exclude_none=True)["custom_system_prompt"] is True


def test_export_state_returns_none_before_initialize() -> None:
    manager = ExperimentManager(client=_StubClient(None))
    assert manager.export_state() is None


@pytest.mark.asyncio
async def test_export_state_returns_response_after_initialize() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "cli_v2", "tracks": []}],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())
    assert manager.export_state() == response


def test_hydrate_does_not_call_client() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "cli_v2", "tracks": []}],
        }
    })
    stub = _StubClient(response)
    manager = ExperimentManager(client=stub)
    manager.hydrate(response)
    assert stub.calls == []


def test_hydrate_makes_get_variant_match_initialized_manager() -> None:
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "force": "cli_v2",
                    "tracks": [
                        {
                            "experiment": {"key": ExperimentName.SYSTEM_PROMPT.value},
                            "result": {
                                "key": "1",
                                "variationId": 1,
                                "inExperiment": True,
                            },
                        }
                    ],
                }
            ],
        }
    })
    hydrated = ExperimentManager(client=_StubClient(None))
    hydrated.hydrate(response)
    assert hydrated.get_variant(ExperimentName.SYSTEM_PROMPT) == "cli_v2"
    hydrated_assignments = hydrated.assignments()
    assert len(hydrated_assignments) == 1
    assert hydrated_assignments[0].experiment_id == ExperimentName.SYSTEM_PROMPT.value
    assert hydrated_assignments[0].variation_name == "cli_v2"
    assert hydrated_assignments[0].variation_id == 1


@pytest.mark.asyncio
async def test_initialize_twice_replaces_response() -> None:
    # Session reset re-runs initialize_experiments, which calls manager.initialize
    # again. The second response must replace the first so the new session sees
    # the up-to-date variant assignment.
    first = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "explore", "tracks": []}],
        }
    })
    second = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [{"force": "lean", "tracks": []}],
        }
    })

    class _ScriptedClient(RemoteEvalClient):
        def __init__(self, responses: list[EvalResponse]) -> None:
            self._responses = responses
            self.call_count = 0

        async def evaluate(
            self, attributes: ExperimentAttributes
        ) -> EvalResponse | None:
            response = self._responses[self.call_count]
            self.call_count += 1
            return response

        async def aclose(self) -> None:
            pass

    client = _ScriptedClient([first, second])
    manager = ExperimentManager(client=client)
    await manager.initialize(_attrs())
    assert manager.get_variant(ExperimentName.SYSTEM_PROMPT) == "explore"
    await manager.initialize(_attrs())
    assert manager.get_variant(ExperimentName.SYSTEM_PROMPT) == "lean"
    assert client.call_count == 2


@pytest.mark.asyncio
async def test_assignments_excludes_tracks_not_in_experiment() -> None:
    # Holdouts and forced overrides come back with inExperiment=False.
    # We must NOT report them as active experiment participants — that would
    # pollute downstream A/B analysis since the assignment map IS the
    # exposure record (no separate exposure event).
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "force": "cli",
                    "tracks": [
                        {
                            "experiment": {"key": ExperimentName.SYSTEM_PROMPT.value},
                            "result": {
                                "key": "0",
                                "variationId": 0,
                                "inExperiment": False,
                            },
                        }
                    ],
                }
            ],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())

    assert manager.assignments() == []


@pytest.mark.asyncio
async def test_assignments_excludes_tracks_with_missing_in_experiment() -> None:
    # Defensive: if the proxy returns a track without inExperiment, treat it
    # as "not confirmed in the experiment" rather than silently counting it.
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {
            "defaultValue": "cli",
            "rules": [
                {
                    "force": "cli_v2",
                    "tracks": [
                        {
                            "experiment": {"key": ExperimentName.SYSTEM_PROMPT.value},
                            "result": {"key": "1", "variationId": 1},
                        }
                    ],
                }
            ],
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())

    assert manager.assignments() == []


@pytest.mark.asyncio
async def test_initialize_drops_unknown_features() -> None:
    # GrowthBook returns every feature defined in the org. Only the ones
    # listed in ExperimentName should survive into manager state and the
    # persisted snapshot.
    response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {"defaultValue": "cli"},
        "unrelated_org_flag": {"defaultValue": True},
        "neko": {"defaultValue": False},
        "gbdemo-checkout-layout": {"defaultValue": "dev"},
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(_attrs())

    snapshot = manager.export_state()
    assert snapshot is not None
    assert set(snapshot.features.keys()) == {ExperimentName.SYSTEM_PROMPT.value}


def test_config_variants_from_response_returns_forced_routing_variant() -> None:
    response = _response({
        ExperimentName.CLI_MODEL_ROUTING.value: {
            "rules": [{"force": '{"active_model": "magistral"}', "tracks": []}]
        }
    })

    variants = config_variants_from_response(response)

    assert variants[ExperimentName.CLI_MODEL_ROUTING.value] == (
        '{"active_model": "magistral"}'
    )


def test_config_variants_from_response_empty_without_forced_or_assigned() -> None:
    response = _response({
        ExperimentName.CLI_MODEL_ROUTING.value: {"defaultValue": "x", "rules": []}
    })

    assert config_variants_from_response(response) == {}


def test_hydrate_drops_unknown_features() -> None:
    # When resuming a session saved by an older vibe version, the snapshot
    # may contain experiments that have since been retired. Filter them on
    # hydrate so the in-memory state matches the current ExperimentName.
    legacy_response = _response({
        ExperimentName.SYSTEM_PROMPT.value: {"defaultValue": "cli"},
        "retired_experiment": {"defaultValue": "control"},
    })
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(legacy_response)

    snapshot = manager.export_state()
    assert snapshot is not None
    assert set(snapshot.features.keys()) == {ExperimentName.SYSTEM_PROMPT.value}
