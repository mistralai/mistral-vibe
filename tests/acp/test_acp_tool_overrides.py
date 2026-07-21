from __future__ import annotations

import pytest

from tests.acp.test_initialize import build_acp_agent_loop


def _expected_override_paths(agent) -> set:
    return {p.expanduser().resolve() for p in agent._get_acp_tool_overrides()}


@pytest.mark.asyncio
async def test_acp_tool_overrides_present_after_load() -> None:
    agent = build_acp_agent_loop()

    orchestrator = await agent._load_orchestrator()

    assert _expected_override_paths(agent) <= set(orchestrator.config.tool_paths)


@pytest.mark.asyncio
async def test_acp_tool_overrides_survive_reload() -> None:
    agent = build_acp_agent_loop()
    orchestrator = await agent._load_orchestrator()

    # Every set_field (model/thinking/... change) reloads the orchestrator
    # internally; simulate that reload directly.
    await orchestrator.reload()

    assert _expected_override_paths(agent) <= set(orchestrator.config.tool_paths)


@pytest.mark.asyncio
async def test_acp_tool_overrides_not_duplicated_across_reloads() -> None:
    agent = build_acp_agent_loop()
    orchestrator = await agent._load_orchestrator()

    await orchestrator.reload()
    await orchestrator.reload()

    tool_paths = list(orchestrator.config.tool_paths)
    for path in _expected_override_paths(agent):
        assert tool_paths.count(path) == 1
