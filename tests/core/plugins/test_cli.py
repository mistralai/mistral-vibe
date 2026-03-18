from __future__ import annotations

import pytest

from vibe.core.plugins.cli import build_plugin_parser, handle_plugin_command
from vibe.core.plugins.models import PluginEntry, PluginSource
from vibe.core.plugins.registry import PluginRegistryManager


class TestBuildPluginParser:
    def test_parser_has_subcommands(self) -> None:
        parser = build_plugin_parser()
        # Should not raise
        args = parser.parse_args(["list"])
        assert args.plugin_command == "list"

    def test_install_subcommand(self) -> None:
        parser = build_plugin_parser()
        args = parser.parse_args(["install", "https://github.com/test/repo"])
        assert args.plugin_command == "install"
        assert args.source == "https://github.com/test/repo"

    def test_install_with_local_flag(self) -> None:
        parser = build_plugin_parser()
        args = parser.parse_args(["install", "/some/path", "--local"])
        assert args.local is True


class TestHandlePluginList:
    def test_list_empty(
        self,
        registry: PluginRegistryManager,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "vibe.core.config.harness_files._harness_manager._get_plugin_registry_manager",
            lambda: registry,
        )
        parser = build_plugin_parser()
        args = parser.parse_args(["list"])
        handle_plugin_command(args)
        captured = capsys.readouterr()
        assert "No plugins installed" in captured.out

    def test_list_with_plugin(
        self,
        registry: PluginRegistryManager,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        registry.register(
            PluginEntry(
                name="test-plugin",
                version="1.0.0",
                source=PluginSource.GIT,
                enabled=True,
            )
        )
        monkeypatch.setattr(
            "vibe.core.config.harness_files._harness_manager._get_plugin_registry_manager",
            lambda: registry,
        )
        parser = build_plugin_parser()
        args = parser.parse_args(["list"])
        handle_plugin_command(args)
        captured = capsys.readouterr()
        assert "test-plugin" in captured.out


class TestHandlePluginEnableDisable:
    def test_enable(
        self, registry: PluginRegistryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry.register(
            PluginEntry(
                name="my-plugin",
                version="1.0.0",
                source=PluginSource.GIT,
                enabled=False,
            )
        )
        monkeypatch.setattr(
            "vibe.core.config.harness_files._harness_manager._get_plugin_registry_manager",
            lambda: registry,
        )
        parser = build_plugin_parser()
        args = parser.parse_args(["enable", "my-plugin"])
        handle_plugin_command(args)
        assert registry.get_all_plugins()["my-plugin"].enabled is True

    def test_disable(
        self, registry: PluginRegistryManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry.register(
            PluginEntry(
                name="my-plugin", version="1.0.0", source=PluginSource.GIT, enabled=True
            )
        )
        monkeypatch.setattr(
            "vibe.core.config.harness_files._harness_manager._get_plugin_registry_manager",
            lambda: registry,
        )
        parser = build_plugin_parser()
        args = parser.parse_args(["disable", "my-plugin"])
        handle_plugin_command(args)
        assert registry.get_all_plugins()["my-plugin"].enabled is False
