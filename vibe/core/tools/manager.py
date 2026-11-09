from __future__ import annotations

import asyncio
from collections.abc import Callable
import importlib
import inspect
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any, TypeGuard

from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.paths import DEFAULT_TOOL_DIR
from vibe.core.tools.base import BaseTool, BaseToolConfig, ToolPermission
from vibe.core.tools.remote import MCPTool
from vibe.core.utils import is_windows, name_matches, run_sync
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.tools.connectors.connector_registry import ConnectorRegistry
    from vibe.core.tools.mcp.registry import MCPRegistry


_BUILTIN_PACKAGE = "vibe.core.tools.builtins"

# Every derivation of every session in the process lists the builtin tools
# again, and the builtin files never change while Vibe runs.
_builtin_tools: tuple[type[BaseTool], ...] | None = None
_builtin_tools_lock = threading.Lock()


def _discover_builtin_tools() -> tuple[type[BaseTool], ...]:
    global _builtin_tools
    with _builtin_tools_lock:
        if _builtin_tools is not None:
            return _builtin_tools
        tools: list[type[BaseTool]] = []
        for path in sorted(DEFAULT_TOOL_DIR.path.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                tools.extend(_tool_classes_of(f"{_BUILTIN_PACKAGE}.{path.stem}"))
            except Exception as exc:
                logger.warning("Failed to load tools from %s: %s", path, exc)
        _builtin_tools = tuple(tools)
        return _builtin_tools


def builtin_tool_classes() -> tuple[type[BaseTool], ...]:
    """Every builtin tool class, including each variant of a name."""
    return _discover_builtin_tools()


def discover_tool_defaults() -> dict[str, dict[str, Any]]:
    defaults: dict[str, dict[str, Any]] = {}
    for cls in _discover_builtin_tools():
        try:
            tool_name = cls.get_name()
            config_class = cls._get_tool_config_class()
            defaults[tool_name] = config_class().model_dump(exclude_none=True)
        except Exception as e:
            logger.warning("Failed to get defaults for tool %s: %s", cls.__name__, e)
            continue
    return defaults


def _tool_classes_of(module_name: str) -> list[type[BaseTool]]:
    module = importlib.import_module(module_name)
    # Builtin tool modules import shared base classes (e.g. BashOutput) from
    # sibling files; drop those re-exports so they are not registered twice
    # under the importing module.
    return [
        tool_obj
        for tool_obj in vars(module).values()
        if inspect.isclass(tool_obj)
        and tool_obj.__module__ == module.__name__
        and issubclass(tool_obj, BaseTool)
        and tool_obj is not BaseTool
        and not inspect.isabstract(tool_obj)
    ]


class NoSuchToolError(Exception):
    """Exception raised when a tool is not found."""


class ToolManager:
    """Manages tool discovery and instantiation for an Agent.

    Lists the builtin tools, then MCP and connector tools. Each Agent should
    have its own ToolManager instance.
    """

    def __init__(
        self,
        config_getter: Callable[[], VibeConfigSchema],
        mcp_registry: MCPRegistry | None = None,
        connector_registry: ConnectorRegistry | None = None,
        *,
        defer_mcp: bool = False,
        permission_getter: Callable[[str], ToolPermission | None] | None = None,
        local_managed_shell_runtime_enabled: bool = True,
        cwd: Path | None = None,
        harness_files: HarnessFilesManager | None = None,
        scratchpad_dir: Path | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._cwd = (cwd or Path.cwd()).resolve()
        self._harness_files = harness_files or get_harness_files_manager()
        self._scratchpad_dir = scratchpad_dir
        self._permission_getter = permission_getter
        self._local_managed_shell_runtime_enabled = local_managed_shell_runtime_enabled
        self._mcp_registry = mcp_registry
        self._connector_registry = connector_registry
        self._instances: dict[str, BaseTool] = {}
        self._lock = threading.Lock()
        self._mcp_integrated = False

        self._tool_variants_by_name: dict[str, list[type[BaseTool]]] = {}
        # Historical one-class-per-name registry. When multiple classes publish the
        # same name, this is the fallback used if no variant is active.
        self._all_tools: dict[str, type[BaseTool]] = {}
        for tool_class in _discover_builtin_tools():
            self._register_discovered_tool_variant(tool_class)
        if not defer_mcp:
            self.integrate_all()

    def _get_mcp_registry(self) -> MCPRegistry:
        if self._mcp_registry is None:
            from vibe.core.tools.mcp.registry import MCPRegistry

            self._mcp_registry = MCPRegistry()
        return self._mcp_registry

    @property
    def _config(self) -> VibeConfigSchema:
        return self._config_getter()

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
        if (
            cls.local_managed_shell_only
            and not self._local_managed_shell_runtime_enabled
        ):
            return False
        if not self._is_enabled_for_shell_rollout(cls):
            return False
        return self._is_tool_runtime_available(cls)

    def _is_tool_runtime_available(self, cls: type[BaseTool]) -> bool:
        return cls.is_available(self._config)

    def _is_enabled_for_shell_rollout(self, cls: type[BaseTool]) -> bool:
        match cls.shell_rollout:
            case None:
                return True
            case "managed":
                return self._config.managed_shell_tools_enabled
            case "legacy":
                if not self._config.managed_shell_tools_enabled:
                    return True
                if not is_windows():
                    return True
                return False
            case _:
                return True

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
        # The fallback is the variant registered last.
        return self._select_available_variant(name, fallback) or fallback

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

    async def _integrate_mcp_async(self, *, raise_on_failure: bool = False) -> None:
        """Async MCP discovery — canonical implementation."""
        if self._mcp_integrated:
            return
        if not self._config.enable_mcp or not self._config.mcp_servers:
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

    def suspend_mcp(self, name: str, tool_name: str | None = None) -> None:
        """Withdraw one source or remote tool before reducing its authority."""
        with self._lock:
            stale_keys = [
                key
                for key, tool_class in self._all_tools.items()
                if self._is_remote_tool_class(tool_class)
                and not tool_class.is_connector()
                and tool_class.get_server_name() == name
                and (tool_name is None or tool_class.get_remote_name() == tool_name)
            ]
            for key in stale_keys:
                self._all_tools.pop(key, None)
                self._instances.pop(key, None)

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

    def get_tool_config(
        self, tool_name: str, *, default_permission: ToolPermission = ToolPermission.ASK
    ) -> BaseToolConfig:
        with self._lock:
            tool_class = self._tool_class_for_config(tool_name)

        if tool_class:
            config_class = tool_class._get_tool_config_class()
            default_config = config_class()
        else:
            config_class = BaseToolConfig
            default_config = BaseToolConfig(permission=default_permission)

        user_overrides = self._config.tools.get(tool_name)
        permission_override = (
            self._permission_getter(tool_name) if self._permission_getter else None
        )
        if user_overrides is None and permission_override is None:
            return default_config

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
        instance = tool_class.from_config(
            lambda: self.get_tool_config(tool_name),
            cwd=self._cwd,
            harness_files=self._harness_files,
            scratchpad_dir=self._scratchpad_dir,
        )
        self._instances[tool_name] = instance
        return instance
