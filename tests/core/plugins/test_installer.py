from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest
import tomli_w

from vibe.core.plugins.installer import PluginInstaller, _GitUrl
from vibe.core.plugins.models import PluginSource
from vibe.core.plugins.registry import PluginRegistryManager

_skip_symlink = pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("CI"),
    reason="Symlinks require elevated privileges on Windows",
)


def _create_local_plugin(base: Path, name: str) -> Path:
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True)
    skills_dir = plugin_dir / "skills"
    skills_dir.mkdir()
    data = {"name": name, "description": f"Test {name}", "skills": ["skills"]}
    (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
    return plugin_dir


@_skip_symlink
class TestInstallFromLocal:
    def test_install_creates_symlink(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        registry: PluginRegistryManager,
    ) -> None:
        local = _create_local_plugin(tmp_path / "local-plugins", "my-plugin")
        manifest = installer.install_from_local(local)
        assert manifest.name == "my-plugin"
        link = registry.plugins_dir / "my-plugin"
        assert link.is_symlink()
        assert link.resolve() == local.resolve()

    def test_install_registers_plugin(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        registry: PluginRegistryManager,
    ) -> None:
        local = _create_local_plugin(tmp_path / "local-plugins", "my-plugin")
        installer.install_from_local(local)
        plugins = registry.get_all_plugins()
        assert "my-plugin" in plugins
        assert plugins["my-plugin"].source == PluginSource.LOCAL

    def test_reinstall_overwrites_symlink(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        registry: PluginRegistryManager,
    ) -> None:
        local1 = _create_local_plugin(tmp_path / "v1", "my-plugin")
        local2 = _create_local_plugin(tmp_path / "v2", "my-plugin")
        installer.install_from_local(local1)
        installer.install_from_local(local2)
        link = registry.plugins_dir / "my-plugin"
        assert link.resolve() == local2.resolve()


class TestInstallFromMarketplace:
    def test_install_copies_plugin(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        registry: PluginRegistryManager,
    ) -> None:
        marketplace = tmp_path / "marketplace"
        marketplace.mkdir()
        _create_local_plugin(marketplace / "plugins", "cool-plugin")
        index_data = {
            "name": "test-market",
            "plugins": [
                {
                    "name": "cool-plugin",
                    "source": "plugins/cool-plugin",
                    "description": "Cool",
                }
            ],
        }
        (marketplace / "marketplace.toml").write_text(
            tomli_w.dumps(index_data), encoding="utf-8"
        )

        manifest = installer.install_from_marketplace("cool-plugin", marketplace)
        assert manifest.name == "cool-plugin"
        assert (registry.plugins_dir / "cool-plugin").is_dir()
        assert not (registry.plugins_dir / "cool-plugin").is_symlink()

    def test_install_with_plugin_root(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        registry: PluginRegistryManager,
    ) -> None:
        """Marketplace with metadata.pluginRoot resolves source relative to root."""
        marketplace = tmp_path / "marketplace"
        (marketplace / ".github" / "plugin").mkdir(parents=True)
        _create_local_plugin(marketplace / "plugins", "my-plugin")
        index_data = {
            "name": "test-market",
            "metadata": {"pluginRoot": "./plugins"},
            "plugins": [
                {"name": "my-plugin", "source": "my-plugin", "description": "Test"}
            ],
        }
        index_path = marketplace / ".github" / "plugin" / "marketplace.json"
        import json

        index_path.write_text(json.dumps(index_data), encoding="utf-8")

        manifest = installer.install_from_marketplace("my-plugin", marketplace)
        assert manifest.name == "my-plugin"
        assert (registry.plugins_dir / "my-plugin").is_dir()

    def test_marketplace_rejects_path_traversal(
        self, tmp_path: Path, installer: PluginInstaller
    ) -> None:
        marketplace = tmp_path / "marketplace"
        marketplace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        _create_local_plugin(outside, "evil-plugin")
        index_data = {
            "name": "test-market",
            "plugins": [
                {
                    "name": "evil-plugin",
                    "source": "../outside/evil-plugin",
                    "description": "Evil",
                }
            ],
        }
        (marketplace / "marketplace.toml").write_text(
            tomli_w.dumps(index_data), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="Invalid source path"):
            installer.install_from_marketplace("evil-plugin", marketplace)

    def test_marketplace_not_found_raises(
        self, tmp_path: Path, installer: PluginInstaller
    ) -> None:
        marketplace = tmp_path / "marketplace"
        marketplace.mkdir()
        index_data = {"name": "empty", "plugins": []}
        (marketplace / "marketplace.toml").write_text(
            tomli_w.dumps(index_data), encoding="utf-8"
        )
        with pytest.raises(KeyError, match="not found"):
            installer.install_from_marketplace("nonexistent", marketplace)


@_skip_symlink
class TestRemove:
    def test_remove_local_plugin(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        registry: PluginRegistryManager,
    ) -> None:
        local = _create_local_plugin(tmp_path / "local-plugins", "my-plugin")
        installer.install_from_local(local)
        installer.remove("my-plugin")
        assert "my-plugin" not in registry.get_all_plugins()
        assert not (registry.plugins_dir / "my-plugin").exists()


class TestRemovePathTraversal:
    def test_remove_rejects_path_traversal(self, installer: PluginInstaller) -> None:
        with pytest.raises(ValueError, match="Invalid plugin name"):
            installer.remove("../../evil")

    def test_remove_rejects_dotdot_name(self, installer: PluginInstaller) -> None:
        with pytest.raises(ValueError, match="Invalid plugin name"):
            installer.remove("../ssh")


class TestUpdatePathTraversal:
    def test_update_rejects_invalid_name(self, installer: PluginInstaller) -> None:
        with pytest.raises(ValueError, match="Invalid plugin name"):
            installer.update("../../evil")


class TestGitClone:
    def test_rejects_url_starting_with_dash(self) -> None:
        with pytest.raises(ValueError, match="Invalid git URL"):
            PluginInstaller._git_clone("--upload-pack=evil", Path("/tmp/dest"), None)

    def test_rejects_ref_starting_with_dash(self) -> None:
        with pytest.raises(ValueError, match="Invalid git ref"):
            PluginInstaller._git_clone(
                "https://github.com/test/repo", Path("/tmp/dest"), "--evil"
            )


class TestParseGithubUrl:
    def test_plain_github_url(self) -> None:
        result = PluginInstaller._parse_github_url(
            "https://github.com/github/awesome-copilot"
        )
        assert result == _GitUrl(
            clone_url="https://github.com/github/awesome-copilot", ref=None, subdir=None
        )

    def test_github_url_with_dot_git(self) -> None:
        result = PluginInstaller._parse_github_url(
            "https://github.com/github/awesome-copilot.git"
        )
        assert result == _GitUrl(
            clone_url="https://github.com/github/awesome-copilot", ref=None, subdir=None
        )

    def test_github_tree_ref_only(self) -> None:
        result = PluginInstaller._parse_github_url(
            "https://github.com/org/repo/tree/main"
        )
        assert result == _GitUrl(
            clone_url="https://github.com/org/repo", ref="main", subdir=None
        )

    def test_github_tree_ref_and_subdir(self) -> None:
        result = PluginInstaller._parse_github_url(
            "https://github.com/anthropics/claude-code/tree/main/plugins"
        )
        assert result == _GitUrl(
            clone_url="https://github.com/anthropics/claude-code",
            ref="main",
            subdir="plugins",
        )

    def test_github_deep_subdir(self) -> None:
        result = PluginInstaller._parse_github_url(
            "https://github.com/org/repo/tree/v2/path/to/plugin"
        )
        assert result == _GitUrl(
            clone_url="https://github.com/org/repo", ref="v2", subdir="path/to/plugin"
        )

    def test_non_github_url_passthrough(self) -> None:
        result = PluginInstaller._parse_github_url("https://gitlab.com/org/repo")
        assert result == _GitUrl(
            clone_url="https://gitlab.com/org/repo", ref=None, subdir=None
        )

    def test_ssh_url_passthrough(self) -> None:
        result = PluginInstaller._parse_github_url("git@github.com:org/repo.git")
        assert result == _GitUrl(
            clone_url="git@github.com:org/repo.git", ref=None, subdir=None
        )

    def test_trailing_slash_tree_url(self) -> None:
        result = PluginInstaller._parse_github_url(
            "https://github.com/org/repo/tree/main/"
        )
        assert result == _GitUrl(
            clone_url="https://github.com/org/repo", ref="main", subdir=None
        )


class TestInstallFromGitSubdirTraversal:
    def test_rejects_path_traversal_in_subdir(
        self, installer: PluginInstaller, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_git_clone(url: str, dest: Path, ref: str | None) -> None:
            dest.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(PluginInstaller, "_git_clone", staticmethod(fake_git_clone))
        with pytest.raises(ValueError, match="Invalid subdirectory"):
            installer.install_from_git(
                "https://github.com/org/repo/tree/main/../../etc/passwd"
            )


class TestInstallFromGitSubdir:
    def test_install_from_subdir_url(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        registry: PluginRegistryManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_git_clone(url: str, dest: Path, ref: str | None) -> None:
            dest.mkdir(parents=True, exist_ok=True)
            plugin_dir = dest / "plugins"
            plugin_dir.mkdir()
            skills_dir = plugin_dir / "skills"
            skills_dir.mkdir()
            data = {
                "name": "claude-plugin",
                "description": "A plugin in a subdir",
                "skills": ["skills"],
            }
            (plugin_dir / "plugin.toml").write_text(
                tomli_w.dumps(data), encoding="utf-8"
            )

        monkeypatch.setattr(PluginInstaller, "_git_clone", staticmethod(fake_git_clone))

        url = "https://github.com/anthropics/claude-code/tree/main/plugins"
        manifest = installer.install_from_git(url)

        assert manifest.name == "claude-plugin"
        assert (registry.plugins_dir / "claude-plugin").is_dir()

        plugins = registry.get_all_plugins()
        assert "claude-plugin" in plugins
        assert plugins["claude-plugin"].source == PluginSource.GIT
        assert plugins["claude-plugin"].source_uri == url

    def test_explicit_ref_overrides_url_ref(
        self,
        tmp_path: Path,
        installer: PluginInstaller,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_ref: list[str | None] = []

        def fake_git_clone(url: str, dest: Path, ref: str | None) -> None:
            captured_ref.append(ref)
            plugin_sub = dest / "plugins"
            plugin_sub.mkdir(parents=True)
            data = {
                "name": "test-plugin",
                "description": "Explicit ref test",
                "version": "1.0.0",
            }
            (plugin_sub / "plugin.toml").write_text(
                tomli_w.dumps(data), encoding="utf-8"
            )

        monkeypatch.setattr(PluginInstaller, "_git_clone", staticmethod(fake_git_clone))
        installer.install_from_git(
            "https://github.com/anthropics/claude-code/tree/main/plugins", ref="v2"
        )
        assert captured_ref == ["v2"]
