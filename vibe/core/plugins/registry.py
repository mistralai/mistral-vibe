from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibe.core.logger import logger
from vibe.core.paths import VIBE_HOME
from vibe.core.plugins.models import (
    PluginEntry,
    PluginManifest,
    PluginRegistry,
    PluginScope,
    PluginSource,
)

if TYPE_CHECKING:
    from vibe.core.plugins.models import ResolvedPluginPaths


class PluginRegistryManager:
    """Manages the plugin registry and aggregates paths from enabled plugins.

    Supports multiple installation scopes (USER, PROJECT, LOCAL).  When
    plugins share a name across scopes the priority order is
    USER > LOCAL > PROJECT (last writer wins during merge).
    """

    def __init__(
        self,
        plugins_dir: Path | None = None,
        *,
        project_plugins_dir: Path | None = None,
        local_plugins_dir: Path | None = None,
    ) -> None:
        self._scope_dirs: dict[PluginScope, Path] = {}
        self._scope_dirs[PluginScope.USER] = plugins_dir or VIBE_HOME.path / "plugins"
        if project_plugins_dir:
            self._scope_dirs[PluginScope.PROJECT] = project_plugins_dir
        if local_plugins_dir:
            self._scope_dirs[PluginScope.LOCAL] = local_plugins_dir
        self._cached_registries: dict[PluginScope, PluginRegistry] | None = None
        self._cached_resolved: (
            list[tuple[PluginEntry, PluginManifest, ResolvedPluginPaths]] | None
        ) = None
        self._dev_plugins: dict[str, Path] = {}

    # ------------------------------------------------------------------
    # Registry loading / caching
    # ------------------------------------------------------------------

    def _load_registries(self) -> dict[PluginScope, PluginRegistry]:
        if self._cached_registries is not None:
            return self._cached_registries
        regs: dict[PluginScope, PluginRegistry] = {}
        for scope, d in self._scope_dirs.items():
            regs[scope] = PluginRegistry.load(d / "registry.toml")
        self._cached_registries = regs
        return regs

    def _merged_plugins(self) -> dict[str, tuple[PluginScope, PluginEntry]]:
        """Return ``{name: (scope, entry)}`` with USER > LOCAL > PROJECT priority."""
        merged: dict[str, tuple[PluginScope, PluginEntry]] = {}
        for scope in (PluginScope.PROJECT, PluginScope.LOCAL, PluginScope.USER):
            if (reg := self._load_registries().get(scope)) is None:
                continue
            for name, entry in reg.plugins.items():
                merged[name] = (scope, entry)
        return merged

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def plugins_dir(self) -> Path:
        """USER scope directory — kept for backward compatibility."""
        return self._scope_dirs[PluginScope.USER]

    def get_plugins_dir_for_scope(self, scope: PluginScope) -> Path:
        """Return the directory for *scope* or raise ``KeyError``."""
        if scope not in self._scope_dirs:
            msg = f"Scope '{scope}' is not configured"
            raise KeyError(msg)
        return self._scope_dirs[scope]

    def get_enabled_plugin_dirs(self) -> dict[str, Path]:
        """Return ``{name: path}`` for all enabled plugins, including dev overrides."""
        dirs: dict[str, Path] = {
            name: self._scope_dirs[scope] / name
            for name, (scope, entry) in self._merged_plugins().items()
            if entry.enabled
        }
        # Dev plugins take highest priority; add or override installed entries
        dirs.update(self._dev_plugins)
        return dirs

    def get_enabled_plugins(self) -> dict[str, PluginEntry]:
        return {
            name: entry
            for name, (_, entry) in self._merged_plugins().items()
            if entry.enabled
        }

    def get_all_plugins(self) -> dict[str, PluginEntry]:
        return {name: entry for name, (_, entry) in self._merged_plugins().items()}

    def get_all_plugins_with_scope(self) -> dict[str, tuple[PluginScope, PluginEntry]]:
        """Return all plugins with their resolved scope."""
        return dict(self._merged_plugins())

    def get_plugin_dir(self, name: str) -> Path | None:
        if (hit := self._merged_plugins().get(name)) is not None:
            scope, _ = hit
            return self._scope_dirs[scope] / name
        return None

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(
        self, entry: PluginEntry, scope: PluginScope = PluginScope.USER
    ) -> None:
        regs = self._load_registries()
        if scope not in regs:
            msg = f"Scope '{scope}' is not configured"
            raise KeyError(msg)
        regs[scope].plugins[entry.name] = entry
        self._cached_resolved = None
        self._save_scope(scope)

    def unregister(self, name: str) -> None:
        merged = self._merged_plugins()
        if name not in merged:
            return
        scope, _ = merged[name]
        registries = self._load_registries()
        registries[scope].plugins.pop(name, None)
        self._cached_resolved = None
        self._save_scope(scope)

    def set_enabled(self, name: str, *, enabled: bool) -> None:
        merged = self._merged_plugins()
        if name not in merged:
            msg = f"Plugin '{name}' is not registered"
            raise KeyError(msg)
        scope, _ = merged[name]
        registries = self._load_registries()
        registries[scope].plugins[name].enabled = enabled
        self._cached_resolved = None
        self._save_scope(scope)

    def _save_scope(self, scope: PluginScope) -> None:
        reg = self._load_registries()[scope]
        reg.save(self._scope_dirs[scope] / "registry.toml")

    def invalidate(self) -> None:
        self._cached_registries = None
        self._cached_resolved = None

    def add_dev_plugin(self, path: Path) -> None:
        """Load a plugin directory for development without installing it.

        Dev plugins take highest priority and override installed plugins of the same name.
        """
        path = path.resolve()
        manifest = PluginManifest.from_dir(path)
        self._dev_plugins[manifest.name] = path
        self._cached_resolved = None

    # ------------------------------------------------------------------
    # Path aggregation
    # ------------------------------------------------------------------

    def _resolve_enabled(
        self,
    ) -> list[tuple[PluginEntry, PluginManifest, ResolvedPluginPaths]]:
        if self._cached_resolved is not None:
            return self._cached_resolved
        results: list[tuple[PluginEntry, PluginManifest, ResolvedPluginPaths]] = []
        seen_names: set[str] = set()

        # Dev plugins take highest priority
        for name, path in self._dev_plugins.items():
            try:
                manifest = PluginManifest.from_dir(path)
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("Skipping dev plugin '%s': %s", name, exc)
                continue
            resolved = manifest.resolve_paths(path)
            dev_entry = PluginEntry(
                name=name,
                version=manifest.version,
                source=PluginSource.LOCAL,
                enabled=True,
            )
            results.append((dev_entry, manifest, resolved))
            seen_names.add(name)

        # Then registered plugins (skip if overridden by dev)
        for name, entry in self.get_enabled_plugins().items():
            if name in seen_names:
                continue
            plugin_dir = self.get_plugin_dir(name)
            if plugin_dir is None:
                logger.warning("Skipping plugin '%s': not found in any scope", name)
                continue
            try:
                manifest = PluginManifest.from_dir(plugin_dir)
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("Skipping plugin '%s': %s", name, exc)
                continue
            resolved = manifest.resolve_paths(plugin_dir)
            results.append((entry, manifest, resolved))

        self._cached_resolved = results
        return results

    def get_all_skill_dirs(self) -> list[Path]:
        return [p for _, _, rp in self._resolve_enabled() for p in rp.skill_dirs]

    def get_all_agent_dirs(self) -> list[Path]:
        return [p for _, _, rp in self._resolve_enabled() for p in rp.agent_dirs]

    def get_all_tool_dirs(self) -> list[Path]:
        return [p for _, _, rp in self._resolve_enabled() for p in rp.tool_dirs]

    def get_all_command_dirs(self) -> list[Path]:
        return [p for _, _, rp in self._resolve_enabled() for p in rp.command_dirs]

    def get_all_mcp_servers(self) -> list[dict[str, Any]]:
        return [s for _, m, _ in self._resolve_enabled() for s in m.mcp_servers]
