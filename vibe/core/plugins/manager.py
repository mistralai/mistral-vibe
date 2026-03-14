"""vibe/core/plugins/manager.py

─────────────────────────────────────────────────────────────────────────────
PluginManager — discovers, instantiates, and manages Vibe plugins.

Discovery rules (in priority order, first match wins per plugin name)
──────────────────────────────────────────────────────────────────────
1. Paths listed in ``config.plugin_paths``          (user overrides)
2. ``{VIBE_HOME}/plugins/``                         (user global plugins)
3. ``{workdir}/.vibe/plugins/``                     (project-local plugins)
4. ``vibe.core.plugins.builtin``                    (built-in plugins)

Each search path is scanned for Python packages/modules that contain a
class that:
  • inherits from :class:`~vibe.core.plugins.base.VibePlugin`
  • is not abstract (i.e. can be instantiated)
  • is not the base class itself

Filtering (same semantics as tool/skill filtering in VibeConfig):
  • ``enabled_plugins``  → whitelist (supports exact names, globs, regex)
  • ``disabled_plugins`` → blacklist applied after whitelist
"""

from __future__ import annotations

import fnmatch
import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING

from vibe.core.plugins.base import PluginContext, ToolEventPlugin, VibePlugin
from vibe.core.plugins.command_plugin import CommandPlugin

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig

logger = logging.getLogger(__name__)

# Built-in plugins package (shipped with Vibe)
_BUILTIN_PACKAGE = "vibe.core.plugins.builtin"


class PluginManager:
    """Manages the full lifecycle of Vibe plugins.

    Parameters
    ----------
    config:
        Live :class:`~vibe.core.config.VibeConfig` instance.
    context:
        :class:`~vibe.core.plugins.base.PluginContext` shared with all plugins.
    command_registry:
        Optional CommandRegistry to register plugin commands into.
    """

    def __init__(
        self, config: VibeConfig, context: PluginContext, command_registry=None
    ) -> None:
        self._config = config
        self._context = context
        self._command_registry = command_registry
        self._plugins: list[VibePlugin] = []
        self._tool_event_plugins: list[ToolEventPlugin] = []

    # ── Public API ────────────────────────────────────────────────────────────

    async def discover_and_setup(self) -> None:
        """Discover all eligible plugins and call :meth:`VibePlugin.setup` on each active one.

        This method is idempotent; calling it twice rebuilds the plugin list
        from scratch.
        """
        self._plugins = []
        self._tool_event_plugins = []

        classes = self._discover_plugin_classes()

        for cls in classes:
            meta = cls.metadata()
            if not self._is_enabled(meta.name):
                logger.debug("Plugin %s disabled by config", meta.name)
                continue
            try:
                instance: VibePlugin = cls()
            except Exception:
                logger.exception("Failed to instantiate plugin %s", meta.name)
                continue

            if not instance.is_applicable(self._context):
                logger.debug("Plugin %s not applicable to current context", meta.name)
                continue

            try:
                await instance.setup(self._context)
            except Exception:
                logger.exception("Plugin %s raised during setup", meta.name)
                continue

            self._plugins.append(instance)
            if isinstance(instance, ToolEventPlugin):
                self._tool_event_plugins.append(instance)

            # Register commands if this plugin implements CommandPlugin
            if isinstance(instance, CommandPlugin) and self._command_registry:
                try:
                    await instance.register_commands(self._command_registry)
                    logger.debug("Registered commands from plugin %s", meta.name)

                    # Auto-register handler methods for commands added by this plugin
                    # We need to track which commands were just added, but since we can't easily
                    # know that, we'll register handlers only if they exist on the plugin instance
                    for _cmd_name, command in self._command_registry.commands.items():
                        if hasattr(instance, command.handler):
                            handler_method = getattr(instance, command.handler)
                            # Only register if not already registered (to avoid overwriting built-in commands)
                            if command.handler not in self._command_registry._handler_map:
                                self._command_registry.register_handler(command.handler, handler_method)
                except Exception:
                    logger.exception("Plugin %s raised during command registration", meta.name)

            logger.info("Plugin %s (%s) activated", meta.name, meta.version)

    async def teardown_all(self) -> None:
        """Call :meth:`VibePlugin.teardown` on every active plugin."""
        for plugin in reversed(self._plugins):
            try:
                await plugin.teardown()
            except Exception:
                logger.exception("Plugin %s raised during teardown", plugin.metadata().name)
        self._plugins = []
        self._tool_event_plugins = []

    @property
    def tool_event_plugins(self) -> list[ToolEventPlugin]:
        """Active plugins that implement :class:`ToolEventPlugin`."""
        return list(self._tool_event_plugins)

    @property
    def all_plugins(self) -> list[VibePlugin]:
        """All active plugins."""
        return list(self._plugins)

    def summary(self) -> str:
        """Return a human-readable summary of active plugins in markdown format."""
        if not self._plugins:
            return "No plugins active."
        lines = ["## Plugins"]
        for p in self._plugins:
            m = p.metadata()
            lines.append("")
            lines.append(f"### {m.name}")
            lines.append(m.description)
        return "\n".join(lines)

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _search_paths(self) -> list[Path]:
        """Return ordered list of directories to scan for plugin classes."""
        paths: list[Path] = []

        # 1. User-configured extra paths
        for p in getattr(self._config, "plugin_paths", []):
            paths.append(Path(p).expanduser().resolve())

        # 2. VIBE_HOME/plugins
        from vibe.core.paths._vibe_home import VIBE_HOME  # type: ignore[import]

        paths.append(VIBE_HOME.path / "plugins")

        # 3. Project-local .vibe/plugins
        paths.append(self._context.workdir / ".vibe" / "plugins")

        return [p for p in paths if p.is_dir()]

    def _discover_plugin_classes(self) -> list[type[VibePlugin]]:
        """Return all unique, non-abstract VibePlugin subclasses found."""
        found: dict[str, type[VibePlugin]] = {}  # name → class

        # Built-ins first (lowest priority — overridable by user paths)
        for cls in self._scan_package(_BUILTIN_PACKAGE):
            found[cls.metadata().name] = cls

        # File-system paths (higher priority — later paths win per name)
        for path in self._search_paths():
            for cls in self._scan_directory(path):
                found[cls.metadata().name] = cls

        return list(found.values())

    @staticmethod
    def _scan_package(package_name: str) -> list[type[VibePlugin]]:
        """Import all submodules of a package and collect plugin classes."""
        try:
            pkg = importlib.import_module(package_name)
        except ImportError:
            logger.debug("Built-in plugin package %s not found", package_name)
            return []

        pkg_path = Path(pkg.__file__).parent  # type: ignore[arg-type]
        classes: list[type[VibePlugin]] = []

        for py_file in sorted(pkg_path.rglob("plugin.py")):
            # e.g. vibe/core/plugins/builtin/lsp/plugin.py
            # → vibe.core.plugins.builtin.lsp.plugin
            rel = py_file.relative_to(pkg_path.parent.parent.parent.parent)
            mod_name = ".".join(rel.with_suffix("").parts)
            classes.extend(PluginManager._load_classes_from_module_name(mod_name))

        return classes

    @staticmethod
    def _scan_directory(directory: Path) -> list[type[VibePlugin]]:
        """Scan a filesystem directory for plugin.py files."""
        classes: list[type[VibePlugin]] = []
        for py_file in sorted(directory.rglob("plugin.py")):
            if py_file.name.startswith("_"):
                continue
            classes.extend(PluginManager._load_classes_from_path(py_file))
        return classes

    @staticmethod
    def _load_classes_from_module_name(
        module_name: str,
    ) -> list[type[VibePlugin]]:
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            logger.exception("Could not import module %s", module_name)
            return []
        return PluginManager._extract_plugin_classes(mod)

    @staticmethod
    def _load_classes_from_path(path: Path) -> list[type[VibePlugin]]:
        module_name = f"_vibe_plugin_{path.stem}_{abs(hash(str(path)))}"
        if module_name in sys.modules:
            mod = sys.modules[module_name]
        else:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return []
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            try:
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
            except Exception:
                logger.exception("Could not load plugin from %s", path)
                del sys.modules[module_name]
                return []
        return PluginManager._extract_plugin_classes(mod)

    @staticmethod
    def _extract_plugin_classes(module: object) -> list[type[VibePlugin]]:
        classes = []
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, VibePlugin)
                and obj is not VibePlugin
                and obj is not ToolEventPlugin
                and not inspect.isabstract(obj)
                and obj.__module__ == getattr(module, "__name__", "")
            ):
                classes.append(obj)
        return classes

    # ── Filtering ─────────────────────────────────────────────────────────────

    def _is_enabled(self, name: str) -> bool:
        enabled: list[str] | None = getattr(self._config, "enabled_plugins", None)
        disabled: list[str] = getattr(self._config, "disabled_plugins", [])

        # If whitelist is set (not None), name must match at least one pattern
        if enabled is not None and not any(_matches(name, pat) for pat in enabled):
            return False
        # If blacklist matches, disable
        if any(_matches(name, pat) for pat in disabled):
            return False
        return True


# ── Pattern matching (same semantics as Vibe's tool filtering) ─────────────


def _matches(name: str, pattern: str) -> bool:
    """Return True if *name* matches *pattern* (exact / glob / regex)."""
    if pattern.startswith("re:"):
        return bool(re.fullmatch(pattern[3:], name, re.IGNORECASE))
    # Heuristic: treat as regex if it contains regex metacharacters beyond */?
    if any(c in pattern for c in r"()[]{}+^$|\\"):
        try:
            return bool(re.fullmatch(pattern, name, re.IGNORECASE))
        except re.error:
            pass
    # Glob
    return fnmatch.fnmatch(name.lower(), pattern.lower())
