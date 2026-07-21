from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import tomli_w

from tests.acp.test_initialize import build_acp_agent_loop
from vibe.acp.acp_agent_loop import NON_INTERACTIVE_DISABLED_TOOLS


def _set_disabled_tools(config_dir: Path, tools: list[str]) -> None:
    config_file = config_dir / "config.toml"
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    data["disabled_tools"] = tools
    config_file.write_text(tomli_w.dumps(data), encoding="utf-8")


@pytest.mark.asyncio
async def test_non_interactive_tools_added_after_load() -> None:
    agent = build_acp_agent_loop()

    orchestrator = await agent._load_orchestrator()

    assert set(NON_INTERACTIVE_DISABLED_TOOLS) <= set(
        orchestrator.config.disabled_tools
    )


@pytest.mark.asyncio
async def test_non_interactive_tools_preserve_user_disabled_tools(
    config_dir: Path,
) -> None:
    _set_disabled_tools(config_dir, ["task"])
    agent = build_acp_agent_loop()

    orchestrator = await agent._load_orchestrator()

    disabled = set(orchestrator.config.disabled_tools)
    assert "task" in disabled
    assert set(NON_INTERACTIVE_DISABLED_TOOLS) <= disabled


@pytest.mark.asyncio
async def test_non_interactive_tools_survive_reload() -> None:
    agent = build_acp_agent_loop()
    orchestrator = await agent._load_orchestrator()

    await orchestrator.reload()

    assert set(NON_INTERACTIVE_DISABLED_TOOLS) <= set(
        orchestrator.config.disabled_tools
    )
