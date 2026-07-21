from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfigSchema
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator


async def _real_orchestrator() -> ConfigOrchestrator[VibeConfigSchema]:
    # Reproduce the production layer stack's closure resolver: the default-layer
    # resolver captures a specific layer instance, so a naive copy.deepcopy of the
    # orchestrator would leave it pointing at the original (uncopied) layer.
    data = build_test_vibe_config().model_dump(mode="json", exclude_none=True)
    layer = OverridesLayer(data=data)

    def default_layer_resolver() -> ConfigLayer[RawConfig]:
        return layer

    return await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[layer],
        default_layer_resolver=default_layer_resolver,
    )


@pytest.mark.asyncio
async def test_fork_supports_implicit_target_set_field_on_forked_loop() -> None:
    orchestrator = await _real_orchestrator()
    assert orchestrator.config.bypass_tool_permissions is False

    agent = AgentLoop(
        orchestrator,
        agent_name=BuiltinAgentName.DEFAULT,
        backend=FakeBackend(),
        mcp_registry=FakeMCPRegistry(),
    )

    forked = await agent.fork()

    # Implicit-target write routes through the default-layer resolver; this raised
    # DefaultLayerResolutionError when fork() used copy.deepcopy instead of copy().
    failures = await forked.config_orchestrator.set_field(
        "/bypass_tool_permissions", True
    )

    assert failures == []
    assert forked.config_orchestrator.config.bypass_tool_permissions is True
    assert agent.config_orchestrator.config.bypass_tool_permissions is False
