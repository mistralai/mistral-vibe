from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
import pytest
import tomli_w

from vibe.core.plugins.models import (
    MarketplaceConfig,
    MarketplaceIndex,
    PluginManifest,
    PluginRegistry,
)


class TestPluginManifest:
    def test_from_dir_toml(self, tmp_path: Path) -> None:
        data = {"name": "my-plugin", "description": "A test plugin", "version": "1.0.0"}
        (tmp_path / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        manifest = PluginManifest.from_dir(tmp_path)
        assert manifest.name == "my-plugin"
        assert manifest.version == "1.0.0"

    def test_from_dir_json(self, tmp_path: Path) -> None:
        data = {"name": "my-plugin", "description": "A test plugin"}
        (tmp_path / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
        manifest = PluginManifest.from_dir(tmp_path)
        assert manifest.name == "my-plugin"

    def test_from_dir_toml_preferred_over_json(self, tmp_path: Path) -> None:
        toml_data = {"name": "from-toml", "description": "TOML version"}
        json_data = {"name": "from-json", "description": "JSON version"}
        (tmp_path / "plugin.toml").write_text(
            tomli_w.dumps(toml_data), encoding="utf-8"
        )
        (tmp_path / "plugin.json").write_text(json.dumps(json_data), encoding="utf-8")
        manifest = PluginManifest.from_dir(tmp_path)
        assert manifest.name == "from-toml"

    def test_from_dir_no_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No plugin.toml or plugin.json"):
            PluginManifest.from_dir(tmp_path)

    def test_from_dir_no_manifest_with_mcp_json(self, tmp_path: Path) -> None:
        mcp_data = {
            "mcpServers": {"my-server": {"command": "node", "args": ["index.js"]}}
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")
        manifest = PluginManifest.from_dir(tmp_path)
        assert manifest.name == tmp_path.name.lower().replace("_", "-")
        assert len(manifest.mcp_servers) == 1
        assert manifest.mcp_servers[0]["name"] == "my-server"
        assert manifest.mcp_servers[0]["transport"] == "stdio"

    def test_from_dir_no_manifest_with_skills_dir(self, tmp_path: Path) -> None:
        (tmp_path / "skills").mkdir()
        manifest = PluginManifest.from_dir(tmp_path)
        assert manifest.name == tmp_path.name.lower().replace("_", "-")
        assert manifest.skills == ["skills"]
        assert manifest.agents == []
        assert manifest.mcp_servers == []

    def test_from_dir_no_manifest_nothing_useful_raises(self, tmp_path: Path) -> None:
        (tmp_path / "random-file.txt").write_text("nothing", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="No plugin.toml or plugin.json"):
            PluginManifest.from_dir(tmp_path)

    def test_name_validation_rejects_uppercase(self) -> None:
        with pytest.raises(ValidationError):
            PluginManifest(name="MyPlugin", description="bad")

    def test_name_validation_rejects_spaces(self) -> None:
        with pytest.raises(ValidationError):
            PluginManifest(name="my plugin", description="bad")

    def test_name_validation_accepts_hyphens(self) -> None:
        m = PluginManifest(name="my-cool-plugin", description="good")
        assert m.name == "my-cool-plugin"

    def test_from_dir_loads_mcp_json(self, tmp_path: Path) -> None:
        toml_data = {"name": "mcp-plugin", "description": "has mcp.json"}
        (tmp_path / "plugin.toml").write_text(
            tomli_w.dumps(toml_data), encoding="utf-8"
        )
        mcp_data = {
            "mcpServers": {
                "my-server": {"command": "node", "args": ["server.js"], "env": {}}
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")

        manifest = PluginManifest.from_dir(tmp_path)

        assert len(manifest.mcp_servers) == 1
        assert manifest.mcp_servers[0]["name"] == "my-server"
        assert manifest.mcp_servers[0]["transport"] == "stdio"

    def test_from_dir_merges_inline_and_mcp_json(self, tmp_path: Path) -> None:
        toml_data = {
            "name": "merge-plugin",
            "description": "inline + file",
            "mcp_servers": [
                {
                    "name": "inline-srv",
                    "transport": "http",
                    "url": "http://localhost:8080",
                }
            ],
        }
        (tmp_path / "plugin.toml").write_text(
            tomli_w.dumps(toml_data), encoding="utf-8"
        )
        mcp_data = {
            "mcpServers": {"file-srv": {"command": "python", "args": ["-m", "server"]}}
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")

        manifest = PluginManifest.from_dir(tmp_path)

        assert len(manifest.mcp_servers) == 2
        assert manifest.mcp_servers[0]["name"] == "inline-srv"
        assert manifest.mcp_servers[1]["name"] == "file-srv"
        assert manifest.mcp_servers[1]["transport"] == "stdio"

    def test_mcp_json_type_to_transport_mapping(self, tmp_path: Path) -> None:
        toml_data = {"name": "type-map-plugin", "description": "type mapping"}
        (tmp_path / "plugin.toml").write_text(
            tomli_w.dumps(toml_data), encoding="utf-8"
        )
        mcp_data = {
            "mcpServers": {
                "http-srv": {"type": "http", "url": "http://localhost:3000"},
                "sse-srv": {"type": "sse", "url": "http://localhost:3001"},
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp_data), encoding="utf-8")

        manifest = PluginManifest.from_dir(tmp_path)

        by_name = {s["name"]: s for s in manifest.mcp_servers}
        assert by_name["http-srv"]["transport"] == "http"
        assert by_name["sse-srv"]["transport"] == "http"
        assert "type" not in by_name["http-srv"]
        assert "type" not in by_name["sse-srv"]

    def test_mcp_json_missing_file_is_noop(self, tmp_path: Path) -> None:
        toml_data = {"name": "no-mcp-json", "description": "no .mcp.json file"}
        (tmp_path / "plugin.toml").write_text(
            tomli_w.dumps(toml_data), encoding="utf-8"
        )

        manifest = PluginManifest.from_dir(tmp_path)

        assert manifest.mcp_servers == []

    def test_resolve_paths_existing_dirs(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()
        manifest = PluginManifest(
            name="test-plugin",
            description="test",
            skills=["skills"],
            agents=["agents"],
            tools=["tools"],
        )
        resolved = manifest.resolve_paths(plugin_dir)
        assert skills_dir.resolve() in resolved.skill_dirs
        assert agents_dir.resolve() in resolved.agent_dirs
        assert resolved.tool_dirs == []

    def test_resolve_paths_rejects_traversal(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        manifest = PluginManifest(
            name="evil-plugin", description="test", skills=["../outside"]
        )
        resolved = manifest.resolve_paths(plugin_dir)
        assert resolved.skill_dirs == []


class TestPluginRegistry:
    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        registry = PluginRegistry.load(tmp_path / "nonexistent.toml")
        assert registry.plugins == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        from vibe.core.plugins.models import PluginEntry, PluginSource

        registry = PluginRegistry()
        registry.plugins["test"] = PluginEntry(
            name="test", version="1.0.0", source=PluginSource.GIT
        )
        path = tmp_path / "registry.toml"
        registry.save(path)
        loaded = PluginRegistry.load(path)
        assert "test" in loaded.plugins
        assert loaded.plugins["test"].version == "1.0.0"


class TestMarketplaceIndex:
    def test_from_dir_toml(self, tmp_path: Path) -> None:
        data = {
            "name": "test-marketplace",
            "plugins": [
                {"name": "plugin-a", "source": "plugins/a", "description": "A"}
            ],
        }
        (tmp_path / "marketplace.toml").write_text(
            tomli_w.dumps(data), encoding="utf-8"
        )
        index = MarketplaceIndex.from_dir(tmp_path)
        assert index.name == "test-marketplace"
        assert len(index.plugins) == 1

    def test_from_dir_json(self, tmp_path: Path) -> None:
        data = {"name": "test-marketplace", "plugins": []}
        (tmp_path / "marketplace.json").write_text(json.dumps(data), encoding="utf-8")
        index = MarketplaceIndex.from_dir(tmp_path)
        assert index.name == "test-marketplace"

    def test_from_dir_no_index_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            MarketplaceIndex.from_dir(tmp_path)

    def test_dict_source_coerced_to_string(self, tmp_path: Path) -> None:
        data = {
            "name": "test-marketplace",
            "plugins": [
                {
                    "name": "azure-skills",
                    "source": {
                        "source": "github",
                        "repo": "owner/repo",
                        "path": "plugins/azure-skills",
                    },
                    "description": "Azure skills",
                }
            ],
        }
        (tmp_path / "marketplace.json").write_text(json.dumps(data), encoding="utf-8")
        index = MarketplaceIndex.from_dir(tmp_path)
        assert index.plugins[0].source == "github/owner/repo/plugins/azure-skills"

    def test_plugin_root_from_metadata(self, tmp_path: Path) -> None:
        data = {
            "name": "test-marketplace",
            "metadata": {"pluginRoot": "./plugins"},
            "plugins": [{"name": "my-plug", "source": "my-plug", "description": "My"}],
        }
        (tmp_path / "marketplace.json").write_text(json.dumps(data), encoding="utf-8")
        index = MarketplaceIndex.from_dir(tmp_path)
        assert index.plugin_root == "./plugins"

    def test_plugin_root_defaults_empty(self, tmp_path: Path) -> None:
        data = {"name": "test-marketplace", "plugins": []}
        (tmp_path / "marketplace.json").write_text(json.dumps(data), encoding="utf-8")
        index = MarketplaceIndex.from_dir(tmp_path)
        assert index.plugin_root == ""


class TestMarketplaceConfig:
    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValidationError):
            MarketplaceConfig(url="")


class TestAuthorCoercion:
    def test_string_passthrough(self, tmp_path: Path) -> None:
        data = {"name": "test-plug", "description": "Test", "author": "Alice"}
        (tmp_path / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        assert PluginManifest.from_dir(tmp_path).author == "Alice"

    def test_dict_extracts_name(self, tmp_path: Path) -> None:
        data = {
            "name": "test-plug",
            "description": "Test",
            "author": {"name": "Bob", "email": "bob@example.com"},
        }
        (tmp_path / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
        assert PluginManifest.from_dir(tmp_path).author == "Bob"

    def test_missing_defaults_empty(self, tmp_path: Path) -> None:
        data = {"name": "test-plug", "description": "Test"}
        (tmp_path / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")
        assert PluginManifest.from_dir(tmp_path).author == ""


class TestManifestDiscovery:
    def test_github_plugin_dir(self, tmp_path: Path) -> None:
        sub = tmp_path / ".github" / "plugin"
        sub.mkdir(parents=True)
        data = {"name": "gh-plug", "description": "GitHub plugin"}
        (sub / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
        assert PluginManifest.from_dir(tmp_path).name == "gh-plug"

    def test_claude_plugin_dir(self, tmp_path: Path) -> None:
        sub = tmp_path / ".claude-plugin"
        sub.mkdir(parents=True)
        data = {"name": "claude-plug", "description": "Claude plugin"}
        (sub / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
        assert PluginManifest.from_dir(tmp_path).name == "claude-plug"

    def test_root_takes_priority(self, tmp_path: Path) -> None:
        (tmp_path / "plugin.toml").write_text(
            tomli_w.dumps({"name": "root-plug", "description": "Root"}),
            encoding="utf-8",
        )
        sub = tmp_path / ".github" / "plugin"
        sub.mkdir(parents=True)
        (sub / "plugin.json").write_text(
            json.dumps({"name": "gh-plug", "description": "GH"}), encoding="utf-8"
        )
        assert PluginManifest.from_dir(tmp_path).name == "root-plug"

    def test_mcp_json_loaded_from_discovered_subdir(self, tmp_path: Path) -> None:
        sub = tmp_path / ".claude-plugin"
        sub.mkdir(parents=True)
        (sub / "plugin.json").write_text(
            json.dumps({"name": "mcp-plug", "description": "MCP test"}),
            encoding="utf-8",
        )
        mcp = {"mcpServers": {"srv": {"command": "node", "args": ["s.js"]}}}
        (sub / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
        manifest = PluginManifest.from_dir(tmp_path)
        assert len(manifest.mcp_servers) == 1
        assert manifest.mcp_servers[0]["name"] == "srv"

    def test_rejects_url_starting_with_dash(self) -> None:
        with pytest.raises(ValidationError):
            MarketplaceConfig(url="--evil")

    def test_rejects_url_without_slash(self) -> None:
        with pytest.raises(ValidationError):
            MarketplaceConfig(url="noslash")

    def test_accepts_owner_repo(self) -> None:
        config = MarketplaceConfig(url="owner/repo")
        assert config.url == "owner/repo"

    def test_accepts_https_url(self) -> None:
        config = MarketplaceConfig(url="https://github.com/test/repo")
        assert config.url == "https://github.com/test/repo"

    def test_accepts_git_ssh_url(self) -> None:
        config = MarketplaceConfig(url="git@github.com:owner/repo.git")
        assert config.url == "git@github.com:owner/repo.git"


class TestPluginEntryNoScope:
    def test_plugin_entry_no_scope_field(self) -> None:
        from vibe.core.plugins.models import PluginEntry, PluginSource

        entry = PluginEntry(name="test", source=PluginSource.GIT)
        assert "scope" not in PluginEntry.model_fields
        assert not hasattr(entry, "scope")

    def test_accepts_valid_url(self) -> None:
        config = MarketplaceConfig(url="https://github.com/test/repo")
        assert config.url == "https://github.com/test/repo"
