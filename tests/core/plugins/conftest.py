from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from vibe.core.plugins.installer import PluginInstaller
from vibe.core.plugins.registry import PluginRegistryManager


@pytest.fixture()
def plugins_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugins"
    d.mkdir()
    return d


@pytest.fixture()
def project_plugins_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project-plugins"
    d.mkdir()
    return d


@pytest.fixture()
def local_plugins_dir(tmp_path: Path) -> Path:
    d = tmp_path / "local-plugins"
    d.mkdir()
    return d


@pytest.fixture()
def registry(plugins_dir: Path) -> PluginRegistryManager:
    return PluginRegistryManager(plugins_dir=plugins_dir)


@pytest.fixture()
def installer(registry: PluginRegistryManager) -> PluginInstaller:
    return PluginInstaller(registry)


@pytest.fixture()
def multi_scope_registry(
    plugins_dir: Path, project_plugins_dir: Path, local_plugins_dir: Path
) -> PluginRegistryManager:
    return PluginRegistryManager(
        plugins_dir=plugins_dir,
        project_plugins_dir=project_plugins_dir,
        local_plugins_dir=local_plugins_dir,
    )


def make_plugin(base_dir: Path, name: str, *, version: str = "1.0.0") -> Path:
    """Create a minimal plugin directory with a plugin.toml."""
    plugin_dir = base_dir / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    skills_dir = plugin_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    data = {
        "name": name,
        "description": f"Test plugin {name}",
        "version": version,
        "skills": ["skills"],
    }
    (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
    return plugin_dir
