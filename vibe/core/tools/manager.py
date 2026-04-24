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
from typing import TYPE_CHECKING, Any

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.logger import logger
from vibe.core.paths import DEFAULT_TOOL_DIR
from vibe.core.tools.base import BaseTool, BaseToolConfig
from vibe.core.tools.connectors import ConnectorRegistry
from vibe.core.tools.mcp import MCPRegistry
from vibe.core.tools.mcp.tools import MCPTool
from vibe.core.utils import name_matches, run_sync

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig


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
        config_getter: Callable[[], VibeConfig],
        mcp_registry: MCPRegistry | None = None,
        connector_registry: ConnectorRegistry | None = None,
        *,
        defer_mcp: bool = False,
    ) -> None:
        self._config_getter = config_getter
        self._mcp_registry = mcp_registry or MCPRegistry()
        self._connector_registry = connector_registry
        self._instances: dict[str, BaseTool] = {}
        self._search_paths: list[Path] = self._compute_search_paths(self._config)
        self._lock = threading.Lock()
        self._mcp_integrated = False

        self._available: dict[str, type[BaseTool]] = {
            cls.get_name(): cls for cls in self._iter_tool_classes(self._search_paths)
        }
        if not defer_mcp:
            self.integrate_all()

    @property
    def _config(self) -> VibeConfig:
        return self._config_getter()

    @staticmethod
    def _compute_search_paths(config: VibeConfig) -> list[Path]:
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

        Note: if a search path is not a directory, it is treated as a single tool file.
        """
        for base in search_paths:
            if not base.is_dir() and base.name.endswith(".py"):
                if tools := ToolManager._load_tools_from_file(base):
                    for tool in tools:
                        yield tool

            for path in base.rglob("*.py"):
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

    @property
    def registered_tools(self) -> dict[str, type[BaseTool]]:
        with self._lock:
            return dict(self._available)

    @property
    def available_tools(self) -> dict[str, type[BaseTool]]:
        with self._lock:
            runtime_available = {
                name: cls for name, cls in self._available.items() if cls.is_available()
            }

        if self._config.enabled_tools:
            return {
                name: cls
                for name, cls in runtime_available.items()
                if name_matches(name, self._config.enabled_tools)
            }
        if self._config.disabled_tools:
            return {
                name: cls
                for name, cls in runtime_available.items()
                if not name_matches(name, self._config.disabled_tools)
            }
        return runtime_available

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
            return

        try:
            mcp_tools = await self._mcp_registry.get_tools_async(
                self._config.mcp_servers
            )
        except Exception as exc:
            logger.warning("MCP integration failed: %s", exc)
            if raise_on_failure:
                raise
            return

        with self._lock:
            self._available = {**self._available, **mcp_tools}
        self._mcp_integrated = True
        logger.info(
            "MCP integration registered %d tools (via registry)", len(mcp_tools)
        )

    def _purge_connector_state(self) -> None:
        """Remove stale connector tool classes and cached instances."""
        stale_keys = [
            name
            for name, cls in self._available.items()
            if issubclass(cls, MCPTool) and cls.is_connector()
        ]
        for key in stale_keys:
            self._available.pop(key, None)
            self._instances.pop(key, None)

    def _purge_mcp_state(self) -> None:
        """Remove stale MCP tool classes and cached instances."""
        stale_keys = [
            name
            for name, cls in self._available.items()
            if issubclass(cls, MCPTool) and not cls.is_connector()
        ]
        for key in stale_keys:
            self._available.pop(key, None)
            self._instances.pop(key, None)

    def integrate_connectors(self) -> None:
        """Discover and register connector tools (sync wrapper)."""
        run_sync(self.integrate_connectors_async())

    async def integrate_connectors_async(self) -> None:
        """Discover and register connector tools — canonical implementation.

        Thread-safe: can be called from the deferred-init background thread.
        """
        if self._connector_registry is None:
            return

        try:
            connector_tools = await self._connector_registry.get_tools_async()
        except Exception as exc:
            logger.warning(f"Connector integration failed: {exc}")
            with self._lock:
                self._purge_connector_state()
            return

        with self._lock:
            self._purge_connector_state()
            self._available.update(connector_tools)
        logger.info(f"Connector integration registered {len(connector_tools)} tools")

    async def refresh_remote_tools_async(self) -> None:
        """Force MCP and connector re-discovery for the current config."""
        with self._lock:
            self._mcp_registry.clear()
            self._purge_mcp_state()
            self._mcp_integrated = False
            self._purge_connector_state()
            if self._connector_registry is not None:
                self._connector_registry.clear()

        await self._integrate_all_async()

    def refresh_remote_tools(self) -> None:
        """Sync wrapper for :meth:`refresh_remote_tools_async`."""
        run_sync(self.refresh_remote_tools_async())

    def integrate_all(self, *, raise_on_mcp_failure: bool = False) -> None:
        """Discover MCP and connector tools in parallel.

        Runs both async discovery paths concurrently via ``asyncio.gather``
        inside a single ``run_sync`` call.
        """
        run_sync(self._integrate_all_async(raise_on_mcp_failure=raise_on_mcp_failure))

    async def _integrate_all_async(self, *, raise_on_mcp_failure: bool = False) -> None:
        """Run MCP and connector discovery concurrently.

        Uses ``return_exceptions=True`` so that a failing MCP server does
        not cancel in-flight connector discovery (or vice-versa).
        """
        mcp_result, connector_result = await asyncio.gather(
            self._integrate_mcp_async(raise_on_failure=raise_on_mcp_failure),
            self.integrate_connectors_async(),
            return_exceptions=True,
        )

        # Re-raise MCP errors when the caller asked for them.
        if isinstance(mcp_result, BaseException):
            if raise_on_mcp_failure:
                raise mcp_result
            logger.warning(f"MCP integration failed: {mcp_result}")

        if isinstance(connector_result, BaseException):
            logger.warning(f"Connector integration failed: {connector_result}")

    def get_tool_config(self, tool_name: str) -> BaseToolConfig:
        with self._lock:
            tool_class = self._available.get(tool_name)

        if tool_class:
            config_class = tool_class._get_tool_config_class()
            default_config = config_class()
        else:
            config_class = BaseToolConfig
            default_config = BaseToolConfig()

        user_overrides = self._config.tools.get(tool_name)
        if user_overrides is None:
            return config_class()

        merged_dict = {**default_config.model_dump(), **user_overrides}
        return config_class.model_validate(merged_dict)

    def get(self, tool_name: str) -> BaseTool:
        """Get a tool instance, creating it lazily on first call.

        Raises:
            NoSuchToolError: If the requested tool is not available.
        """
        if tool_name in self._instances:
            return self._instances[tool_name]

        with self._lock:
            if tool_name not in self._available:
                raise NoSuchToolError(
                    f"Unknown tool: {tool_name}. Available: {list(self._available.keys())}"
                )
            tool_class = self._available[tool_name]
        self._instances[tool_name] = tool_class.from_config(
            lambda: self.get_tool_config(tool_name)
        )
        return self._instances[tool_name]

    def reset_all(self) -> None:
        self._instances.clear()
