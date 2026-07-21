from __future__ import annotations

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_config,
    stub_config_reload,
)
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
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
async def test_refresh_config_does_not_mark_undiscovered_oauth_server_ok(
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

    assert registry.status() == {"linear": AuthStatus.NEEDS_AUTH}
