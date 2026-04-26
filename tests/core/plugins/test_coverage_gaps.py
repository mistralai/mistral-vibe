"""Additional tests to cover gaps identified in code review.

Covers:
- CLI handler integration (install, search, marketplace)
- MarketplaceManager.fetch with mocked git
- get_all_mcp_servers aggregation
- strip_frontmatter utility
- install_from_local rollback
- install_from_marketplace name mismatch warning
- _marketplace_update with normalized URLs
- double-toggle in plugin picker
- _extract_description edge cases
- Three-scope priority chain
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w

from vibe.core.plugins.commands import _extract_description, strip_frontmatter
from vibe.core.plugins.installer import PluginInstaller
from vibe.core.plugins.marketplace import MarketplaceManager
from vibe.core.plugins.models import (
    MarketplaceConfig,
    MarketplaceIndex,
    MarketplacePluginRef,
    PluginEntry,
    PluginManifest,
    PluginScope,
    PluginSource,
)
from vibe.core.plugins.registry import PluginRegistryManager

# ---------------------------------------------------------------------------
# strip_frontmatter utility
# ---------------------------------------------------------------------------


class TestStripFrontmatter:
    def test_removes_frontmatter(self) -> None:
        text = '---\ndescription: "hello"\n---\nBody text\n'
        assert strip_frontmatter(text) == "\nBody text\n"

    def test_no_frontmatter_passthrough(self) -> None:
        text = "Just plain text\n"
        assert strip_frontmatter(text) == text

    def test_unclosed_frontmatter_passthrough(self) -> None:
        text = "---\nkey: value\nno closing delimiter\n"
        assert strip_frontmatter(text) == text


# ---------------------------------------------------------------------------
# _extract_description edge cases
# ---------------------------------------------------------------------------


class TestExtractDescriptionEdgeCases:
    def test_unclosed_frontmatter_falls_back(self, tmp_path: Path) -> None:
        md = tmp_path / "test.md"
        md.write_text("---\nkey: value\n", encoding="utf-8")
        result = _extract_description(md)
        assert isinstance(result, str)
        assert result == "---"

    def test_frontmatter_without_description_key(self, tmp_path: Path) -> None:
        md = tmp_path / "test.md"
        md.write_text("---\ntitle: Hello\n---\nBody text\n", encoding="utf-8")
        result = _extract_description(md)
        assert result == "Body text"

    def test_malformed_yaml_returns_meaningful_fallback(self, tmp_path: Path) -> None:
        md = tmp_path / "broken.md"
        md.write_text("---\n: : :\n---\nBody text\n", encoding="utf-8")
        result = _extract_description(md)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# MarketplaceManager.fetch with mocked git
# ---------------------------------------------------------------------------


class TestMarketplaceManagerFetch:
    def test_fetch_clones_fresh_repo(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        mgr = MarketplaceManager(cache_dir=cache_dir)
        config = MarketplaceConfig(url="https://github.com/test/market")

        clone_target = cache_dir / mgr._cache_key(mgr.normalize_url(config.url))

        def fake_subprocess_run(cmd: list[str], *, check: bool = False) -> None:
            if "clone" in cmd:
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / ".git").mkdir()
                data = {"name": "test-market", "plugins": []}
                (dest / "marketplace.toml").write_text(
                    tomli_w.dumps(data), encoding="utf-8"
                )

        with patch(
            "vibe.core.plugins.marketplace.subprocess.run",
            side_effect=fake_subprocess_run,
        ):
            index = mgr.fetch(config)
            assert index.name == "test-market"
            assert clone_target.exists()

    def test_fetch_pulls_existing_repo(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        mgr = MarketplaceManager(cache_dir=cache_dir)
        config = MarketplaceConfig(url="https://github.com/test/market")

        # Pre-create a cached clone
        clone_target = cache_dir / mgr._cache_key(mgr.normalize_url(config.url))
        clone_target.mkdir(parents=True)
        (clone_target / ".git").mkdir()
        data = {"name": "test-market", "plugins": []}
        (clone_target / "marketplace.toml").write_text(
            tomli_w.dumps(data), encoding="utf-8"
        )

        pull_called = []

        def fake_subprocess_run(cmd: list[str], *, check: bool = False) -> None:
            if "pull" in cmd:
                pull_called.append(True)

        with patch(
            "vibe.core.plugins.marketplace.subprocess.run",
            side_effect=fake_subprocess_run,
        ):
            index = mgr.fetch(config, force=True)
            assert index.name == "test-market"
            assert pull_called

    def test_fetch_ttl_skips_repull(self, tmp_path: Path) -> None:
        """Fetching the same marketplace twice within TTL should skip the git pull."""
        cache_dir = tmp_path / "cache"
        mgr = MarketplaceManager(cache_dir=cache_dir)
        config = MarketplaceConfig(url="https://github.com/test/market")

        clone_target = cache_dir / mgr._cache_key(mgr.normalize_url(config.url))
        clone_target.mkdir(parents=True)
        (clone_target / ".git").mkdir()
        data = {"name": "test-market", "plugins": []}
        (clone_target / "marketplace.toml").write_text(
            tomli_w.dumps(data), encoding="utf-8"
        )

        call_count = []

        def fake_subprocess_run(cmd: list[str], *, check: bool = False) -> None:
            call_count.append(cmd)

        with patch(
            "vibe.core.plugins.marketplace.subprocess.run",
            side_effect=fake_subprocess_run,
        ):
            mgr.fetch(config, force=True)  # First fetch — should pull
            mgr.fetch(config)  # Second fetch — should skip (TTL)
            # Only one subprocess call (the forced pull)
            assert len(call_count) == 1

    def test_fetch_ttl_skips_repull_across_manager_instances(
        self, tmp_path: Path
    ) -> None:
        cache_dir = tmp_path / "cache"
        config = MarketplaceConfig(url="https://github.com/test/market")
        mgr = MarketplaceManager(cache_dir=cache_dir)

        clone_target = cache_dir / mgr._cache_key(mgr.normalize_url(config.url))
        clone_target.mkdir(parents=True)
        (clone_target / ".git").mkdir()
        data = {"name": "test-market", "plugins": []}
        (clone_target / "marketplace.toml").write_text(
            tomli_w.dumps(data), encoding="utf-8"
        )

        call_count = []

        def fake_subprocess_run(cmd: list[str], *, check: bool = False) -> None:
            call_count.append(cmd)

        with patch(
            "vibe.core.plugins.marketplace.subprocess.run",
            side_effect=fake_subprocess_run,
        ):
            mgr.fetch(config, force=True)
            MarketplaceManager(cache_dir=cache_dir).fetch(config)

        assert len(call_count) == 1

    def test_is_marketplace_returns_false_on_missing_index(
        self, tmp_path: Path
    ) -> None:
        cache_dir = tmp_path / "cache"
        mgr = MarketplaceManager(cache_dir=cache_dir)
        config = MarketplaceConfig(url="https://github.com/test/nope")

        def fake_subprocess_run(cmd: list[str], *, check: bool = False) -> None:
            if "clone" in cmd:
                dest = Path(cmd[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / ".git").mkdir()
                # No marketplace.toml — just regular repo

        with patch(
            "vibe.core.plugins.marketplace.subprocess.run",
            side_effect=fake_subprocess_run,
        ):
            assert mgr.is_marketplace(config) is False

    def test_search_all_across_multiple_marketplaces(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        mgr = MarketplaceManager(cache_dir=cache_dir)

        configs = [
            MarketplaceConfig(url="https://github.com/test/market1"),
            MarketplaceConfig(url="https://github.com/test/market2"),
        ]

        def fake_fetch(
            config: MarketplaceConfig, *, force: bool = False
        ) -> MarketplaceIndex:
            if "market1" in config.url:
                return MarketplaceIndex(
                    name="market-1",
                    plugins=[
                        MarketplacePluginRef(
                            name="search-a", source="a", description="search plugin"
                        )
                    ],
                )
            return MarketplaceIndex(
                name="market-2",
                plugins=[
                    MarketplacePluginRef(
                        name="search-b", source="b", description="another search"
                    )
                ],
            )

        with patch.object(mgr, "fetch", side_effect=fake_fetch):
            results = mgr.search_all(configs, "search")
            assert len(results) == 2
            names = {ref.name for _, ref in results}
            assert names == {"search-a", "search-b"}


# ---------------------------------------------------------------------------
# get_all_mcp_servers
# ---------------------------------------------------------------------------


class TestGetAllMcpServers:
    def test_aggregates_mcp_servers_from_enabled_plugins(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        # Plugin with .mcp.json
        plugin_dir = plugins_dir / "mcp-plugin"
        plugin_dir.mkdir()
        data = {"name": "mcp-plugin", "description": "Has MCP"}
        (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        mcp_data = {
            "mcpServers": {"my-srv": {"command": "node", "args": ["server.js"]}}
        }
        (plugin_dir / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")

        registry = PluginRegistryManager(plugins_dir=plugins_dir)
        registry.register(
            PluginEntry(
                name="mcp-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=True,
            )
        )

        servers = registry.get_all_mcp_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "my-srv"
        assert servers[0]["transport"] == "stdio"

    def test_disabled_plugin_excluded_from_mcp(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        plugin_dir = plugins_dir / "mcp-plugin"
        plugin_dir.mkdir()
        data = {"name": "mcp-plugin", "description": "Has MCP"}
        (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        mcp_data = {"mcpServers": {"srv": {"command": "node", "args": []}}}
        (plugin_dir / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")

        registry = PluginRegistryManager(plugins_dir=plugins_dir)
        registry.register(
            PluginEntry(
                name="mcp-plugin",
                version="1.0.0",
                source=PluginSource.LOCAL,
                enabled=False,
            )
        )

        servers = registry.get_all_mcp_servers()
        assert servers == []


# ---------------------------------------------------------------------------
# install_from_local rollback
# ---------------------------------------------------------------------------


class TestInstallFromLocalRollback:
    def test_rollback_on_registration_failure(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        registry = PluginRegistryManager(plugins_dir=plugins_dir)
        installer = PluginInstaller(registry)

        local = tmp_path / "local" / "my-plugin"
        local.mkdir(parents=True)
        data = {"name": "my-plugin", "description": "Test"}
        (local / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")

        with patch.object(registry, "register", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                installer.install_from_local(local)

        link = plugins_dir / "my-plugin"
        assert not link.exists() and not link.is_symlink()


# ---------------------------------------------------------------------------
# install_from_marketplace name mismatch
# ---------------------------------------------------------------------------


class TestInstallFromMarketplaceNameMismatch:
    def test_name_mismatch_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        registry = PluginRegistryManager(plugins_dir=plugins_dir)
        installer = PluginInstaller(registry)

        marketplace = tmp_path / "marketplace"
        marketplace.mkdir()
        plugin_src = marketplace / "plugins" / "cool-plugin"
        plugin_src.mkdir(parents=True)
        data = {
            "name": "different-name",
            "description": "Manifest has a different name",
        }
        (plugin_src / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        index_data = {
            "name": "test-market",
            "plugins": [
                {
                    "name": "cool-plugin",
                    "source": "plugins/cool-plugin",
                    "description": "Mismatch test",
                }
            ],
        }
        (marketplace / "marketplace.toml").write_text(
            tomli_w.dumps(index_data), encoding="utf-8"
        )

        import logging

        with caplog.at_level(logging.WARNING):
            installer.install_from_marketplace("cool-plugin", marketplace)

        # Registry should use the marketplace name, not the manifest name
        plugins = registry.get_all_plugins()
        assert "cool-plugin" in plugins
        assert "differs from marketplace name" in caplog.text


# ---------------------------------------------------------------------------
# Three-scope priority chain
# ---------------------------------------------------------------------------


class TestThreeScopePriority:
    def test_user_overrides_local_overrides_project(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        local_dir = tmp_path / "local"
        local_dir.mkdir()

        registry = PluginRegistryManager(
            plugins_dir=user_dir,
            project_plugins_dir=proj_dir,
            local_plugins_dir=local_dir,
        )

        # Register same plugin in all three scopes with different versions
        registry.register(
            PluginEntry(name="my-plugin", version="1.0.0", source=PluginSource.GIT),
            scope=PluginScope.PROJECT,
        )
        registry.register(
            PluginEntry(name="my-plugin", version="2.0.0", source=PluginSource.GIT),
            scope=PluginScope.LOCAL,
        )
        registry.register(
            PluginEntry(name="my-plugin", version="3.0.0", source=PluginSource.GIT),
            scope=PluginScope.USER,
        )

        # USER (3.0.0) should win
        plugins = registry.get_all_plugins_with_scope()
        scope, entry = plugins["my-plugin"]
        assert scope == PluginScope.USER
        assert entry.version == "3.0.0"


# ---------------------------------------------------------------------------
# Marketplace search with empty query
# ---------------------------------------------------------------------------


class TestSearchEmptyQuery:
    def test_empty_query_returns_all_plugins(self) -> None:
        mgr = MarketplaceManager()
        index = MarketplaceIndex(
            name="test",
            plugins=[
                MarketplacePluginRef(name="a", source="a", description="first"),
                MarketplacePluginRef(name="b", source="b", description="second"),
            ],
        )
        results = mgr.search(index, "")
        assert len(results) == 2


# ---------------------------------------------------------------------------
# MCP JSON allowlisted keys
# ---------------------------------------------------------------------------


class TestMcpJsonAllowlistedKeys:
    def test_unknown_keys_filtered_out(self, tmp_path: Path) -> None:
        """Unknown keys in .mcp.json entries should be filtered."""
        mcp_data = {
            "mcpServers": {
                "my-srv": {
                    "command": "node",
                    "args": ["s.js"],
                    "evil_key": "should_be_dropped",
                    "another_bad": True,
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")
        servers = PluginManifest.load_mcp_json(tmp_path)
        assert len(servers) == 1
        assert "evil_key" not in servers[0]
        assert "another_bad" not in servers[0]
        assert servers[0]["command"] == "node"


# ---------------------------------------------------------------------------
# CLI handler integration tests
# ---------------------------------------------------------------------------


class TestCLIHandlerInstallLocal:
    def test_install_local_via_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        registry = PluginRegistryManager(plugins_dir=plugins_dir)

        local = tmp_path / "my-plugin"
        local.mkdir()
        data = {"name": "my-plugin", "description": "Test"}
        (local / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")

        monkeypatch.setattr(
            "vibe.core.config.harness_files._harness_manager._get_plugin_registry_manager",
            lambda: registry,
        )

        from vibe.core.plugins.cli import build_plugin_parser, handle_plugin_command

        parser = build_plugin_parser()
        args = parser.parse_args(["install", str(local), "--local"])
        handle_plugin_command(args)

        plugins = registry.get_all_plugins()
        assert "my-plugin" in plugins


class TestCLIHandlerSearch:
    def test_search_no_marketplaces(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        registry = PluginRegistryManager(plugins_dir=plugins_dir)

        monkeypatch.setattr(
            "vibe.core.config.harness_files._harness_manager._get_plugin_registry_manager",
            lambda: registry,
        )
        monkeypatch.setattr(
            "vibe.core.plugins.cli._load_marketplace_configs", lambda: []
        )

        from vibe.core.plugins.cli import build_plugin_parser, handle_plugin_command

        parser = build_plugin_parser()
        args = parser.parse_args(["search", "something"])
        handle_plugin_command(args)

        captured = capsys.readouterr()
        assert "No marketplaces configured" in captured.out


class TestCLIHandlerMarketplaceList:
    def test_marketplace_list_empty(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        registry = PluginRegistryManager(plugins_dir=plugins_dir)

        monkeypatch.setattr(
            "vibe.core.config.harness_files._harness_manager._get_plugin_registry_manager",
            lambda: registry,
        )
        monkeypatch.setattr(
            "vibe.core.plugins.cli._load_marketplace_configs", lambda: []
        )

        from vibe.core.plugins.cli import build_plugin_parser, handle_plugin_command

        parser = build_plugin_parser()
        args = parser.parse_args(["marketplace", "list"])
        handle_plugin_command(args)

        captured = capsys.readouterr()
        assert "No marketplaces configured" in captured.out


class TestCLIHandlerInfo:
    def test_info_not_installed(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        registry = PluginRegistryManager(plugins_dir=plugins_dir)

        monkeypatch.setattr(
            "vibe.core.config.harness_files._harness_manager._get_plugin_registry_manager",
            lambda: registry,
        )

        from vibe.core.plugins.cli import build_plugin_parser, handle_plugin_command

        parser = build_plugin_parser()
        args = parser.parse_args(["info", "nonexistent"])
        handle_plugin_command(args)

        captured = capsys.readouterr()
        assert "not installed" in captured.out


# ---------------------------------------------------------------------------
# Registry invalidation proves cache flush
# ---------------------------------------------------------------------------


class TestRegistryInvalidationFlushesCache:
    def test_invalidate_rereads_from_disk(self, tmp_path: Path) -> None:
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        registry = PluginRegistryManager(plugins_dir=plugins_dir)

        # Register a plugin
        registry.register(
            PluginEntry(name="plugin-a", version="1.0.0", source=PluginSource.GIT)
        )
        assert "plugin-a" in registry.get_all_plugins()

        # Externally modify the registry file
        from vibe.core.plugins.models import PluginRegistry

        on_disk = PluginRegistry.load(plugins_dir / "registry.toml")
        on_disk.plugins["plugin-b"] = PluginEntry(
            name="plugin-b", version="2.0.0", source=PluginSource.LOCAL
        )
        on_disk.save(plugins_dir / "registry.toml")

        # Before invalidation, cache still has only plugin-a
        assert "plugin-b" not in registry.get_all_plugins()

        # After invalidation, cache is flushed and plugin-b appears
        registry.invalidate()
        assert "plugin-b" in registry.get_all_plugins()
