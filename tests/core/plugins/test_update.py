"""Tests for plugin update and update_all methods."""

from __future__ import annotations

from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from tests.core.plugins.conftest import make_plugin
from vibe.core.plugins.installer import PluginInstaller
from vibe.core.plugins.models import PluginEntry, PluginScope, PluginSource
from vibe.core.plugins.registry import PluginRegistryManager


class TestUpdate:
    def test_update_not_installed_raises(self, installer: PluginInstaller) -> None:
        with pytest.raises(KeyError, match="not installed"):
            installer.update("nonexistent")

    def test_update_local_plugin_returns_none(
        self,
        installer: PluginInstaller,
        registry: PluginRegistryManager,
        tmp_path: Path,
    ) -> None:
        make_plugin(tmp_path / "local", "my-plugin")
        # Register directly as LOCAL source (no source_uri for git)
        registry.register(
            PluginEntry(
                name="my-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=True,
            )
        )
        result = installer.update("my-plugin")
        assert result is None

    def test_update_git_plugin_calls_install_from_git(
        self, installer: PluginInstaller, registry: PluginRegistryManager
    ) -> None:
        registry.register(
            PluginEntry(
                name="git-plugin",
                version="1.0.0",
                source=PluginSource.GIT,
                source_uri="https://github.com/test/repo.git",
                enabled=True,
                pinned_ref="main",
            )
        )
        from vibe.core.plugins.models import PluginManifest

        mock_manifest = PluginManifest(
            name="git-plugin", description="Test", version="2.0.0"
        )
        with patch.object(
            installer, "install_from_git", return_value=mock_manifest
        ) as mock_install:
            result = installer.update("git-plugin")
            assert result is not None
            assert result.version == "2.0.0"
            mock_install.assert_called_once_with(
                "https://github.com/test/repo.git", "main", scope=PluginScope.USER
            )


class TestUpdateAll:
    def test_update_all_skips_non_git(
        self, installer: PluginInstaller, registry: PluginRegistryManager
    ) -> None:
        registry.register(
            PluginEntry(
                name="local-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=True,
            )
        )
        result = installer.update_all()
        assert result == []

    def test_update_all_returns_changed(
        self, installer: PluginInstaller, registry: PluginRegistryManager
    ) -> None:
        registry.register(
            PluginEntry(
                name="git-plugin",
                version="1.0.0",
                source=PluginSource.GIT,
                source_uri="https://github.com/test/repo.git",
                enabled=True,
            )
        )
        from vibe.core.plugins.models import PluginManifest

        mock_manifest = PluginManifest(
            name="git-plugin", description="Test", version="2.0.0"
        )
        with patch.object(installer, "install_from_git", return_value=mock_manifest):
            result = installer.update_all()
            assert len(result) == 1
            assert result[0] == ("git-plugin", "1.0.0", "2.0.0")

    def test_update_all_handles_errors_gracefully(
        self, installer: PluginInstaller, registry: PluginRegistryManager
    ) -> None:
        registry.register(
            PluginEntry(
                name="broken-plugin",
                version="1.0.0",
                source=PluginSource.GIT,
                source_uri="https://github.com/test/broken.git",
                enabled=True,
            )
        )
        with patch.object(
            installer,
            "install_from_git",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = installer.update_all()
            assert result == []


class TestCLIUpdate:
    def test_update_single_plugin(self) -> None:
        from vibe.core.plugins.cli import build_plugin_parser

        parser = build_plugin_parser()
        args = parser.parse_args(["update", "my-plugin"])
        assert args.plugin_command == "update"
        assert args.name == "my-plugin"

    def test_update_all_plugins(self) -> None:
        from vibe.core.plugins.cli import build_plugin_parser

        parser = build_plugin_parser()
        args = parser.parse_args(["update"])
        assert args.plugin_command == "update"
        assert args.name is None
