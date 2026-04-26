from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from vibe.core.plugins.models import PluginEntry, PluginSource
from vibe.core.plugins.registry import PluginRegistryManager


@pytest.fixture()
def plugins_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugins"
    d.mkdir()
    return d


@pytest.fixture()
def registry(plugins_dir: Path) -> PluginRegistryManager:
    return PluginRegistryManager(plugins_dir=plugins_dir)


def _make_plugin(plugins_dir: Path, name: str) -> Path:
    """Create a minimal plugin directory with a plugin.toml."""
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    skills_dir = plugin_dir / "skills"
    skills_dir.mkdir()
    data = {"name": name, "description": f"Test plugin {name}", "skills": ["skills"]}
    (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
    return plugin_dir


class TestPluginRegistryManager:
    def test_register_and_get(self, registry: PluginRegistryManager) -> None:
        entry = PluginEntry(name="test", version="1.0.0", source=PluginSource.GIT)
        registry.register(entry)
        plugins = registry.get_all_plugins()
        assert "test" in plugins
        assert plugins["test"].version == "1.0.0"

    def test_unregister(self, registry: PluginRegistryManager) -> None:
        entry = PluginEntry(name="test", version="1.0.0", source=PluginSource.GIT)
        registry.register(entry)
        registry.unregister("test")
        assert "test" not in registry.get_all_plugins()

    def test_get_enabled_plugins(self, registry: PluginRegistryManager) -> None:
        registry.register(
            PluginEntry(
                name="enabled", version="1.0.0", source=PluginSource.GIT, enabled=True
            )
        )
        registry.register(
            PluginEntry(
                name="disabled", version="1.0.0", source=PluginSource.GIT, enabled=False
            )
        )
        enabled = registry.get_enabled_plugins()
        assert "enabled" in enabled
        assert "disabled" not in enabled

    def test_set_enabled(self, registry: PluginRegistryManager) -> None:
        registry.register(
            PluginEntry(
                name="test", version="1.0.0", source=PluginSource.GIT, enabled=True
            )
        )
        registry.set_enabled("test", enabled=False)
        assert not registry.get_all_plugins()["test"].enabled

    def test_set_enabled_not_registered_raises(
        self, registry: PluginRegistryManager
    ) -> None:
        with pytest.raises(KeyError, match="not registered"):
            registry.set_enabled("nonexistent", enabled=True)

    def test_get_all_skill_dirs(
        self, registry: PluginRegistryManager, plugins_dir: Path
    ) -> None:
        _make_plugin(plugins_dir, "my-plugin")
        registry.register(
            PluginEntry(
                name="my-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=True,
            )
        )
        skill_dirs = registry.get_all_skill_dirs()
        assert len(skill_dirs) == 1
        assert skill_dirs[0] == (plugins_dir / "my-plugin" / "skills").resolve()

    def test_disabled_plugin_excluded_from_paths(
        self, registry: PluginRegistryManager, plugins_dir: Path
    ) -> None:
        _make_plugin(plugins_dir, "my-plugin")
        registry.register(
            PluginEntry(
                name="my-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=False,
            )
        )
        assert registry.get_all_skill_dirs() == []

    def test_invalidate_clears_cache(self, registry: PluginRegistryManager) -> None:
        registry.register(
            PluginEntry(name="test", version="1.0.0", source=PluginSource.GIT)
        )
        assert "test" in registry.get_all_plugins()
        registry.invalidate()
        # After invalidation, registry re-reads from disk
        assert "test" in registry.get_all_plugins()

    def test_persistence_across_instances(self, plugins_dir: Path) -> None:
        reg1 = PluginRegistryManager(plugins_dir=plugins_dir)
        reg1.register(
            PluginEntry(name="persist", version="2.0.0", source=PluginSource.GIT)
        )
        reg2 = PluginRegistryManager(plugins_dir=plugins_dir)
        assert "persist" in reg2.get_all_plugins()
