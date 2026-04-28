from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from vibe.core.tools.builtins.bash import Bash
from vibe.core.tools.manager import ToolManager, NoSuchToolError


@pytest.fixture
def config():
    return build_test_vibe_config(
        system_prompt_id="tests", include_project_context=False
    )


@pytest.fixture
def tool_manager(config):
    return ToolManager(lambda: config)


class TestGetToolInstance:
    """Tests for ToolManager.get() method."""

    def test_returns_tool_instance(self, tool_manager):
        tool = tool_manager.get("bash")
        assert isinstance(tool, Bash)

    def test_returns_same_instance_on_multiple_calls(self, tool_manager):
        tool1 = tool_manager.get("bash")
        tool2 = tool_manager.get("bash")
        assert tool1 is tool2

    def test_raises_error_for_unknown_tool(self, tool_manager):
        with pytest.raises(NoSuchToolError, match="Unknown tool: nonexistent"):
            tool_manager.get("nonexistent")

    def test_config_is_accessible_via_property(self, tool_manager):
        tool = tool_manager.get("bash")
        config = tool.config
        assert isinstance(config, BaseToolConfig)
        assert hasattr(config, "max_output_bytes")

    def test_config_property_returns_correct_type(self, tool_manager):
        tool = tool_manager.get("bash")
        config = tool.config
        assert type(config).__name__ == "BashToolConfig"

    def test_config_getter_is_callable(self, tool_manager):
        tool = tool_manager.get("bash")
        assert callable(tool._config_getter)

    def test_config_refreshes_on_each_access(self, tool_manager, config):
        tool = tool_manager.get("bash")
        config1 = tool.config

        # Modify config
        config.tools = {"bash": {"default_timeout": 600}}
        config2 = tool.config

        assert config1 is not config2
        assert config2.default_timeout == 600  # type: ignore[attr-defined]


class TestGetCaching:
    """Tests for tool instance caching behavior."""

    def test_caches_first_call(self, tool_manager):
        tool1 = tool_manager.get("bash")
        tool2 = tool_manager.get("bash")
        assert tool1 is tool2

    def test_reset_all_clears_cache(self, tool_manager):
        tool1 = tool_manager.get("bash")
        tool_manager.reset_all()
        tool2 = tool_manager.get("bash")
        assert tool1 is not tool2

    def test_invalidate_tool_removes_from_cache(self, tool_manager):
        tool1 = tool_manager.get("bash")
        tool_manager.invalidate_tool("bash")
        tool2 = tool_manager.get("bash")
        assert tool1 is not tool2


class TestGetLspTools:
    """Tests for LSP tool handling (tools with _tool suffix)."""

    def test_lsp_tool_returns_stripped_name_instance(self, tool_manager):
        # Mock an LSP tool scenario
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.get_name.return_value = "lsp_completion"

        # Register in both _available and _instances
        mock_class = MagicMock()
        mock_class.get_name.return_value = "lsp_completion"
        tool_manager._available["lsp_completion"] = mock_class
        tool_manager._instances["lsp_completion"] = mock_tool

        # Request with _tool suffix - should return base name instance
        result = tool_manager.get("lsp_completion_tool")

        assert result is mock_tool

    def test_lsp_tool_creates_new_if_base_not_registered(self, tool_manager):
        # Mock a tool class
        mock_class = MagicMock()
        mock_instance = MagicMock()
        mock_class.from_config.return_value = mock_instance
        mock_class.get_name.return_value = "test_tool"

        tool_manager._available["test_tool_tool"] = mock_class

        result = tool_manager.get("test_tool_tool")

        assert result is mock_instance
        mock_class.from_config.assert_called_once()


class TestGetWithPluginTools:
    """Tests for tools registered by plugins."""

    def test_returns_pre_registered_instance(self, tool_manager):
        mock_tool = MagicMock(spec=BaseTool)
        tool_manager._instances["plugin_tool"] = mock_tool

        result = tool_manager.get("plugin_tool")

        assert result is mock_tool

    def test_does_not_create_new_instance_for_pre_registered(self, tool_manager):
        mock_tool = MagicMock(spec=BaseTool)
        tool_manager._instances["plugin_tool"] = mock_tool

        # Even if available, should return pre-registered
        mock_class = MagicMock()
        tool_manager._available["plugin_tool"] = mock_class

        result = tool_manager.get("plugin_tool")

        assert result is mock_tool
        mock_class.from_config.assert_not_called()


class TestGetConfigCallable:
    """Tests to ensure config getter is properly callable, not a config object."""

    def test_config_getter_is_lambda_not_object(self, tool_manager):
        tool = tool_manager.get("bash")
        # The _config_getter should be callable (lambda), not a config object
        assert callable(tool._config_getter)
        # Calling it should return a config object
        config = tool._config_getter()
        assert isinstance(config, BaseToolConfig)

    def test_tool_works_after_fix(self, tool_manager):
        """Test that the original bug (config object not callable) is fixed."""
        tool = tool_manager.get("bash")
        # This should not raise: 'BashToolConfig' object is not callable
        try:
            config = tool.config
            assert config is not None
        except TypeError as e:
            if "not callable" in str(e):
                pytest.fail("Bug not fixed: config object is not callable")
            raise

    def test_multiple_tools_have_independent_configs(self, tool_manager):
        bash_tool = tool_manager.get("bash")
        # Get another tool if available
        available = tool_manager.available_tools
        if "read_file" in available:
            read_tool = tool_manager.get("read_file")
            assert bash_tool._config_getter is not read_tool._config_getter


class TestGetThreadSafety:
    """Tests for thread-safety of get() method."""

    def test_concurrent_get_calls(self, tool_manager):
        import threading

        tools = []
        errors = []
        barrier = threading.Barrier(4)

        def get_tool():
            try:
                barrier.wait()
                tool = tool_manager.get("bash")
                tools.append(tool)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_tool) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors occurred: {errors}"
        assert len(tools) == 4
        # All should be the same instance due to caching
        assert all(t is tools[0] for t in tools)


class TestGetToolWithCustomConfig:
    """Tests for get() with custom tool configurations."""

    def test_uses_user_overrides(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            tools={"bash": {"default_timeout": 600}},
        )
        manager = ToolManager(lambda: vibe_config)

        tool = manager.get("bash")
        config = tool.config

        assert config.default_timeout == 600  # type: ignore[attr-defined]

    def test_custom_config_reflected_in_property(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            tools={"bash": {"max_output_bytes": 32000}},
        )
        manager = ToolManager(lambda: vibe_config)

        tool = manager.get("bash")
        assert tool.config.max_output_bytes == 32000  # type: ignore[attr-defined]
