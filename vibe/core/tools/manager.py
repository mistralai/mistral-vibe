from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
import hashlib
import importlib.util
import inspect
from pathlib import Path
import re
import sys
import threading
from typing import TYPE_CHECKING, Any, TypeGuard

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.logger import logger
from vibe.core.paths import DEFAULT_TOOL_DIR
from vibe.core.tools.base import BaseTool, BaseToolConfig, ToolPermission
from vibe.core.tools.remote import MCPTool
from vibe.core.types import AvailableFunction
from vibe.core.utils import name_matches, run_sync
from vibe.core.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.tools.connectors.connector_registry import ConnectorRegistry
    from vibe.core.tools.mcp.registry import MCPRegistry


def _try_canonical_module_name(path: Path) -> str | None:
    """Extract canonical module name for vibe package files.

    Prevents Pydantic class identity mismatches when the same module
    is imported via dynamic discovery and regular imports.
    """
    try:
        parts = path.resolve().parts
    except (OSError, ValueError):
        return None

    try:
        vibe_idx = parts.index("vibe")
    except ValueError:
        return None

    if vibe_idx + 1 >= len(parts):
        return None

    module_parts = [p.removesuffix(".py") for p in parts[vibe_idx:]]
    return ".".join(module_parts)


def _compute_module_name(path: Path) -> str:
    """Return canonical module name for vibe files, hash-based synthetic name otherwise."""
    if canonical := _try_canonical_module_name(path):
        return canonical

    resolved = path.resolve()
    path_hash = hashlib.md5(str(resolved).encode()).hexdigest()[:8]
    stem = re.sub(r"[^0-9A-Za-z_]", "_", path.stem) or "mod"
    return f"vibe_tools_discovered_{stem}_{path_hash}"


class NoSuchToolError(Exception):
    """Exception raised when a tool is not found."""


class ToolManager:
    """Manages tool discovery and instantiation for an Agent.

    Discovers available tools from the provided search paths. Each Agent
    should have its own ToolManager instance.
    """

    def __init__(
        self,
        config_getter: Callable[[], VibeConfigSchema],
        mcp_registry: MCPRegistry | None = None,
        connector_registry: ConnectorRegistry | None = None,
        *,
        defer_mcp: bool = False,
        permission_getter: Callable[[str], ToolPermission | None] | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._permission_getter = permission_getter
        self._mcp_registry = mcp_registry
        self._connector_registry = connector_registry
        self._instances: dict[str, BaseTool] = {}
        self._search_paths: list[Path] = self._compute_search_paths(self._config)
        self._lock = threading.Lock()
        self._mcp_integrated = False

        self._tool_variants_by_name: dict[str, list[type[BaseTool]]] = {}
        # Historical one-class-per-name registry. When multiple classes publish the
        # same name, this is the fallback used if no variant is active.
        self._all_tools: dict[str, type[BaseTool]] = {}
        for tool_class in self._iter_tool_classes(self._search_paths):
            self._register_discovered_tool_variant(tool_class)
        self._tool_descriptions: dict[str, str] = dict(
            self._iter_tool_descriptions(self._search_paths)
        )
        if not defer_mcp:
            self.integrate_all()

    def set_mcp_registry(self, mcp_registry: MCPRegistry | None) -> None:
        self._mcp_registry = mcp_registry

    def set_connector_registry(
        self, connector_registry: ConnectorRegistry | None
    ) -> None:
        self._connector_registry = connector_registry

    def _get_mcp_registry(self) -> MCPRegistry:
        if self._mcp_registry is None:
            from vibe.core.tools.mcp.registry import MCPRegistry

            self._mcp_registry = MCPRegistry()
        return self._mcp_registry

    @property
    def _config(self) -> VibeConfigSchema:
        return self._config_getter()

    @staticmethod
    def _compute_search_paths(config: VibeConfigSchema) -> list[Path]:
        paths: list[Path] = [DEFAULT_TOOL_DIR.path]

        paths.extend(config.tool_paths)

        mgr = get_harness_files_manager()
        paths.extend(mgr.project_tools_dirs)
        paths.extend(mgr.user_tools_dirs)

        unique: list[Path] = []
        seen: set[Path] = set()
        for p in paths:
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                unique.append(rp)
        return unique

    @staticmethod
    def _iter_tool_classes(search_paths: list[Path]) -> Iterator[type[BaseTool]]:
        """Iterate over all search_paths to find tool classes.

        A search path is either a directory of tool files (``<dir>/*.py``, e.g.
        ``.vibe/tools/``) or a single ``.py`` file. Tool files sit directly in
        the directory — the same flat layout as the builtins and as the sibling
        ``prompts/*.md`` descriptions (see ``_iter_tool_descriptions``).
        """
        for base in search_paths:
            if not base.is_dir() and base.name.endswith(".py"):
                if tools := ToolManager._load_tools_from_file(base):
                    for tool in tools:
                        yield tool

            for path in base.glob("*.py"):
                if tools := ToolManager._load_tools_from_file(path):
                    for tool in tools:
                        yield tool

    @staticmethod
    def _load_tools_from_file(file_path: Path) -> list[type[BaseTool]] | None:
        if not file_path.is_file():
            return
        name = file_path.name
        if name.startswith("_"):
            return

        module_name = _compute_module_name(file_path)

        if module_name in sys.modules:
            module = sys.modules[module_name]
        else:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                return

        tools = []
        for tool_obj in vars(module).values():
            if not inspect.isclass(tool_obj):
                continue
            if not issubclass(tool_obj, BaseTool) or tool_obj is BaseTool:
                continue
            if inspect.isabstract(tool_obj):
                continue
            tools.append(tool_obj)
        return tools

    @staticmethod
    def _iter_tool_descriptions(search_paths: list[Path]) -> Iterator[tuple[str, str]]:
        """Yield ``(tool_name, description)`` from ``prompts/<name>.md`` files in
        the tool search paths.

        Every tool directory pairs implementations with their descriptions the
        same way the builtins do — ``<tools-dir>/*.py`` alongside
        ``<tools-dir>/prompts/*.md`` (the very layout ``get_tool_prompt`` reads).
        So ``.vibe/tools/prompts/weather.md`` describes a custom ``weather`` tool
        and ``.vibe/tools/prompts/bash.md`` re-describes the builtin ``bash``.

        Keyed by file stem (the tool name); later search paths win, matching
        ``.py`` override precedence. A ``.py`` search-path entry is matched
        against the ``prompts/`` dir beside it.
        """
        for base in search_paths:
            if base.is_dir():
                prompts_dir = base / "prompts"
            elif base.name.endswith(".py"):
                prompts_dir = base.parent / "prompts"
            else:
                continue
            if not prompts_dir.is_dir():
                continue
            for md_path in sorted(prompts_dir.glob("*.md")):
                try:
                    text = read_safe(md_path).text
                except OSError:
                    continue
                # Yield the raw text (matching get_full_description), but skip
                # blank files so they fall back instead of blanking a tool.
                if text.strip():
                    yield md_path.stem, text

    @staticmethod
    def discover_tool_defaults(
        search_paths: list[Path] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if search_paths is None:
            search_paths = [DEFAULT_TOOL_DIR.path]

        defaults: dict[str, dict[str, Any]] = {}
        for cls in ToolManager._iter_tool_classes(search_paths):
            try:
                tool_name = cls.get_name()
                config_class = cls._get_tool_config_class()
                defaults[tool_name] = config_class().model_dump(exclude_none=True)
            except Exception as e:
                logger.warning(
                    "Failed to get defaults for tool %s: %s", cls.__name__, e
                )
                continue
        return defaults

    def _register_discovered_tool_variant(self, tool_class: type[BaseTool]) -> None:
        name = tool_class.get_name()
        self._tool_variants_by_name.setdefault(name, []).append(tool_class)
        self._all_tools[name] = tool_class

    @property
    def registered_tools(self) -> dict[str, type[BaseTool]]:
        with self._lock:
            selected_tools: dict[str, type[BaseTool]] = {}
            for name, fallback_tool_class in self._all_tools.items():
                selected_tools[name] = self._select_registered_variant(
                    name, fallback_tool_class
                )
            return selected_tools

    @property
    def available_tools(self) -> dict[str, type[BaseTool]]:
        with self._lock:
            runtime_available: dict[str, type[BaseTool]] = {}
            for name, fallback_tool_class in self._all_tools.items():
                selected_tool_class = self._select_available_variant(
                    name, fallback_tool_class
                )
                if selected_tool_class is None:
                    continue
                runtime_available[name] = selected_tool_class

        # Per-source filtering first (MCP server/connector disabled flags).
        result = self._apply_per_source_filtering(runtime_available)

        # Global allowlist narrows the candidate set; denylist is always final.
        if self._config.enabled_tools:
            result = {
                name: cls
                for name, cls in result.items()
                if name_matches(name, self._config.enabled_tools)
            }
        if self._config.disabled_tools:
            return {
                name: cls
                for name, cls in result.items()
                if not name_matches(name, self._config.disabled_tools)
            }
        return result

    def _is_tool_available(self, cls: type[BaseTool]) -> bool:
        # Backwards-compatibility check to avoid breaking
        # existing custom tools that call is_available without parameters
        if inspect.signature(cls.is_available).parameters:
            return cls.is_available(self._config)
        return cls.is_available()

    def _tool_variants_for_name(
        self, name: str, fallback: type[BaseTool]
    ) -> list[type[BaseTool]]:
        return self._tool_variants_by_name.get(name) or [fallback]

    def _select_available_variant(
        self, name: str, fallback: type[BaseTool]
    ) -> type[BaseTool] | None:
        selected_tool_class: type[BaseTool] | None = None
        selected_rank: tuple[int, int] | None = None

        for discovery_index, tool_class in enumerate(
            self._tool_variants_for_name(name, fallback)
        ):
            if not self._is_tool_available(tool_class):
                continue

            rank = (self._tool_selection_priority(tool_class), discovery_index)
            if selected_rank is not None and rank <= selected_rank:
                continue

            selected_tool_class = tool_class
            selected_rank = rank

        return selected_tool_class

    @staticmethod
    def _tool_selection_priority(tool_class: type[BaseTool]) -> int:
        return tool_class.selection_priority

    def _select_registered_variant(
        self, name: str, fallback: type[BaseTool]
    ) -> type[BaseTool]:
        selected_tool_class = self._select_available_variant(name, fallback)
        if selected_tool_class is not None:
            return selected_tool_class
        return fallback

    def _tool_class_for_config(self, tool_name: str) -> type[BaseTool] | None:
        fallback_tool_class = self._all_tools.get(tool_name)
        if fallback_tool_class is None:
            return None
        return self._select_registered_variant(tool_name, fallback_tool_class)

    def _apply_per_source_filtering(
        self, tools: dict[str, type[BaseTool]]
    ) -> dict[str, type[BaseTool]]:
        """Filter out MCP/connector tools disabled at the server or connector level."""
        disabled_sources, per_source_disabled = self._build_source_disable_index()
        if not disabled_sources and not per_source_disabled:
            return tools

        return {
            name: cls
            for name, cls in tools.items()
            if not self._is_source_disabled(cls, disabled_sources, per_source_disabled)
        }

    def _build_source_disable_index(
        self,
    ) -> tuple[set[tuple[str, bool]], dict[tuple[str, bool], set[str]]]:
        """Return (fully_disabled, per_tool_disabled) keyed by (source_name, is_connector)."""
        disabled_sources: set[tuple[str, bool]] = set()
        per_source_disabled: dict[tuple[str, bool], set[str]] = {}

        for srv in self._config.mcp_servers:
            key = (srv.name, False)
            if srv.disabled:
                disabled_sources.add(key)
            elif srv.disabled_tools:
                per_source_disabled[key] = set(srv.disabled_tools)

        for cfg in self._config.connectors:
            if cfg.disabled_tools and not cfg.disabled:
                per_source_disabled[(cfg.name, True)] = set(cfg.disabled_tools)

        if self._connector_registry is not None:
            by_name = self._config.connectors_by_name()
            for name in self._connector_registry.get_connector_names():
                cfg = by_name.get(name)
                if cfg is None or cfg.disabled:
                    disabled_sources.add((name, True))

        return disabled_sources, per_source_disabled

    @staticmethod
    def _is_source_disabled(
        tool_cls: type[BaseTool],
        disabled_sources: set[tuple[str, bool]],
        per_source_disabled: dict[tuple[str, bool], set[str]],
    ) -> bool:
        if not ToolManager._is_remote_tool_class(tool_cls):
            return False
        server_name = tool_cls.get_server_name()
        if server_name is None:
            return False
        key = (server_name, tool_cls.is_connector())
        if key in disabled_sources:
            return True
        return tool_cls.get_remote_name() in per_source_disabled.get(key, set())

    @staticmethod
    def _is_remote_tool_class(tool_cls: type[BaseTool]) -> TypeGuard[type[MCPTool]]:
        return issubclass(tool_cls, MCPTool)

    def integrate_mcp(self, *, raise_on_failure: bool = False) -> None:
        """Discover and register MCP tools (sync wrapper).

        Idempotent: subsequent calls after a successful integration are
        no-ops to avoid redundant MCP discovery.
        """
        run_sync(self._integrate_mcp_async(raise_on_failure=raise_on_failure))

    async def _integrate_mcp_async(self, *, raise_on_failure: bool = False) -> None:
        """Async MCP discovery — canonical implementation."""
        if self._mcp_integrated:
            return
        if not self._config.mcp_servers:
            if self._mcp_registry is not None:
                self._mcp_registry.sync_active_servers([])
            return

        try:
            mcp_tools = await self._get_mcp_registry().get_tools_async(
                self._config.mcp_servers
            )
        except Exception as exc:
            logger.warning("MCP integration failed: %s", exc)
            if raise_on_failure:
                raise
            return

        with self._lock:
            self._all_tools = {**self._all_tools, **mcp_tools}
        self._mcp_integrated = True
        logger.info(
            "MCP integration registered %d tools (via registry)", len(mcp_tools)
        )

    def _purge_connector_state(self) -> None:
        """Remove stale connector tool classes and cached instances."""
        stale_keys = [
            name
            for name, cls in self._all_tools.items()
            if self._is_remote_tool_class(cls) and cls.is_connector()
        ]
        for key in stale_keys:
            self._all_tools.pop(key, None)
            self._instances.pop(key, None)

    def _purge_mcp_state(self) -> None:
        """Remove stale MCP tool classes and cached instances."""
        stale_keys = [
            name
            for name, cls in self._all_tools.items()
            if self._is_remote_tool_class(cls) and not cls.is_connector()
        ]
        for key in stale_keys:
            self._all_tools.pop(key, None)
            self._instances.pop(key, None)

    def integrate_connectors(self, *, force_refresh: bool = False) -> None:
        """Discover and register connector tools (sync wrapper)."""
        run_sync(self.integrate_connectors_async(force_refresh=force_refresh))

    async def integrate_connectors_async(self, *, force_refresh: bool = False) -> None:
        """Discover and register connector tools — canonical implementation.

        Thread-safe: can be called from the deferred-init background thread.
        """
        if self._connector_registry is None:
            return

        try:
            connector_tools = await self._connector_registry.get_tools_async(
                force_refresh=force_refresh
            )
        except Exception as exc:
            logger.warning(f"Connector integration failed: {exc}")
            with self._lock:
                self._purge_connector_state()
            return

        with self._lock:
            self._purge_connector_state()
            self._all_tools.update(connector_tools)
        logger.info(f"Connector integration registered {len(connector_tools)} tools")

    async def refresh_remote_tools_async(self) -> None:
        """Force MCP and connector re-discovery for the current config."""
        with self._lock:
            if self._mcp_registry is not None:
                self._mcp_registry.clear()
            self._purge_mcp_state()
            self._mcp_integrated = False
            self._purge_connector_state()
            if self._connector_registry is not None:
                self._connector_registry.clear()

        await self._integrate_all_async(force_refresh=True)

    def refresh_remote_tools(self) -> None:
        """Sync wrapper for :meth:`refresh_remote_tools_async`."""
        run_sync(self.refresh_remote_tools_async())

    def integrate_all(
        self, *, raise_on_mcp_failure: bool = False, force_refresh: bool = False
    ) -> None:
        """Discover MCP and connector tools in parallel.

        Runs both async discovery paths concurrently via ``asyncio.gather``
        inside a single ``run_sync`` call.
        """
        run_sync(
            self._integrate_all_async(
                raise_on_mcp_failure=raise_on_mcp_failure, force_refresh=force_refresh
            )
        )

    async def _integrate_all_async(
        self, *, raise_on_mcp_failure: bool = False, force_refresh: bool = False
    ) -> None:
        """Run MCP and connector discovery concurrently.

        Uses ``return_exceptions=True`` so that a failing MCP server does
        not cancel in-flight connector discovery (or vice-versa).
        """
        mcp_result, connector_result = await asyncio.gather(
            self._integrate_mcp_async(raise_on_failure=raise_on_mcp_failure),
            self.integrate_connectors_async(force_refresh=force_refresh),
            return_exceptions=True,
        )

        # Re-raise MCP errors when the caller asked for them.
        if isinstance(mcp_result, BaseException):
            if raise_on_mcp_failure:
                raise mcp_result
            logger.warning(f"MCP integration failed: {mcp_result}")

        if isinstance(connector_result, BaseException):
            logger.warning(f"Connector integration failed: {connector_result}")

    def available_tool_specs(self) -> list[AvailableFunction]:
        """Model-facing definitions for every available tool: name, resolved
        description, and parameters.

        The description comes from a ``<tools-dir>/prompts/<name>.md`` file when
        present (builtin defaults, custom-tool descriptions, and user/project
        overrides all live there), falling back to the tool's own description
        (e.g. MCP/connector tools set it inline). Both the LLM tool formatter and
        the session logger consume this so a tool always looks the same to the
        model and in the logs.
        """
        return [
            AvailableFunction(
                name=name,
                description=self._tool_descriptions.get(name)
                or cls.get_full_description(),
                parameters=cls.get_parameters(),
            )
            for name, cls in self.available_tools.items()
        ]

    def get_tool_config(self, tool_name: str) -> BaseToolConfig:
        with self._lock:
            tool_class = self._tool_class_for_config(tool_name)

        if tool_class:
            config_class = tool_class._get_tool_config_class()
            default_config = config_class()
        else:
            config_class = BaseToolConfig
            default_config = BaseToolConfig()

        user_overrides = self._config.tools.get(tool_name)
        permission_override = (
            self._permission_getter(tool_name) if self._permission_getter else None
        )
        if user_overrides is None and permission_override is None:
            return config_class()

        merged_dict = {**default_config.model_dump(), **(user_overrides or {})}
        if permission_override is not None:
            merged_dict["permission"] = permission_override.value
        return config_class.model_validate(merged_dict)

    def get(self, tool_name: str) -> BaseTool:
        """Get a tool instance, creating it lazily on first call.

        Raises:
            NoSuchToolError: If the requested tool is not available.
        """
        available = self.available_tools
        if tool_name not in available:
            raise NoSuchToolError(
                f"Unknown or disabled tool: {tool_name}. "
                f"Available: {list(available.keys())}"
            )
        tool_class = available[tool_name]
        cached = self._instances.get(tool_name)
        if cached is not None and type(cached) is tool_class:
            return cached
        instance = tool_class.from_config(lambda: self.get_tool_config(tool_name))
        self._instances[tool_name] = instance
        return instance

    def pop_mcp_errors(self) -> dict[str, str]:
        """Return and clear pending MCP discovery errors (server name -> message)."""
        if self._mcp_registry is None:
            return {}
        return self._mcp_registry.pop_failed()

    def reset_all(self) -> None:
        self._instances.clear()
