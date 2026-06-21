from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.config import MCPHttp, MCPOAuth, MCPStreamableHttp, VibeConfig
from vibe.core.tools.mcp import AuthStatus


def test_refresh_config_reconciles_mcp_registry_status(
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

    monkeypatch.setattr(VibeConfig, "load", staticmethod(lambda: refreshed_config))
    agent_loop.refresh_config()

    assert registry.status() == {"kept": AuthStatus.STATIC}


def test_refresh_config_does_not_mark_undiscovered_oauth_server_ok(
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

    monkeypatch.setattr(VibeConfig, "load", staticmethod(lambda: refreshed_config))
    agent_loop.refresh_config()

    assert registry.status() == {"linear": AuthStatus.NEEDS_AUTH}
