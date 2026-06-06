from __future__ import annotations

from tests.conftest import build_test_vibe_config
from vibe.acp.acp_agent_loop import (
    NON_INTERACTIVE_DISABLED_TOOLS,
    _merge_non_interactive_disabled_tools,
)


def test_merge_preserves_toml_disabled_tools():
    config = build_test_vibe_config(disabled_tools=["task"])

    _merge_non_interactive_disabled_tools(config)

    assert "task" in config.disabled_tools
    for tool in NON_INTERACTIVE_DISABLED_TOOLS:
        assert tool in config.disabled_tools


def test_merge_deduplicates():
    config = build_test_vibe_config(disabled_tools=["ask_user_question", "task"])

    _merge_non_interactive_disabled_tools(config)

    assert config.disabled_tools.count("ask_user_question") == 1
    assert "task" in config.disabled_tools
    assert "exit_plan_mode" in config.disabled_tools


def test_merge_on_empty_disabled_tools():
    config = build_test_vibe_config()

    _merge_non_interactive_disabled_tools(config)

    assert set(config.disabled_tools) == set(NON_INTERACTIVE_DISABLED_TOOLS)
