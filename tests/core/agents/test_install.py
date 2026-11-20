from __future__ import annotations

import pytest

from vibe.core.agents.install import plan_installed_agents_change
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfigSchema
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator


@pytest.mark.asyncio
async def test_uninstalling_the_active_agent_switches_to_the_default_agent() -> None:
    # A removed "plan" default is a policy now, so the agent becomes the default.
    local = OverridesLayer(data={"default_agent": "plan", "installed_agents": ["lean"]})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema, layers=[local], default_layer_resolver=lambda: local
    )
    agents = AgentManager(orchestrator, "lean")

    change = plan_installed_agents_change(orchestrator, agents, "lean", installed=False)

    assert change.switch_to == BuiltinAgentName.DEFAULT
    assert agents.active_policy.name == "ask"


@pytest.mark.asyncio
async def test_uninstalling_the_active_agent_under_an_org_list_switches_to_one_it_allows(
    tmp_path,
) -> None:
    from vibe.core.config.layers.admin import AdminConfigLayer

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reviewer.toml").write_text('enabled_tools = ["read_file"]\n')
    local = OverridesLayer(
        data={"installed_agents": ["lean"], "agent_paths": [str(agents_dir)]}
    )
    admin = AdminConfigLayer()
    admin.load_managed_toml('enabled_agents = ["lean", "reviewer"]\n')
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[local, admin],
        default_layer_resolver=lambda: local,
    )
    agents = AgentManager(orchestrator, "lean")

    change = plan_installed_agents_change(orchestrator, agents, "lean", installed=False)

    assert change.switch_to == "reviewer"
