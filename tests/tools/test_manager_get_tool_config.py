from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.tools.manager import ToolManager


@pytest.fixture
def config():
    return build_test_vibe_config(
        system_prompt_id="tests", include_project_context=False
    )


@pytest.fixture
def tool_manager(config):
    return ToolManager(lambda: config)


def test_returns_default_config_when_no_overrides(tool_manager):
    config = tool_manager.get_tool_config("bash")

    assert (
        type(config).__name__ == "BashToolConfig"
    )  # due to vibe's discover system isinstance would fail
    assert config.default_timeout == 300  # type: ignore[attr-defined]
    assert config.max_output_bytes == 16000  # type: ignore[attr-defined]
    assert config.permission == ToolPermission.ASK


def test_merges_user_overrides_with_defaults():
    vibe_config = build_test_vibe_config(
        system_prompt_id="tests",
        include_project_context=False,
        tools={"bash": BaseToolConfig(permission=ToolPermission.ALWAYS)},
    )
    manager = ToolManager(lambda: vibe_config)

    config = manager.get_tool_config("bash")

    assert (
        type(config).__name__ == "BashToolConfig"
    )  # due to vibe's discover system isinstance would fail
    assert config.permission == ToolPermission.ALWAYS
    assert config.default_timeout == 300  # type: ignore[attr-defined]


def test_preserves_tool_specific_fields_from_overrides():
    vibe_config = build_test_vibe_config(
        system_prompt_id="tests",
        include_project_context=False,
        tools={"bash": BaseToolConfig(permission=ToolPermission.ASK)},
    )
    vibe_config.tools["bash"].__pydantic_extra__ = {"default_timeout": 600}
    manager = ToolManager(lambda: vibe_config)

    config = manager.get_tool_config("bash")

    assert type(config).__name__ == "BashToolConfig"
    assert config.default_timeout == 600  # type: ignore[attr-defined]


def test_falls_back_to_base_config_for_unknown_tool(tool_manager):
    config = tool_manager.get_tool_config("nonexistent_tool")

    assert type(config) is BaseToolConfig
    assert config.permission == ToolPermission.ASK


class TestToolManagerFiltering:
    def test_enabled_tools_filters_to_only_enabled(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            enabled_tools=["bash", "grep"],
        )
        manager = ToolManager(lambda: vibe_config)

        tools = manager.available_tools
        assert len(tools) < len(manager._available)
        assert "bash" in tools
        assert "grep" in tools
        assert "read_file" not in tools
        assert "write_file" not in tools

    def test_disabled_tools_excludes_disabled(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            disabled_tools=["bash", "write_file"],
        )
        manager = ToolManager(lambda: vibe_config)

        tools = manager.available_tools
        assert len(tools) < len(manager._available)
        assert "bash" not in tools
        assert "write_file" not in tools
        assert "grep" in tools
        assert "read_file" in tools

    def test_enabled_tools_takes_precedence_over_disabled(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            enabled_tools=["bash"],
            disabled_tools=["bash"],  # Should be ignored
        )
        manager = ToolManager(lambda: vibe_config)

        tools = manager.available_tools
        assert len(tools) == 1
        assert "bash" in tools

    def test_glob_pattern_matching(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            disabled_tools=["*_file"],  # Matches read_file, write_file
        )
        manager = ToolManager(lambda: vibe_config)

        tools = manager.available_tools
        assert "read_file" not in tools
        assert "write_file" not in tools
        assert "bash" in tools
        assert "grep" in tools

    def test_regex_pattern_matching(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            enabled_tools=["re:^(bash|grep)$"],
        )
        manager = ToolManager(lambda: vibe_config)

        tools = manager.available_tools
        assert len(tools) == 2
        assert "bash" in tools
        assert "grep" in tools

    def test_case_insensitive_matching(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            enabled_tools=["BASH", "GREP"],
        )
        manager = ToolManager(lambda: vibe_config)

        tools = manager.available_tools
        assert "bash" in tools
        assert "grep" in tools

    def test_empty_enabled_tools_returns_all(self):
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests", include_project_context=False, enabled_tools=[]
        )
        manager = ToolManager(lambda: vibe_config)

        tools = manager.available_tools
        assert "bash" in tools
        assert "grep" in tools
        assert "read_file" in tools

    def test_tool_paths_with_file_and_directory(self, tmp_path: Path):
        """Should handle a mix of file and directory paths in tool_paths."""
        import sys

        # Create a directory with a tool
        tool_dir = tmp_path / "tools"
        tool_dir.mkdir()
        (tool_dir / "dir_tool.py").write_text("""
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from pydantic import BaseModel
from collections.abc import AsyncGenerator

class DirToolArgs(BaseModel):
    pass

class DirToolResult(BaseModel):
    pass

class DirTool(BaseTool[DirToolArgs, DirToolResult, BaseToolConfig, BaseToolState]):
    description = "Tool from directory"

    async def run(self, args, ctx=None):
        yield DirToolResult()
""")

        # Create a standalone tool file
        file_tool = tmp_path / "file_tool.py"
        file_tool.write_text("""
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from pydantic import BaseModel
from collections.abc import AsyncGenerator

class FileToolArgs(BaseModel):
    pass

class FileToolResult(BaseModel):
    pass

class FileTool(BaseTool[FileToolArgs, FileToolResult, BaseToolConfig, BaseToolState]):
    description = "Tool from file path"

    async def run(self, args, ctx=None):
        yield FileToolResult()
""")

        # Clean up any previously loaded modules
        to_remove = [k for k in sys.modules if "dir_tool" in k or "file_tool" in k]
        for k in to_remove:
            del sys.modules[k]

        vibe_config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            tool_paths=[tool_dir, file_tool],
        )
        manager = ToolManager(lambda: vibe_config)

        tools = manager.available_tools
        assert "dir_tool" in tools
        assert "file_tool" in tools


class TestToolManagerModuleReuse:
    """Tests for module reuse across ToolManager instances.

    When multiple ToolManager instances are created (e.g., main agent + subagent),
    they should reuse the same tool modules from sys.modules to preserve class identity.
    This prevents Pydantic validation errors when tool results from one agent
    are validated against types from another.
    """

    def test_multiple_managers_share_tool_classes(self):
        """Tool classes should be identical across multiple ToolManager instances."""
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests", include_project_context=False
        )

        manager1 = ToolManager(lambda: vibe_config)
        manager2 = ToolManager(lambda: vibe_config)

        # Get the same tool class from both managers
        todo_class1 = manager1.available_tools.get("todo")
        todo_class2 = manager2.available_tools.get("todo")

        assert todo_class1 is not None
        assert todo_class2 is not None
        # Class objects should be identical (same id), not just equal
        assert todo_class1 is todo_class2

    def test_tool_state_classes_are_identical(self):
        """Tool state classes should be identical across managers."""
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests", include_project_context=False
        )

        manager1 = ToolManager(lambda: vibe_config)
        manager2 = ToolManager(lambda: vibe_config)

        todo_class1 = manager1.available_tools["todo"]
        todo_class2 = manager2.available_tools["todo"]

        state_class1 = todo_class1._get_tool_state_class()
        state_class2 = todo_class2._get_tool_state_class()

        assert state_class1 is state_class2

    def test_tool_args_results_classes_are_identical(self):
        """Tool args and result classes should be identical across managers."""
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests", include_project_context=False
        )

        manager1 = ToolManager(lambda: vibe_config)
        manager2 = ToolManager(lambda: vibe_config)

        todo_class1 = manager1.available_tools["todo"]
        todo_class2 = manager2.available_tools["todo"]

        args1, result1 = todo_class1._get_tool_args_results()
        args2, result2 = todo_class2._get_tool_args_results()

        assert args1 is args2
        assert result1 is result2

    def test_tool_instances_are_isolated(self):
        """Tool instances should be separate even though classes are shared.

        This ensures subagents have isolated state (e.g., separate todo lists)
        while still sharing class definitions for Pydantic validation.
        """
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests", include_project_context=False
        )

        manager1 = ToolManager(lambda: vibe_config)
        manager2 = ToolManager(lambda: vibe_config)

        # Get tool instances from each manager
        tool1 = manager1.get("todo")
        tool2 = manager2.get("todo")

        # Instances should be different objects
        assert tool1 is not tool2

        # State should be different objects
        assert tool1.state is not tool2.state

        # Verify state is truly isolated by modifying one
        from vibe.core.tools.builtins.todo import TodoItem

        tool1.state.todos = [TodoItem(id="1", content="test")]
        assert len(tool1.state.todos) == 1
        assert len(tool2.state.todos) == 0  # Unaffected!

    def test_class_shared_but_instances_isolated(self):
        """Classes must be shared (for validation) but instances isolated (for state)."""
        vibe_config = build_test_vibe_config(
            system_prompt_id="tests", include_project_context=False
        )

        manager1 = ToolManager(lambda: vibe_config)
        manager2 = ToolManager(lambda: vibe_config)

        tool1 = manager1.get("todo")
        tool2 = manager2.get("todo")

        # Classes are shared (same object)
        assert type(tool1) is type(tool2)
        assert type(tool1.state) is type(tool2.state)

        # But instances are different
        assert tool1 is not tool2
        assert tool1.state is not tool2.state

    def test_different_files_same_stem_get_different_modules(self, tmp_path: Path):
        """Tools with same stem but different paths should be separate modules.

        This ensures user tools can override builtins - a custom todo.py in
        ~/.config/vibe/tools/ should override the builtin todo.py.
        """
        import sys

        # Create two tool files with the same stem but different content
        dir1 = tmp_path / "tools1"
        dir2 = tmp_path / "tools2"
        dir1.mkdir()
        dir2.mkdir()

        tool_code_v1 = """
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from pydantic import BaseModel
from collections.abc import AsyncGenerator

class DummyArgs(BaseModel):
    value: str

class DummyResult(BaseModel):
    version: str = "v1"

class DummyTool(BaseTool[DummyArgs, DummyResult, BaseToolConfig, BaseToolState]):
    description = "Dummy tool v1"

    async def run(self, args: DummyArgs, ctx=None) -> AsyncGenerator[DummyResult, None]:
        yield DummyResult(version="v1")
"""

        tool_code_v2 = """
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from pydantic import BaseModel
from collections.abc import AsyncGenerator

class DummyArgs(BaseModel):
    value: str

class DummyResult(BaseModel):
    version: str = "v2"

class DummyTool(BaseTool[DummyArgs, DummyResult, BaseToolConfig, BaseToolState]):
    description = "Dummy tool v2"

    async def run(self, args: DummyArgs, ctx=None) -> AsyncGenerator[DummyResult, None]:
        yield DummyResult(version="v2")
"""

        (dir1 / "dummy.py").write_text(tool_code_v1)
        (dir2 / "dummy.py").write_text(tool_code_v2)

        # Clean up any previously loaded dummy modules
        to_remove = [k for k in sys.modules if "dummy" in k]
        for k in to_remove:
            del sys.modules[k]

        # Load tools from both directories (dir2 comes after, should override)
        classes = list(ToolManager._iter_tool_classes([dir1, dir2]))
        dummy_classes = [c for c in classes if "dummy" in c.get_name().lower()]

        # Should have 2 separate classes (from different modules)
        assert len(dummy_classes) == 2

        # They should be different class objects
        assert dummy_classes[0] is not dummy_classes[1]

        # When put in a dict (like _available), the second one wins
        available = {c.get_name(): c for c in classes}
        final_class = available.get("dummy_tool")
        assert final_class is not None
        assert final_class.description == "Dummy tool v2"
