from __future__ import annotations

import pytest

from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.tools.manager import ToolManager


@pytest.fixture
def config():
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        system_prompt_id="tests",
        include_project_context=False,
    )


@pytest.fixture
def tool_manager(config):
    return ToolManager(config)


def test_returns_default_config_when_no_overrides(tool_manager):
    config = tool_manager.get_tool_config("bash")

    assert (
        type(config).__name__ == "BashToolConfig"
    )  # due to vibe's discover system isinstance would fail
    assert config.default_timeout == 30  # type: ignore[attr-defined]
    assert config.max_output_bytes == 16000  # type: ignore[attr-defined]
    assert config.permission == ToolPermission.ASK


def test_merges_user_overrides_with_defaults():
    vibe_config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        system_prompt_id="tests",
        include_project_context=False,
        tools={"bash": BaseToolConfig(permission=ToolPermission.ALWAYS)},
    )
    manager = ToolManager(vibe_config)

    config = manager.get_tool_config("bash")

    assert (
        type(config).__name__ == "BashToolConfig"
    )  # due to vibe's discover system isinstance would fail
    assert config.permission == ToolPermission.ALWAYS
    assert config.default_timeout == 30  # type: ignore[attr-defined]


def test_preserves_tool_specific_fields_from_overrides():
    vibe_config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        system_prompt_id="tests",
        include_project_context=False,
        tools={"bash": BaseToolConfig(permission=ToolPermission.ASK)},
    )
    vibe_config.tools["bash"].__pydantic_extra__ = {"default_timeout": 600}
    manager = ToolManager(vibe_config)

    config = manager.get_tool_config("bash")

    assert type(config).__name__ == "BashToolConfig"
    assert config.default_timeout == 600  # type: ignore[attr-defined]


def test_falls_back_to_base_config_for_unknown_tool(tool_manager):
    config = tool_manager.get_tool_config("nonexistent_tool")

    assert type(config) is BaseToolConfig
    assert config.permission == ToolPermission.ASK


def test_applies_workdir_from_vibe_config(tmp_path):
    vibe_config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        system_prompt_id="tests",
        include_project_context=False,
        workdir=tmp_path,
    )
    manager = ToolManager(vibe_config)

    config = manager.get_tool_config("bash")

    assert config.workdir == tmp_path
    assert config.effective_workdir == tmp_path


def test_default_tool_permission_applies_when_no_tool_config():
    """Test that default_tool_permission applies when no explicit tool config exists."""
    vibe_config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        system_prompt_id="tests",
        include_project_context=False,
        default_tool_permission=ToolPermission.ASK_TIME,
    )
    manager = ToolManager(vibe_config)

    # read_file doesn't have explicit config, should use default
    config = manager.get_tool_config("read_file")
    assert config.permission == ToolPermission.ASK_TIME


def test_default_tool_permission_does_not_override_explicit_config():
    """Test that explicit tool config takes precedence over default_tool_permission."""
    vibe_config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        system_prompt_id="tests",
        include_project_context=False,
        default_tool_permission=ToolPermission.ASK_TIME,
        tools={"read_file": BaseToolConfig(permission=ToolPermission.ALWAYS)},
    )
    manager = ToolManager(vibe_config)

    # read_file has explicit config, should use that instead of default
    config = manager.get_tool_config("read_file")
    assert config.permission == ToolPermission.ALWAYS


def test_default_tool_permission_with_ask_iterations():
    """Test that default_tool_permission works with ask-iterations."""
    vibe_config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        system_prompt_id="tests",
        include_project_context=False,
        default_tool_permission=ToolPermission.ASK_ITERATIONS,
    )
    manager = ToolManager(vibe_config)

    # grep doesn't have explicit config, should use default
    config = manager.get_tool_config("grep")
    assert config.permission == ToolPermission.ASK_ITERATIONS


def test_default_tool_permission_none_uses_tool_default():
    """Test that when default_tool_permission is None, tool class default is used."""
    vibe_config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        system_prompt_id="tests",
        include_project_context=False,
        default_tool_permission=None,
    )
    manager = ToolManager(vibe_config)

    # read_file defaults to ALWAYS in its class
    config = manager.get_tool_config("read_file")
    assert config.permission == ToolPermission.ALWAYS
