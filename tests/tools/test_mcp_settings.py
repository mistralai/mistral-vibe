from __future__ import annotations

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.config import VibeConfigSchema
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
