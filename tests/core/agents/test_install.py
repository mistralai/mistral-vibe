from __future__ import annotations

import pytest

from vibe.core.agents.install import plan_installed_agents_change
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfigSchema
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator


@pytest.mark.asyncio
async def test_uninstalling_the_active_agent_switches_a_removed_plan_default_to_ask() -> (
    None
):
    # A new session would start a "plan" default as ask: so does the switch.
    local = OverridesLayer(data={"default_agent": "plan", "installed_agents": ["lean"]})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema, layers=[local], default_layer_resolver=lambda: local
    )
    agents = AgentManager(orchestrator, "lean")

    change = plan_installed_agents_change(orchestrator, agents, "lean", installed=False)

    assert change.switch_to == BuiltinAgentName.ASK
