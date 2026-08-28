from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.app_server._runtime import AgentRuntimeFactory
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.models import EvalResponse
from vibe.core.session.session_lease import SessionBusyError, SessionLease
from vibe.core.tools.mcp import MCPRegistry


async def _real_orchestrator() -> ConfigOrchestrator[VibeConfigSchema]:
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
        agent_name=BuiltinAgentName.ASK,
        backend=FakeBackend(),
        mcp_registry=FakeMCPRegistry(),
    )

    forked = await AgentRuntimeFactory().fork(agent, None)
    failures = await forked.config_orchestrator.set_field(
        "/bypass_tool_permissions", True
    )

    assert failures == []
    assert forked.config_orchestrator.config.bypass_tool_permissions is True
    assert agent.config_orchestrator.config.bypass_tool_permissions is False


@pytest.mark.parametrize("derived_kind", ["fork", "child"])
@pytest.mark.asyncio
async def test_derived_runtime_inherits_experiment_state(derived_kind: str) -> None:
    orchestrator = await _real_orchestrator()
    agent = AgentLoop(
        orchestrator,
        agent_name=BuiltinAgentName.ASK,
        backend=FakeBackend(),
        mcp_registry=FakeMCPRegistry(),
    )
    state = EvalResponse.model_validate({
        "features": {
            ExperimentName.MANAGED_SHELL_TOOLS.value: {
                "defaultValue": "legacy",
                "rules": [{"force": "managed", "tracks": []}],
            }
        }
    })
    agent.experiment_manager.hydrate(state)
    factory = AgentRuntimeFactory()

    if derived_kind == "fork":
        derived = await factory.fork(agent, None)
    else:
        derived = await factory.create_child(agent, "explore")

    try:
        assert derived.experiment_manager.export_state() == state
        assert (
            derived.experiment_manager.get_variant(ExperimentName.MANAGED_SHELL_TOOLS)
            == "managed"
        )
    finally:
        await derived.aclose()
        await agent.aclose()


@pytest.mark.parametrize("derived_kind", ["fork", "child"])
@pytest.mark.asyncio
async def test_derived_runtime_holds_the_shared_session_lease(
    derived_kind: str, tmp_path: Path
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    agent = AgentLoop(
        FakeConfigOrchestrator(config),
        agent_name=BuiltinAgentName.ASK,
        backend=FakeBackend(),
        mcp_registry=FakeMCPRegistry(),
    )
    factory = AgentRuntimeFactory()
    derived = (
        await factory.fork(agent, None)
        if derived_kind == "fork"
        else await factory.create_child(agent, "explore")
    )

    try:
        with pytest.raises(SessionBusyError):
            SessionLease(tmp_path, derived.session_id).acquire()
    finally:
        await derived.aclose()
        await agent.aclose()

    SessionLease(tmp_path, derived.session_id).acquire().release()


@pytest.mark.parametrize("derived_kind", ["fork", "child"])
@pytest.mark.asyncio
async def test_derived_runtime_clones_preconfigured_mcp_registry_before_init(
    derived_kind: str, tmp_path: Path
) -> None:
    registry = MCPRegistry(descriptor_cache_root=tmp_path / "descriptors")
    agent = AgentLoop(
        FakeConfigOrchestrator(build_test_vibe_config()),
        agent_name=BuiltinAgentName.ASK,
        backend=FakeBackend(),
        mcp_registry=registry,
    )
    factory = AgentRuntimeFactory()

    derived = (
        await factory.fork(agent, None)
        if derived_kind == "fork"
        else await factory.create_child(agent, "explore")
    )

    try:
        assert derived.mcp_registry is not None
        assert derived.mcp_registry is not registry
        assert derived.mcp_registry._descriptor_cache_root == tmp_path / "descriptors"
    finally:
        await derived.aclose()
        await agent.aclose()


@pytest.mark.asyncio
async def test_child_hydrates_experiments_before_deferred_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = await _real_orchestrator()
    agent = AgentLoop(
        orchestrator,
        agent_name=BuiltinAgentName.ASK,
        backend=FakeBackend(),
        mcp_registry=FakeMCPRegistry(),
    )
    state = EvalResponse.model_validate({
        "features": {
            ExperimentName.MANAGED_SHELL_TOOLS.value: {
                "defaultValue": "legacy",
                "rules": [{"force": "managed", "tracks": []}],
            }
        }
    })
    agent.experiment_manager.hydrate(state)
    managed_at_deferred_start: list[bool] = []
    original_start = AgentLoop._start_deferred_init

    def start_deferred_init(loop: AgentLoop):
        managed_at_deferred_start.append(
            loop.experiment_manager.get_variant(ExperimentName.MANAGED_SHELL_TOOLS)
            == "managed"
        )
        return original_start(loop)

    monkeypatch.setattr(AgentLoop, "_start_deferred_init", start_deferred_init)
    child = await AgentRuntimeFactory().create_child(agent, "explore")

    try:
        await child.wait_until_ready()
        assert managed_at_deferred_start[0] is True
    finally:
        await child.aclose()
        await agent.aclose()
