from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.agents import AgentManager
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.session.session_loader import SessionLoader
from vibe.core.skills.manager import SkillManager
from vibe.core.system_prompt import get_universal_system_prompt
from vibe.core.tools.manager import ToolManager
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


def test_resume_overrides_still_win_over_hydrated_state() -> None:
    response = _response_forcing("explore")
    manager = ExperimentManager(
        client=_StubClient(None), overrides={ExperimentName.SYSTEM_PROMPT.value: "lean"}
    )
    manager.hydrate(response)

    assert manager.get_variant(ExperimentName.SYSTEM_PROMPT) == "lean"


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


def _build_managers(config):
    return (
        ToolManager(lambda: config),
        SkillManager(lambda: config),
        AgentManager(lambda: config),
    )


@pytest.mark.asyncio
async def test_graduated_experiment_with_deleted_variant_file_falls_back() -> None:
    config = build_test_vibe_config(
        system_prompt_id="cli", include_model_info=False, include_commit_signature=False
    )
    response = _response_forcing("removed_after_graduation_2025_07")
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(response)

    tool_manager, skill_manager, agent_manager = _build_managers(config)
    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, experiment_manager=manager
    )

    default_prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, experiment_manager=None
    )
    assert prompt == default_prompt
