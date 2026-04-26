from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from vibe.core.plugins.models import PluginManifest
from vibe.core.plugins.registry import PluginRegistryManager

_FRONTMATTER_PARTS = 3


@dataclass(frozen=True, slots=True)
class PluginCommand:
    """A slash command discovered from a plugin ``commands/`` directory."""

    name: str
    plugin_name: str
    description: str
    file_path: Path
    slash_name: str


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter delimiters from *text*, returning the body."""
    if text.startswith("---\n"):
        parts = text.split("---", maxsplit=2)
        if len(parts) >= _FRONTMATTER_PARTS:
            return parts[2]
    return text


def _extract_description(md_path: Path) -> str:
    """Extract a human-readable description from a ``.md`` command file.

    If the file begins with YAML frontmatter (``---`` delimiters), the
    ``description`` field is used.  Otherwise the first non-empty line is
    returned, stripping a leading ``#`` prefix for Markdown headers.
    """
    text = md_path.read_text(encoding="utf-8")

    if text.startswith("---\n"):
        parts = text.split("---", maxsplit=2)
        # parts: ['', frontmatter, body]
        if len(parts) >= _FRONTMATTER_PARTS:
            try:
                front = yaml.safe_load(parts[1])
            except yaml.YAMLError:
                front = None
            if isinstance(front, dict) and (desc := front.get("description")):
                return str(desc).strip()
            # Frontmatter present but no description — scan the body only
            text = parts[2]

    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
            return stripped

    return md_path.stem


def discover_all_plugin_commands(
    registry: PluginRegistryManager,
) -> dict[str, PluginCommand]:
    """Return ``{slash_name: PluginCommand}`` for every enabled plugin's commands."""
    commands: dict[str, PluginCommand] = {}

    for plugin_name, plugin_dir in registry.get_enabled_plugin_dirs().items():
        try:
            manifest = PluginManifest.from_dir(plugin_dir)
        except FileNotFoundError:
            continue

        resolved = manifest.resolve_paths(plugin_dir)

        for cmd_dir in resolved.command_dirs:
            for md_file in sorted(cmd_dir.glob("*.md")):
                cmd_name = md_file.stem.lower()
                slash_name = f"{plugin_name}:{cmd_name}"
                commands[slash_name] = PluginCommand(
                    name=cmd_name,
                    plugin_name=plugin_name,
                    description=_extract_description(md_file),
                    file_path=md_file,
                    slash_name=slash_name,
                )

    return commands
