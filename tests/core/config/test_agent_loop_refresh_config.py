from __future__ import annotations

import asyncio

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_config,
    stub_config_reload,
)
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.agent_loop import AgentLoop
from vibe.core.agent_loop._loop import _ActiveTurn
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import MCPHttp, MCPOAuth, MCPStreamableHttp
from vibe.core.tools.mcp import AuthStatus


@pytest.mark.asyncio
async def test_refresh_config_reconciles_mcp_registry_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kept = MCPHttp(name="kept", transport="http", url="http://kept:1")
    removed = MCPHttp(name="removed", transport="http", url="http://removed:1")
    registry = FakeMCPRegistry()
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(mcp_servers=[kept, removed]),
        mcp_registry=registry,
    )
    refreshed_config = build_test_vibe_config(mcp_servers=[kept])

    stub_config_reload(monkeypatch, refreshed_config)
    await agent_loop.refresh_config()

    assert registry.status() == {"kept": AuthStatus.STATIC}


@pytest.mark.asyncio
async def test_refresh_config_preserves_forced_bypass_tool_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A session-level forced bypass (e.g. CLI --yolo) must survive a config
    # reload, which reads bypass_tool_permissions=False back from disk.
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(bypass_tool_permissions=True),
        force_bypass_tool_permissions=True,
    )
    assert agent_loop.bypass_tool_permissions is True

    refreshed_config = build_test_vibe_config(bypass_tool_permissions=False)
    stub_config_reload(monkeypatch, refreshed_config)
    await agent_loop.refresh_config()

    assert agent_loop.bypass_tool_permissions is True


@pytest.mark.asyncio
async def test_refresh_config_drops_disk_bypass_when_not_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without a forced override, a disk-originated bypass value follows the
    # reloaded config so the user can turn it off by editing their config.
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(bypass_tool_permissions=True)
    )
    assert agent_loop.bypass_tool_permissions is True

    refreshed_config = build_test_vibe_config(bypass_tool_permissions=False)
    stub_config_reload(monkeypatch, refreshed_config)
    await agent_loop.refresh_config()

    assert agent_loop.bypass_tool_permissions is False


@pytest.mark.asyncio
async def test_runtime_policy_reflects_bypass_after_in_session_agent_switch() -> None:
    # Launching without --auto-approve then switching to auto-approve in-session
    # must propagate bypass_tool_permissions=True into child loop construction
    # via runtime_policy, not just into the parent's own permission check.
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(), agent_name=BuiltinAgentName.ASK
    )
    assert agent_loop.bypass_tool_permissions is False
    assert agent_loop.runtime_policy.force_bypass_tool_permissions is False

    await agent_loop.switch_agent(BuiltinAgentName.AUTO_APPROVE)

    assert agent_loop.bypass_tool_permissions is True
    assert agent_loop.runtime_policy.force_bypass_tool_permissions is True


@pytest.mark.asyncio
async def test_refresh_config_does_not_guess_oauth_authorization_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth = MCPStreamableHttp(
        name="linear",
        transport="streamable-http",
        url="https://mcp.example.com/mcp",
        auth=MCPOAuth(type="oauth", scopes=["read"]),
    )
    registry = FakeMCPRegistry()
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(mcp_servers=[]), mcp_registry=registry
    )
    refreshed_config = build_test_vibe_config(mcp_servers=[oauth])

    stub_config_reload(monkeypatch, refreshed_config)
    await agent_loop.refresh_config()

    # Config synchronization owns active membership only. Discovery resolves the
    # server through the authorization provider and records NEEDS_AUTH when needed.
    assert registry.status() == {"linear": AuthStatus.OK}
    assert registry.needs_auth == set()


@pytest.mark.asyncio
async def test_refresh_config_creates_mcp_registry_when_first_server_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Startup with no MCP servers leaves the registry uninitialised. Adding the
    # first server via `/mcp add` calls refresh_config, which must materialise the
    # registry so the follow-up `/mcp login` can find it.
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(mcp_servers=[]),
        mcp_registry=None,
        defer_heavy_init=True,
    )
    await agent_loop.wait_until_ready()
    assert agent_loop.mcp_registry is None

    registry = FakeMCPRegistry()
    monkeypatch.setattr(
        AgentLoop, "_create_mcp_registry", staticmethod(lambda: registry)
    )
    oauth = MCPStreamableHttp(
        name="linear",
        transport="streamable-http",
        url="https://mcp.example.com/mcp",
        auth=MCPOAuth(type="oauth", scopes=["read"]),
    )
    refreshed_config = build_test_vibe_config(mcp_servers=[oauth])

    stub_config_reload(monkeypatch, refreshed_config)
    await agent_loop.refresh_config()

    assert agent_loop.mcp_registry is registry
    assert registry.status() == {"linear": AuthStatus.OK}
    assert registry.needs_auth == set()


@pytest.mark.asyncio
async def test_switch_agent_closes_previous_backend_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: each in-session reload (agent/model/config switch) dropped the
    # previous backend -- and its httpx connection pool -- without closing it,
    # leaking connections until PoolTimeout in a long-lived session.
    close_event = asyncio.Event()

    class _RecordingBackend(FakeBackend):
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            close_event.set()
            return await super().__aexit__(exc_type, exc_val, exc_tb)

    def _factory(self: AgentLoop, config=None) -> _RecordingBackend:
        return _RecordingBackend()

    monkeypatch.setattr(AgentLoop, "backend_factory", _factory)

    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(), agent_name=BuiltinAgentName.ASK
    )
    previous_backend = agent_loop.backend

    await agent_loop.switch_agent(BuiltinAgentName.AUTO_APPROVE)

    # The previous backend's pool is closed in the background after the swap.
    await asyncio.wait_for(close_event.wait(), timeout=2.0)
    assert agent_loop.backend is not previous_backend


@pytest.mark.asyncio
async def test_reload_during_active_turn_defers_backend_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: a side-channel reload (session/agent/update) can run while a
    # turn streams through the old backend. Closing that backend's pool then
    # would abort the in-flight stream, so the close must be deferred until the
    # turn ends (drained at the start of the next turn).
    close_event = asyncio.Event()

    class _RecordingBackend(FakeBackend):
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            close_event.set()
            return await super().__aexit__(exc_type, exc_val, exc_tb)

    def _factory(self: AgentLoop, config=None) -> _RecordingBackend:
        return _RecordingBackend()

    monkeypatch.setattr(AgentLoop, "backend_factory", _factory)

    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(), agent_name=BuiltinAgentName.ASK
    )
    previous_backend = agent_loop.backend
    # Simulate an in-flight turn streaming through the current backend.
    agent_loop._active_turn = _ActiveTurn()

    await agent_loop.switch_agent(BuiltinAgentName.AUTO_APPROVE)

    assert agent_loop.backend is not previous_backend
    # Still in flight: the old backend must NOT be closed yet.
    assert not close_event.is_set()
    assert previous_backend in agent_loop._backends_to_close

    # The turn ends; the next turn drains the deferred close.
    agent_loop._active_turn = None
    agent_loop._drain_pending_backend_closes()
    await asyncio.wait_for(close_event.wait(), timeout=2.0)
    assert agent_loop._backends_to_close == []


@pytest.mark.asyncio
async def test_reload_defers_parent_backend_close_while_subagent_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A subagent's MCP sampling streams through the *parent's* backend, not the
    # child's own: the parent lends its ``sampling_callback`` (built over
    # ``backend_getter=lambda: self.backend``) to the ``task`` tool, which the
    # subagent's MCP tools invoke. The subagent runs as a task inside the
    # parent's turn, so the parent's ``_active_turn`` is set for the subagent's
    # whole lifetime. A side-channel reload (session/agent/update) mid-subagent
    # must therefore defer the parent's old backend close -- closing it would
    # tear down the subagent's in-flight sampling stream. Drained once the
    # parent's turn (and thus the subagent) ends. Locks the invariant the
    # deferral relies on; breaks if a subagent is ever detached to outlive the
    # parent's turn.
    close_event = asyncio.Event()

    class _RecordingBackend(FakeBackend):
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            close_event.set()
            return await super().__aexit__(exc_type, exc_val, exc_tb)

    def _factory(self: AgentLoop, config=None) -> _RecordingBackend:
        return _RecordingBackend()

    monkeypatch.setattr(AgentLoop, "backend_factory", _factory)

    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(), agent_name=BuiltinAgentName.ASK
    )
    parent_backend = agent_loop.backend
    # The subagent reaches the parent backend through the same closure the
    # sampling handler uses (backend_getter=lambda: self.backend); capture that
    # view to represent the backend the subagent's sampling stream holds.
    sampling_backend_getter = lambda: agent_loop.backend
    subagent_sampling_backend = sampling_backend_getter()
    assert subagent_sampling_backend is parent_backend

    # Subagent runs within the parent's turn -- its presence keeps the turn active.
    agent_loop._active_turn = _ActiveTurn()

    await agent_loop.switch_agent(BuiltinAgentName.AUTO_APPROVE)

    # Parent backend swapped, but the subagent's sampling stream still holds the
    # old one; closing it now would abort that stream.
    assert agent_loop.backend is not parent_backend
    assert not close_event.is_set()
    assert parent_backend in agent_loop._backends_to_close
    # The subagent's view of the backend is unchanged -- its stream survives.
    assert subagent_sampling_backend is parent_backend

    # The subagent (and parent turn) ends; the next turn drains the deferred close.
    agent_loop._active_turn = None
    agent_loop._drain_pending_backend_closes()
    await asyncio.wait_for(close_event.wait(), timeout=2.0)
    assert agent_loop._backends_to_close == []
