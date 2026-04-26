"""Tests for multi-scope registry, dev plugins, and plugin picker widget."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest
import tomli_w

from tests.core.plugins.conftest import make_plugin
from vibe.core.plugins.models import PluginEntry, PluginScope, PluginSource
from vibe.core.plugins.registry import PluginRegistryManager

_skip_symlink = pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("CI"),
    reason="Symlinks require elevated privileges on Windows",
)


class TestMultiScopeRegistry:
    """Tests for multi-scope (USER/PROJECT/LOCAL) registry features."""

    def test_register_in_project_scope(
        self, multi_scope_registry: PluginRegistryManager, project_plugins_dir: Path
    ) -> None:
        entry = PluginEntry(
            name="proj-plugin", version="1.0.0", source=PluginSource.GIT
        )
        multi_scope_registry.register(entry, scope=PluginScope.PROJECT)
        plugins = multi_scope_registry.get_all_plugins()
        assert "proj-plugin" in plugins

    def test_register_in_local_scope(
        self, multi_scope_registry: PluginRegistryManager, local_plugins_dir: Path
    ) -> None:
        entry = PluginEntry(
            name="local-plugin", version="1.0.0", source=PluginSource.LOCAL
        )
        multi_scope_registry.register(entry, scope=PluginScope.LOCAL)
        plugins = multi_scope_registry.get_all_plugins()
        assert "local-plugin" in plugins

    def test_user_scope_overrides_project(
        self, multi_scope_registry: PluginRegistryManager
    ) -> None:
        multi_scope_registry.register(
            PluginEntry(name="shared", version="1.0.0", source=PluginSource.GIT),
            scope=PluginScope.PROJECT,
        )
        multi_scope_registry.register(
            PluginEntry(name="shared", version="2.0.0", source=PluginSource.GIT),
            scope=PluginScope.USER,
        )
        plugins = multi_scope_registry.get_all_plugins()
        assert plugins["shared"].version == "2.0.0"

    def test_local_overrides_project(
        self, multi_scope_registry: PluginRegistryManager
    ) -> None:
        multi_scope_registry.register(
            PluginEntry(name="shared", version="1.0.0", source=PluginSource.GIT),
            scope=PluginScope.PROJECT,
        )
        multi_scope_registry.register(
            PluginEntry(name="shared", version="3.0.0", source=PluginSource.GIT),
            scope=PluginScope.LOCAL,
        )
        plugins = multi_scope_registry.get_all_plugins()
        assert plugins["shared"].version == "3.0.0"

    def test_user_overrides_local(
        self, multi_scope_registry: PluginRegistryManager
    ) -> None:
        multi_scope_registry.register(
            PluginEntry(name="shared", version="3.0.0", source=PluginSource.GIT),
            scope=PluginScope.LOCAL,
        )
        multi_scope_registry.register(
            PluginEntry(name="shared", version="4.0.0", source=PluginSource.GIT),
            scope=PluginScope.USER,
        )
        plugins = multi_scope_registry.get_all_plugins()
        assert plugins["shared"].version == "4.0.0"

    def test_get_all_plugins_with_scope(
        self, multi_scope_registry: PluginRegistryManager
    ) -> None:
        multi_scope_registry.register(
            PluginEntry(name="u-plugin", version="1.0.0", source=PluginSource.GIT),
            scope=PluginScope.USER,
        )
        multi_scope_registry.register(
            PluginEntry(name="p-plugin", version="1.0.0", source=PluginSource.GIT),
            scope=PluginScope.PROJECT,
        )
        result = multi_scope_registry.get_all_plugins_with_scope()
        assert result["u-plugin"][0] == PluginScope.USER
        assert result["p-plugin"][0] == PluginScope.PROJECT

    def test_get_plugins_dir_for_scope(
        self,
        multi_scope_registry: PluginRegistryManager,
        plugins_dir: Path,
        project_plugins_dir: Path,
        local_plugins_dir: Path,
    ) -> None:
        assert (
            multi_scope_registry.get_plugins_dir_for_scope(PluginScope.USER)
            == plugins_dir
        )
        assert (
            multi_scope_registry.get_plugins_dir_for_scope(PluginScope.PROJECT)
            == project_plugins_dir
        )
        assert (
            multi_scope_registry.get_plugins_dir_for_scope(PluginScope.LOCAL)
            == local_plugins_dir
        )

    def test_register_unconfigured_scope_raises(
        self, registry: PluginRegistryManager
    ) -> None:
        entry = PluginEntry(name="test", version="1.0.0", source=PluginSource.GIT)
        with pytest.raises(KeyError, match="not configured"):
            registry.register(entry, scope=PluginScope.PROJECT)

    def test_get_plugin_dir_respects_scope(
        self, multi_scope_registry: PluginRegistryManager, project_plugins_dir: Path
    ) -> None:
        multi_scope_registry.register(
            PluginEntry(name="my-plugin", version="1.0.0", source=PluginSource.GIT),
            scope=PluginScope.PROJECT,
        )
        assert (
            multi_scope_registry.get_plugin_dir("my-plugin")
            == project_plugins_dir / "my-plugin"
        )

    def test_unregister_removes_from_correct_scope(
        self, multi_scope_registry: PluginRegistryManager
    ) -> None:
        multi_scope_registry.register(
            PluginEntry(name="to-remove", version="1.0.0", source=PluginSource.GIT),
            scope=PluginScope.PROJECT,
        )
        multi_scope_registry.unregister("to-remove")
        assert "to-remove" not in multi_scope_registry.get_all_plugins()


class TestDevPlugins:
    """Tests for add_dev_plugin and dev plugin override behavior."""

    def test_add_dev_plugin(
        self, registry: PluginRegistryManager, tmp_path: Path
    ) -> None:
        dev_dir = make_plugin(tmp_path / "dev", "dev-plugin")
        registry.add_dev_plugin(dev_dir)
        skill_dirs = registry.get_all_skill_dirs()
        assert len(skill_dirs) == 1
        assert skill_dirs[0] == (dev_dir / "skills").resolve()

    def test_dev_plugin_overrides_installed(
        self, registry: PluginRegistryManager, plugins_dir: Path, tmp_path: Path
    ) -> None:
        make_plugin(plugins_dir, "my-plugin", version="1.0.0")
        registry.register(
            PluginEntry(
                name="my-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=True,
            )
        )
        dev_dir = make_plugin(tmp_path / "dev", "my-plugin", version="2.0.0")
        registry.add_dev_plugin(dev_dir)

        skill_dirs = registry.get_all_skill_dirs()
        # Should only have one entry (dev overrides installed)
        assert len(skill_dirs) == 1
        assert skill_dirs[0] == (dev_dir / "skills").resolve()

    def test_dev_plugin_invalid_dir_skipped(
        self, registry: PluginRegistryManager, tmp_path: Path
    ) -> None:
        # First add a valid dev plugin, then a broken one via direct injection
        dev_dir = make_plugin(tmp_path / "dev", "good-plugin")
        registry.add_dev_plugin(dev_dir)

        # Inject a broken dev plugin (no manifest)
        broken_dir = tmp_path / "broken"
        broken_dir.mkdir()
        registry._dev_plugins["broken-plugin"] = broken_dir

        # Good plugin's skills should still resolve
        skill_dirs = registry.get_all_skill_dirs()
        assert len(skill_dirs) == 1


class TestPathAggregation:
    """Tests for get_all_agent_dirs, get_all_tool_dirs, get_all_command_dirs."""

    def test_get_all_agent_dirs(
        self, registry: PluginRegistryManager, plugins_dir: Path
    ) -> None:
        plugin_dir = plugins_dir / "my-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "agents").mkdir()
        data = {"name": "my-plugin", "description": "Test", "agents": ["agents"]}
        (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        registry.register(
            PluginEntry(
                name="my-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=True,
            )
        )
        agent_dirs = registry.get_all_agent_dirs()
        assert len(agent_dirs) == 1

    def test_get_all_tool_dirs(
        self, registry: PluginRegistryManager, plugins_dir: Path
    ) -> None:
        plugin_dir = plugins_dir / "my-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "tools").mkdir()
        data = {"name": "my-plugin", "description": "Test", "tools": ["tools"]}
        (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        registry.register(
            PluginEntry(
                name="my-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=True,
            )
        )
        tool_dirs = registry.get_all_tool_dirs()
        assert len(tool_dirs) == 1

    def test_get_all_command_dirs(
        self, registry: PluginRegistryManager, plugins_dir: Path
    ) -> None:
        plugin_dir = plugins_dir / "my-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "commands").mkdir()
        data = {"name": "my-plugin", "description": "Test", "commands": ["commands"]}
        (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        registry.register(
            PluginEntry(
                name="my-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=True,
            )
        )
        cmd_dirs = registry.get_all_command_dirs()
        assert len(cmd_dirs) == 1
