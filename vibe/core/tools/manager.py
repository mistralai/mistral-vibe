from __future__ import annotations

from collections.abc import Callable, Iterator
import hashlib
import importlib.util
import inspect
from logging import getLogger
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING, Any

from vibe.core.paths.config_paths import resolve_local_tools_dir
from vibe.core.paths.global_paths import DEFAULT_TOOL_DIR, GLOBAL_TOOLS_DIR
from vibe.core.tools.base import BaseTool, BaseToolConfig
from vibe.core.tools.mcp import (
    RemoteTool,
    create_mcp_http_proxy_tool_class,
    create_mcp_stdio_proxy_tool_class,
    list_tools_http,
    list_tools_stdio,
)
from vibe.core.utils import name_matches, run_sync

logger = getLogger("vibe")

if TYPE_CHECKING:
    from vibe.core.config import MCPHttp, MCPStdio, MCPStreamableHttp, VibeConfig


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

    def __init__(self, config_getter: Callable[[], VibeConfig]) -> None:
        self._config_getter = config_getter
        self._instances: dict[str, BaseTool] = {}
        self._search_paths: list[Path] = self._compute_search_paths(self._config)

        self._available: dict[str, type[BaseTool]] = {
            cls.get_name(): cls for cls in self._iter_tool_classes(self._search_paths)
        }
        self._integrate_mcp()

    @property
    def _config(self) -> VibeConfig:
        return self._config_getter()

    @staticmethod
    def _compute_search_paths(config: VibeConfig) -> list[Path]:
        paths: list[Path] = [DEFAULT_TOOL_DIR.path]

        paths.extend(config.tool_paths)

        if (tools_dir := resolve_local_tools_dir(Path.cwd())) is not None:
            paths.append(tools_dir)

        paths.append(GLOBAL_TOOLS_DIR.path)

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
    def available_tools(self) -> dict[str, type[BaseTool]]:
        if self._config.enabled_tools:
            return {
                name: cls
                for name, cls in self._available.items()
                if name_matches(name, self._config.enabled_tools)
            }
        if self._config.disabled_tools:
            return {
                name: cls
                for name, cls in self._available.items()
                if not name_matches(name, self._config.disabled_tools)
            }
        return dict(self._available)

    def _integrate_mcp(self) -> None:
        if not self._config.mcp_servers:
            return
        run_sync(self._integrate_mcp_async())

    async def _integrate_mcp_async(self) -> None:
        try:
            http_count = 0
            stdio_count = 0

            for srv in self._config.mcp_servers:
                match srv.transport:
                    case "http" | "streamable-http":
                        http_count += await self._register_http_server(srv)
                    case "stdio":
                        stdio_count += await self._register_stdio_server(srv)
                    case _:
                        logger.warning("Unsupported MCP transport: %r", srv.transport)

            logger.info(
                "MCP integration registered %d tools (http=%d, stdio=%d)",
                http_count + stdio_count,
                http_count,
                stdio_count,
            )
        except Exception as exc:
            logger.warning("Failed to integrate MCP tools: %s", exc)

    async def _register_http_server(self, srv: MCPHttp | MCPStreamableHttp) -> int:
        url = (srv.url or "").strip()
        if not url:
            logger.warning("MCP server '%s' missing url for http transport", srv.name)
            return 0

        headers = srv.http_headers()
        try:
            tools: list[RemoteTool] = await list_tools_http(
                url, headers=headers, startup_timeout_sec=srv.startup_timeout_sec
            )
        except Exception as exc:
            logger.warning("MCP HTTP discovery failed for %s: %s", url, exc)
            return 0

        added = 0
        for remote in tools:
            try:
                proxy_cls = create_mcp_http_proxy_tool_class(
                    url=url,
                    remote=remote,
                    alias=srv.name,
                    server_hint=srv.prompt,
                    headers=headers,
                    startup_timeout_sec=srv.startup_timeout_sec,
                    tool_timeout_sec=srv.tool_timeout_sec,
                )
                self._available[proxy_cls.get_name()] = proxy_cls
                added += 1
            except Exception as exc:
                logger.warning(
                    "Failed to register MCP HTTP tool '%s' from %s: %r",
                    getattr(remote, "name", "<unknown>"),
                    url,
                    exc,
                )
        return added

    async def _register_stdio_server(self, srv: MCPStdio) -> int:
        cmd = srv.argv()
        if not cmd:
            logger.warning("MCP stdio server '%s' has invalid/empty command", srv.name)
            return 0

        try:
            tools: list[RemoteTool] = await list_tools_stdio(
                cmd, env=srv.env or None, startup_timeout_sec=srv.startup_timeout_sec
            )
        except Exception as exc:
            logger.warning("MCP stdio discovery failed for %r: %s", cmd, exc)
            return 0

        added = 0
        for remote in tools:
            try:
                proxy_cls = create_mcp_stdio_proxy_tool_class(
                    command=cmd,
                    remote=remote,
                    alias=srv.name,
                    server_hint=srv.prompt,
                    env=srv.env or None,
                    startup_timeout_sec=srv.startup_timeout_sec,
                    tool_timeout_sec=srv.tool_timeout_sec,
                )
                self._available[proxy_cls.get_name()] = proxy_cls
                added += 1
            except Exception as exc:
                logger.warning(
                    "Failed to register MCP stdio tool '%s' from %r: %r",
                    getattr(remote, "name", "<unknown>"),
                    cmd,
                    exc,
                )
        return added

    def get_tool_config(self, tool_name: str) -> BaseToolConfig:
        tool_class = self._available.get(tool_name)

        if tool_class:
            config_class = tool_class._get_tool_config_class()
            default_config = config_class()
        else:
            config_class = BaseToolConfig
            default_config = BaseToolConfig()

        user_overrides = self._config.tools.get(tool_name)
        if user_overrides is None:
            merged_dict = default_config.model_dump()
        else:
            merged_dict = {**default_config.model_dump(), **user_overrides.model_dump()}

        return config_class.model_validate(merged_dict)

    def get(self, tool_name: str) -> BaseTool:
        """Get a tool instance, creating it lazily on first call.

        Raises:
            NoSuchToolError: If the requested tool is not available.
        """
        if tool_name in self._instances:
            return self._instances[tool_name]

        if tool_name not in self._available:
            raise NoSuchToolError(
                f"Unknown tool: {tool_name}. Available: {list(self._available.keys())}"
            )

        tool_class = self._available[tool_name]
        tool_config = self.get_tool_config(tool_name)
        self._instances[tool_name] = tool_class.from_config(tool_config)
        return self._instances[tool_name]

    def reset_all(self) -> None:
        self._instances.clear()

    def invalidate_tool(self, tool_name: str) -> None:
        self._instances.pop(tool_name, None)
