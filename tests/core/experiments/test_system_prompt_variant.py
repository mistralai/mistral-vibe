from __future__ import annotations

from datetime import date
from string import Template

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.agents import AgentManager
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.prompts import load_system_prompt
from vibe.core.skills.manager import SkillManager
from vibe.core.system_prompt import get_universal_system_prompt
from vibe.core.tools.manager import ToolManager


def _expected_prompt(prompt_id: str) -> str:
    today = date.today()
    current_date = f"{today.isoformat()} ({today.strftime('%A')})"
    return Template(load_system_prompt(prompt_id)).safe_substitute(
        current_date=current_date
    )


class _StubClient(RemoteEvalClient):
    def __init__(self, response: EvalResponse | None) -> None:
        self._response = response

    async def evaluate(self, attributes: ExperimentAttributes) -> EvalResponse | None:
        return self._response

    async def aclose(self) -> None:
        pass


def _build_managers(config):
    return (
        ToolManager(lambda: config),
        SkillManager(lambda: config),
        AgentManager(lambda: config),
    )


@pytest.mark.asyncio
async def test_system_prompt_uses_assigned_variant() -> None:
    config = build_test_vibe_config(
        system_prompt_id="cli",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    response = EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {
                "defaultValue": "cli",
                "rules": [{"force": "tests", "tracks": []}],
            }
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(
        ExperimentAttributes(
            userId="x", entrypoint="cli", agent_version="0", os="darwin"
        )
    )

    tool_manager, skill_manager, agent_manager = _build_managers(config)
    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, experiment_manager=manager
    )
    assert prompt.startswith("You are Vibe, a super useful programming assistant.")


@pytest.mark.asyncio
async def test_system_prompt_falls_back_to_default_when_variant_unknown() -> None:
    config = build_test_vibe_config(
        system_prompt_id="cli",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    response = EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {
                "defaultValue": "cli",
                "rules": [{"force": "nonexistent_prompt", "tracks": []}],
            }
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(
        ExperimentAttributes(
            userId="x", entrypoint="cli", agent_version="0", os="darwin"
        )
    )

    tool_manager, skill_manager, agent_manager = _build_managers(config)
    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, experiment_manager=manager
    )
    # Falls back to the default `cli` system prompt.
    assert "You are Mistral Vibe" in prompt or "You are Vibe" in prompt


def test_system_prompt_uses_default_when_no_manager() -> None:
    config = build_test_vibe_config(
        system_prompt_id="cli",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    tool_manager, skill_manager, agent_manager = _build_managers(config)
    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager
    )
    assert "You are Mistral Vibe" in prompt or "You are Vibe" in prompt


def test_system_prompt_honors_user_config_when_manager_uninitialized() -> None:
    config = build_test_vibe_config(
        system_prompt_id="lean",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    manager = ExperimentManager(client=_StubClient(None))

    tool_manager, skill_manager, agent_manager = _build_managers(config)
    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, experiment_manager=manager
    )
    assert prompt == _expected_prompt("lean")


@pytest.mark.asyncio
async def test_system_prompt_honors_user_config_when_no_remote_assignment() -> None:
    config = build_test_vibe_config(
        system_prompt_id="lean",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    response = EvalResponse.model_validate({
        "features": {"some_other_feature": {"defaultValue": True}}
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(
        ExperimentAttributes(
            userId="x", entrypoint="cli", agent_version="0", os="darwin"
        )
    )

    tool_manager, skill_manager, agent_manager = _build_managers(config)
    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, experiment_manager=manager
    )
    assert prompt == _expected_prompt("lean")


@pytest.mark.asyncio
async def test_user_config_overrides_assigned_experiment_variant() -> None:
    config = build_test_vibe_config(
        system_prompt_id="lean",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
    )
    response = EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {
                "defaultValue": "cli",
                "rules": [{"force": "explore", "tracks": []}],
            }
        }
    })
    manager = ExperimentManager(client=_StubClient(response))
    await manager.initialize(
        ExperimentAttributes(
            userId="x", entrypoint="cli", agent_version="0", os="darwin"
        )
    )

    tool_manager, skill_manager, agent_manager = _build_managers(config)
    prompt = get_universal_system_prompt(
        tool_manager, config, skill_manager, agent_manager, experiment_manager=manager
    )
    assert prompt == _expected_prompt("lean")
    assert prompt != _expected_prompt("explore")
