from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server.mcp_catalog import create_sessionless_mcp_catalog
from vibe.app_server.protocol import MCPAddParams, MCPRemoveParams, MCPRemoveResponse
from vibe.core.config import build_user_config_orchestrator


@pytest.mark.asyncio
async def test_sessionless_remove_on_a_fresh_connection_clears_oauth_server(
    config_dir: Path,
) -> None:
    catalog = create_sessionless_mcp_catalog(build_user_config_orchestrator)
    await catalog.dispatch(
        "mcp_catalog/add",
        MCPAddParams(url="https://example.invalid/mcp", name="example"),
    )
    assert "example" in (config_dir / "config.toml").read_text()

    fresh_catalog = create_sessionless_mcp_catalog(build_user_config_orchestrator)
    result = await fresh_catalog.dispatch(
        "mcp_catalog/remove", MCPRemoveParams(name="example")
    )

    assert isinstance(result, MCPRemoveResponse)
    assert result.removed
    assert result.runtime is None
    assert "example" not in (config_dir / "config.toml").read_text()
    repeated = await fresh_catalog.dispatch(
        "mcp_catalog/remove", MCPRemoveParams(name="example")
    )
    assert isinstance(repeated, MCPRemoveResponse)
    assert not repeated.removed
