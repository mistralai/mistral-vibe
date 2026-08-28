from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.session.session_loader import SessionLoader
from vibe.core.types import SessionMetadata


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


def _response_forcing(variant: str) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {
                "defaultValue": "cli",
                "rules": [
                    {
                        "force": variant,
                        "tracks": [
                            {
                                "experiment": {
                                    "key": ExperimentName.SYSTEM_PROMPT.value
                                },
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
        }
    })


@pytest.mark.asyncio
async def test_resume_round_trip_preserves_variant() -> None:
    response = _response_forcing("explore")
    fresh = ExperimentManager(client=_StubClient(response))
    await fresh.initialize(_attrs())

    persisted_state = fresh.export_state()
    assert persisted_state is not None

    serialized = persisted_state.model_dump_json()
    rehydrated_response = EvalResponse.model_validate_json(serialized)

    stub = _StubClient(None)
    resumed = ExperimentManager(client=stub)
    resumed.hydrate(rehydrated_response)

    assert resumed.get_variant(ExperimentName.SYSTEM_PROMPT) == "explore"
    assert resumed.assignments() == fresh.assignments()
    assert stub.calls == []


@pytest.mark.asyncio
async def test_resume_round_trip_restores_experiment_attributes() -> None:
    # Regression: hydrate() must restore the GrowthBook bucketing snapshot
    # (planName/planType/...) alongside the eval response. Without it every
    # telemetry event on a resumed session would miss experiment_attributes.
    response = _response_forcing("explore")
    fresh = ExperimentManager(client=_StubClient(response))
    attributes = ExperimentAttributes(
        userId="user-1",
        entrypoint="cli",
        agent_version="0",
        os="darwin",
        planType="mistral_code",
        planName="E",
    )
    await fresh.initialize(attributes)

    persisted_state = fresh.export_state()
    assert persisted_state is not None

    resumed = ExperimentManager(client=_StubClient(None))
    resumed.hydrate(persisted_state, attributes=fresh.attributes())

    attrs = resumed.attributes()
    assert attrs is not None
    assert attrs.planType == "mistral_code"
    assert attrs.planName == "E"


def test_hydrate_without_attributes_leaves_snapshot_none() -> None:
    # Old sessions persisted before the attributes field existed hydrate
    # without it; attributes() must stay None rather than raise.
    response = _response_forcing("explore")
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(response)
    assert manager.attributes() is None


def test_resume_old_session_without_experiments_field_falls_back_to_defaults(
    tmp_path: Path,
) -> None:
    legacy_metadata = {
        "session_id": "legacy",
        "start_time": "2025-01-01T00:00:00+00:00",
        "end_time": None,
        "git_commit": None,
        "git_branch": None,
        "environment": {"working_directory": str(tmp_path)},
        "username": "tester",
        "loops": [],
    }
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(json.dumps(legacy_metadata))

    metadata = SessionLoader.load_metadata(tmp_path)

    assert metadata.experiments is None


def test_session_metadata_round_trips_experiments_field() -> None:
    response = _response_forcing("explore")
    metadata = SessionMetadata(
        session_id="s",
        start_time="2025-01-01T00:00:00+00:00",
        end_time=None,
        git_commit=None,
        git_branch=None,
        environment={"working_directory": "/tmp"},
        username="tester",
        experiments=response,
    )

    serialized = metadata.model_dump_json()
    restored = SessionMetadata.model_validate_json(serialized)

    assert restored.experiments is not None
    assert restored.experiments == response
