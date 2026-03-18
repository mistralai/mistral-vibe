from __future__ import annotations

from enum import StrEnum, auto
import json
from pathlib import Path
import tomllib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
import tomli_w

_MANIFEST_SEARCH_DIRS = (".", ".github/plugin", ".claude-plugin")

PLUGIN_NAME_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class PluginSource(StrEnum):
    GIT = auto()
    LOCAL = auto()
    MARKETPLACE = auto()


class ResolvedPluginPaths(BaseModel):
    """Absolute paths produced by resolving a manifest against its plugin directory."""

    skill_dirs: list[Path] = Field(default_factory=list)
    agent_dirs: list[Path] = Field(default_factory=list)
    tool_dirs: list[Path] = Field(default_factory=list)
    command_dirs: list[Path] = Field(default_factory=list)


class PluginManifest(BaseModel):
    """Parsed from ``plugin.toml`` or ``plugin.json``."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=PLUGIN_NAME_PATTERN,
        description="Plugin identifier. Lowercase letters, numbers, and hyphens only.",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Short description of the plugin.",
    )
    version: str = "0.0.0"
    author: str = ""
    license: str | None = None
    repository: str | None = None
    keywords: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=lambda: ["skills"])
    agents: list[str] = Field(default_factory=lambda: ["agents"])
    tools: list[str] = Field(default_factory=lambda: ["tools"])
    commands: list[str] = Field(default_factory=lambda: ["commands"])
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("author", mode="before")
    @classmethod
    def _coerce_author(cls, v: Any) -> str:
        match v:
            case {"name": str() as name}:
                return name
            case str():
                return v
            case _:
                return ""

    @staticmethod
    def load_mcp_json(plugin_dir: Path) -> list[dict[str, Any]]:
        """Load MCP server definitions from ``.mcp.json`` (Claude Code convention)."""
        mcp_path = plugin_dir / ".mcp.json"
        if not mcp_path.is_file():
            return []

        with mcp_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)

        servers: list[dict[str, Any]] = []
        known_keys = {
            "name",
            "transport",
            "type",
            "command",
            "url",
            "args",
            "env",
            "cwd",
        }
        for name, cfg in raw.get("mcpServers", {}).items():
            entry = {k: v for k, v in cfg.items() if k in known_keys}
            entry["name"] = name
            if "type" in entry:
                match entry.pop("type"):
                    case "sse":
                        entry.setdefault("transport", "http")
                    case other:
                        entry.setdefault("transport", other)
            elif "command" in entry and "transport" not in entry:
                entry["transport"] = "stdio"
            servers.append(entry)
        return servers

    @classmethod
    def from_dir(cls, plugin_dir: Path) -> PluginManifest:
        """Load manifest from *plugin_dir*, trying TOML then JSON.

        Searches the directory root first, then known plugin convention
        sub-directories (``.github/plugin/``, ``.claude-plugin/``).

        Raises:
            FileNotFoundError: No manifest found in any searched location.
        """
        for rel in _MANIFEST_SEARCH_DIRS:
            candidate = plugin_dir / rel
            if not candidate.resolve().is_relative_to(plugin_dir.resolve()):
                continue
            toml_path = candidate / "plugin.toml"
            json_path = candidate / "plugin.json"

            if toml_path.is_file():
                with toml_path.open("rb") as fh:
                    data = tomllib.load(fh)
                manifest = cls.model_validate(data)
                manifest.mcp_servers.extend(cls.load_mcp_json(candidate))
                return manifest
            if json_path.is_file():
                with json_path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                manifest = cls.model_validate(data)
                manifest.mcp_servers.extend(cls.load_mcp_json(candidate))
                return manifest

        return cls._synthesize_from_dir(plugin_dir)

    @classmethod
    def _synthesize_from_dir(cls, plugin_dir: Path) -> PluginManifest:
        """Build a minimal manifest from directory contents when no manifest file exists.

        Raises:
            FileNotFoundError: No useful content found in *plugin_dir*.
        """
        mcp_servers = cls.load_mcp_json(plugin_dir)
        asset_dirs = {
            d
            for d in ("skills", "agents", "tools", "commands")
            if (plugin_dir / d).is_dir()
        }

        if not mcp_servers and not asset_dirs:
            msg = f"No plugin.toml or plugin.json found in {plugin_dir}"
            raise FileNotFoundError(msg)

        name = plugin_dir.name.lower().replace("_", "-")
        return cls(
            name=name,
            description=f"Auto-discovered plugin from {plugin_dir.name}",
            mcp_servers=mcp_servers,
            skills=["skills"] if "skills" in asset_dirs else [],
            agents=["agents"] if "agents" in asset_dirs else [],
            tools=["tools"] if "tools" in asset_dirs else [],
            commands=["commands"] if "commands" in asset_dirs else [],
        )

    def resolve_paths(self, plugin_dir: Path) -> ResolvedPluginPaths:
        """Resolve relative manifest paths to absolute, keeping only existing dirs."""
        resolved_root = plugin_dir.resolve()

        def _resolve(relative_paths: list[str]) -> list[Path]:
            resolved: list[Path] = []
            for rel in relative_paths:
                candidate = (plugin_dir / rel).resolve()
                if not candidate.is_relative_to(resolved_root):
                    continue
                if candidate.is_dir():
                    resolved.append(candidate)
            return resolved

        return ResolvedPluginPaths(
            skill_dirs=_resolve(self.skills),
            agent_dirs=_resolve(self.agents),
            tool_dirs=_resolve(self.tools),
            command_dirs=_resolve(self.commands),
        )


class PluginScope(StrEnum):
    USER = auto()
    PROJECT = auto()
    LOCAL = auto()


class PluginEntry(BaseModel):
    """Tracks one installed plugin in the registry."""

    name: str = Field(..., min_length=1, pattern=PLUGIN_NAME_PATTERN)
    version: str = "0.0.0"
    source: PluginSource
    source_uri: str = ""
    enabled: bool = True
    installed_at: str = ""
    pinned_ref: str | None = None


class PluginRegistry(BaseModel):
    """Persisted as ``registry.toml`` inside the plugins directory."""

    plugins: dict[str, PluginEntry] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> PluginRegistry:
        """Load registry from *path*. Returns empty registry when file is missing."""
        if not path.is_file():
            return cls()
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        raw_plugins: dict[str, Any] = data.get("plugins", {})
        entries = {
            name: PluginEntry.model_validate(entry)
            for name, entry in raw_plugins.items()
        }
        return cls(plugins=entries)

    def save(self, path: Path) -> None:
        """Write registry to *path* in TOML format."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "plugins": {
                name: entry.model_dump(mode="json", exclude_none=True)
                for name, entry in self.plugins.items()
            }
        }
        with path.open("wb") as fh:
            tomli_w.dump(payload, fh)


class MarketplacePluginRef(BaseModel):
    """Reference to a single plugin within a marketplace index."""

    model_config = ConfigDict(extra="ignore")

    name: str
    source: str
    description: str = ""
    version: str = "0.0.0"

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source(cls, v: Any) -> str:
        """Accept dict-style source (e.g. {"source": "github", "repo": "...", "path": "..."})."""
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            parts = [v.get("source", ""), v.get("repo", ""), v.get("path", "")]
            return "/".join(p for p in parts if p)
        return str(v)


class _MarketplaceMetadata(BaseModel):
    """Optional ``metadata`` block inside a marketplace index."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    description: str = ""
    version: str = "0.0.0"
    plugin_root: str = Field("", alias="pluginRoot")


class MarketplaceIndex(BaseModel):
    """Parsed from ``marketplace.toml`` or ``marketplace.json``."""

    name: str
    description: str = ""
    version: str = "0.0.0"
    plugin_root: str = ""
    plugins: list[MarketplacePluginRef] = Field(default_factory=list)

    @classmethod
    def from_dir(cls, marketplace_dir: Path) -> MarketplaceIndex:
        """Load index from *marketplace_dir*, trying TOML then JSON.

        Searches the directory root first, then known plugin convention
        sub-directories (``.github/plugin/``, ``.claude-plugin/``).

        Raises:
            FileNotFoundError: No marketplace index found in any searched location.
        """
        for rel in _MANIFEST_SEARCH_DIRS:
            candidate = marketplace_dir / rel
            if not candidate.resolve().is_relative_to(marketplace_dir.resolve()):
                continue
            toml_path = candidate / "marketplace.toml"
            json_path = candidate / "marketplace.json"

            if toml_path.is_file():
                with toml_path.open("rb") as fh:
                    data = tomllib.load(fh)
                return cls._parse_index(data)
            if json_path.is_file():
                with json_path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return cls._parse_index(data)

        msg = f"No marketplace.toml or marketplace.json found in {marketplace_dir}"
        raise FileNotFoundError(msg)

    @classmethod
    def _parse_index(cls, data: dict[str, Any]) -> MarketplaceIndex:
        """Parse raw index data, extracting ``metadata.pluginRoot`` if present."""
        if metadata := data.get("metadata"):
            meta = _MarketplaceMetadata.model_validate(metadata)
            data.setdefault("plugin_root", meta.plugin_root)
        return cls.model_validate(data)


class MarketplaceConfig(BaseModel):
    """Marketplace entry used inside ``VibeConfig``."""

    url: str
    name: str = ""

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not v:
            msg = "Marketplace URL must not be empty"
            raise ValueError(msg)
        if v.startswith("-"):
            msg = "Marketplace URL must not start with '-'"
            raise ValueError(msg)
        # Accept owner/repo shorthand, https://, http://, git@ formats
        if not ("/" in v or v.startswith(("https://", "http://", "git@"))):
            msg = (
                "Marketplace URL must be owner/repo, https://, http://, or git@ format"
            )
            raise ValueError(msg)
        return v
