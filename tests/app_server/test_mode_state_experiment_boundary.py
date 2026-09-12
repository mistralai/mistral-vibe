"""End-to-end boundary: a GrowthBook rollout served purely via a feature's
``defaultValue`` (no force rules) must surface smart-approve all the way from the
resolved config through AgentManager into the ACP mode list.

This locks the seam past ``resolve``/``GrowthbookLayer`` (already unit-tested):
config field -> AgentManager.available_agents -> project_agent_summaries ->
build_mode_state, which is what the desktop client renders.
"""

from __future__ import annotations

import pytest

from vibe.acp.utils import build_mode_state
from vibe.app_server._projection import project_agent_summaries
from vibe.core.agents.manager import AgentManager
from vibe.core.config import build_default_orchestrator
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.manager import config_variants_from_response
from vibe.core.experiments.models import EvalResponse


def _default_value_only_smart_approve() -> EvalResponse:
    # Mirrors the desktop eval cache: smart-approve served as a boolean
    # defaultValue with no force rules / tracks.
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SMART_APPROVE.value: {"defaultValue": True, "rules": []},
            ExperimentName.SMART_APPROVE_DEFAULT.value: {
                "defaultValue": True,
                "rules": [],
            },
        }
    })


async def _orchestrator_with_smart_approve():
    orchestrator = await build_default_orchestrator()
    layer = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(layer, GrowthbookLayer)
    layer.set_variants(
        config_variants_from_response(_default_value_only_smart_approve())
    )
    await orchestrator.reload()
    return orchestrator


@pytest.mark.asyncio
async def test_default_value_rollout_enables_smart_approve_config() -> None:
    config = (await _orchestrator_with_smart_approve()).config
    assert config.smart_approve_available is True
    assert config.smart_approve_default is True
    assert config.smart_approve_offered() is True
    assert config.resolve_default_agent() == "smart-approve"


@pytest.mark.asyncio
async def test_default_value_rollout_surfaces_smart_approve_in_mode_list() -> None:
    orchestrator = await _orchestrator_with_smart_approve()
    agents = AgentManager(orchestrator, orchestrator.config.resolve_default_agent())

    active, summaries = project_agent_summaries(
        agents.active_profile, agents.available_agents.values()
    )
    state, option = build_mode_state(summaries, active)

    mode_ids = [mode.id for mode in state.available_modes]
    assert "smart-approve" in mode_ids
    assert state.current_mode_id == "smart-approve"
    assert "smart-approve" in [choice.value for choice in option.options]
