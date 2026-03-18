from __future__ import annotations

from pathlib import Path

import tomli_w

from vibe.core.plugins.commands import (
    _extract_description,
    discover_all_plugin_commands,
)
from vibe.core.plugins.models import PluginEntry, PluginRegistry, PluginSource
from vibe.core.plugins.registry import PluginRegistryManager


def _write_plugin(
    plugins_dir: Path,
    name: str,
    *,
    commands: dict[str, str] | None = None,
    create_manifest: bool = True,
) -> Path:
    """Create a minimal plugin directory with optional command files."""
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if create_manifest:
        data = {
            "name": name,
            "description": f"Test plugin {name}",
            "commands": ["commands"],
        }
        (plugin_dir / "plugin.toml").write_text(tomli_w.dumps(data), encoding="utf-8")

    cmd_dir = plugin_dir / "commands"
    cmd_dir.mkdir(exist_ok=True)

    for filename, content in (commands or {}).items():
        (cmd_dir / filename).write_text(content, encoding="utf-8")

    return plugin_dir


def _register_plugin(plugins_dir: Path, name: str, *, enabled: bool = True) -> None:
    """Write/update a registry.toml with the given plugin entry."""
    registry = PluginRegistry.load(plugins_dir / "registry.toml")
    registry.plugins[name] = PluginEntry(
        name=name, source=PluginSource.LOCAL, enabled=enabled
    )
    registry.save(plugins_dir / "registry.toml")


class TestExtractDescription:
    def test_extract_description_from_frontmatter(self, tmp_path: Path) -> None:
        md = tmp_path / "review.md"
        md.write_text(
            '---\ndescription: "Review code"\n---\nBody text\n', encoding="utf-8"
        )

        assert _extract_description(md) == "Review code"

    def test_extract_description_from_header(self, tmp_path: Path) -> None:
        md = tmp_path / "review.md"
        md.write_text("# Review Code\nSome body text\n", encoding="utf-8")

        assert _extract_description(md) == "Review Code"

    def test_extract_description_from_plain_text(self, tmp_path: Path) -> None:
        md = tmp_path / "review.md"
        md.write_text(
            "Run the linter on the project\n\nMore details\n", encoding="utf-8"
        )

        assert _extract_description(md) == "Run the linter on the project"

    def test_extract_description_empty_file(self, tmp_path: Path) -> None:
        md = tmp_path / "review.md"
        md.write_text("", encoding="utf-8")

        assert _extract_description(md) == "review"


class TestDiscoverPluginCommands:
    def test_discover_commands_from_plugin(self, plugins_dir: Path) -> None:
        _write_plugin(
            plugins_dir,
            "test-plugin",
            commands={
                "review.md": '---\ndescription: "Review code"\n---\n',
                "lint.md": "# Lint Project\n",
            },
        )
        _register_plugin(plugins_dir, "test-plugin", enabled=True)

        mgr = PluginRegistryManager(plugins_dir=plugins_dir)
        result = discover_all_plugin_commands(mgr)

        assert len(result) == 2
        assert "test-plugin:review" in result
        assert "test-plugin:lint" in result

        review = result["test-plugin:review"]
        assert review.name == "review"
        assert review.plugin_name == "test-plugin"
        assert review.description == "Review code"
        assert review.slash_name == "test-plugin:review"

        lint = result["test-plugin:lint"]
        assert lint.description == "Lint Project"

    def test_discover_commands_empty_commands_dir(self, plugins_dir: Path) -> None:
        _write_plugin(plugins_dir, "test-plugin", commands={})
        _register_plugin(plugins_dir, "test-plugin", enabled=True)

        mgr = PluginRegistryManager(plugins_dir=plugins_dir)
        result = discover_all_plugin_commands(mgr)

        assert result == {}

    def test_discover_commands_disabled_plugin_excluded(
        self, plugins_dir: Path
    ) -> None:
        _write_plugin(plugins_dir, "test-plugin", commands={"review.md": "# Review\n"})
        _register_plugin(plugins_dir, "test-plugin", enabled=False)

        mgr = PluginRegistryManager(plugins_dir=plugins_dir)
        result = discover_all_plugin_commands(mgr)

        assert result == {}

    def test_discover_commands_missing_manifest_skipped(
        self, plugins_dir: Path
    ) -> None:
        _write_plugin(
            plugins_dir,
            "test-plugin",
            commands={"review.md": "# Review\n"},
            create_manifest=False,
        )
        _register_plugin(plugins_dir, "test-plugin", enabled=True)

        mgr = PluginRegistryManager(plugins_dir=plugins_dir)
        result = discover_all_plugin_commands(mgr)

        assert result == {}


class TestExtractDescriptionMalformedYaml:
    def test_extract_description_malformed_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML frontmatter must not crash; the function falls through to plain-text extraction."""
        md = tmp_path / "broken.md"
        md.write_text("---\n: : :\n---\nBody text\n", encoding="utf-8")

        result = _extract_description(md)
        # The function doesn't crash and returns *some* string (falls through
        # to the line-scanning loop whose first non-empty hit is the `---` delimiter).
        assert isinstance(result, str)
        assert len(result) > 0
