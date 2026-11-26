from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.config import MCPStdio, VibeConfigSchema
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.mcp_servers import persist_stdio_mcp_server
from vibe.core.tools.mcp_settings import persist_mcp_toggle


@pytest.mark.asyncio
async def test_persist_mcp_toggle_blocked_when_mcp_disabled(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    orchestrator = load_orchestrator(build_config(enable_mcp=False, mcp_servers=[]))

    with pytest.raises(ValueError, match="disabled by your administrator"):
        await persist_mcp_toggle(
            orchestrator, name="linear", is_connector=False, disabled=True
        )


@pytest.mark.asyncio
async def test_persist_mcp_toggle_connector_unaffected_by_mcp_flag(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    """Connector toggles are gated by enable_connectors, not enable_mcp."""
    orchestrator = load_orchestrator(build_config(enable_mcp=False))

    # Should not raise — connector toggle is a separate gate.
    await persist_mcp_toggle(
        orchestrator, name="slack", is_connector=True, disabled=True
    )


@pytest.mark.asyncio
async def test_persist_mcp_toggle_keeps_a_server_another_session_added(
    config_dir: Path,
) -> None:
    def stdio(name: str) -> MCPStdio:
        return MCPStdio(name=name, transport="stdio", command="python")

    await persist_stdio_mcp_server(await build_default_orchestrator(), stdio("srv1"))
    first = await build_default_orchestrator()
    await persist_stdio_mcp_server(await build_default_orchestrator(), stdio("srv2"))

    await persist_mcp_toggle(first, name="srv1", is_connector=False, disabled=True)

    with (config_dir / "config.toml").open("rb") as file:
        servers = tomllib.load(file)["mcp_servers"]
    assert [(s["name"], s.get("disabled", False)) for s in servers] == [
        ("srv1", True),
        ("srv2", False),
    ]


@pytest.mark.asyncio
async def test_persist_mcp_toggle_skips_the_write_for_an_unpersisted_server(
    config_dir: Path,
) -> None:
    config_file = config_dir / "config.toml"
    before = config_file.read_bytes()

    await persist_mcp_toggle(
        await build_default_orchestrator(),
        name="missing",
        is_connector=False,
        disabled=True,
    )

    assert config_file.read_bytes() == before
