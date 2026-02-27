from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, cast

from vibe.core.logger import logger
from vibe.core.tools.base import BaseTool
from vibe.core.tools.mcp.tools import (
    create_mcp_http_proxy_tool_class,
    create_mcp_stdio_proxy_tool_class,
    list_tools_http,
    list_tools_stdio,
)
from vibe.core.utils import run_sync

if TYPE_CHECKING:
    from vibe.core.config import MCPHttp, MCPServer, MCPStdio, MCPStreamableHttp


class MCPRegistry:
    """Shared cache for MCP server tool discovery.

    Survives agent switches so that shift-tab does not re-discover
    servers whose config has not changed.  The cache is keyed by a
    stable fingerprint derived from each server's full config.
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, type[BaseTool]]] = {}

    @staticmethod
    def _server_key(srv: MCPServer) -> str:
        raw = srv.model_dump_json(exclude_none=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_tools(self, servers: list[MCPServer]) -> dict[str, type[BaseTool]]:
        """Return proxy tool classes for *servers*, using cache when possible."""
        result: dict[str, type[BaseTool]] = {}
        to_discover: list[tuple[str, MCPServer]] = []

        for srv in servers:
            key = self._server_key(srv)
            if key in self._cache:
                result.update(self._cache[key])
            else:
                to_discover.append((key, srv))

        if to_discover:
            discovered = run_sync(self._discover_all(to_discover))
            result.update(discovered)

        return result

    async def _discover_all(
        self, servers: list[tuple[str, MCPServer]]
    ) -> dict[str, type[BaseTool]]:
        results = await asyncio.gather(
            *(self._discover_server(srv) for _, srv in servers), return_exceptions=True
        )
        out: dict[str, type[BaseTool]] = {}
        for (key, srv), result in zip(servers, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "MCP discovery failed for server %r: %s", srv.name, result
                )
                continue
            if result is None:
                continue
            self._cache[key] = result
            out.update(result)
        return out

    async def _discover_server(
        self, srv: MCPServer
    ) -> dict[str, type[BaseTool]] | None:
        match srv.transport:
            case "http" | "streamable-http":
                return await self._discover_http(
                    cast("MCPHttp | MCPStreamableHttp", srv)
                )
            case "stdio":
                return await self._discover_stdio(cast("MCPStdio", srv))
            case _:
                logger.warning("Unsupported MCP transport: %r", srv.transport)
                return {}

    async def _discover_http(
        self, srv: MCPHttp | MCPStreamableHttp
    ) -> dict[str, type[BaseTool]] | None:
        url = (srv.url or "").strip()
        if not url:
            logger.warning("MCP server '%s' missing url for http transport", srv.name)
            return {}

        headers = srv.http_headers()
        try:
            remotes = await list_tools_http(
                url, headers=headers, startup_timeout_sec=srv.startup_timeout_sec
            )
        except Exception as exc:
            logger.warning("MCP HTTP discovery failed for %s: %s", url, exc)
            return None

        tools: dict[str, type[BaseTool]] = {}
        for remote in remotes:
            try:
                proxy_cls = create_mcp_http_proxy_tool_class(
                    url=url,
                    remote=remote,
                    alias=srv.name,
                    server_hint=srv.prompt,
                    headers=headers,
                    startup_timeout_sec=srv.startup_timeout_sec,
                    tool_timeout_sec=srv.tool_timeout_sec,
                    sampling_enabled=srv.sampling_enabled,
                )
                tools[proxy_cls.get_name()] = proxy_cls
            except Exception as exc:
                logger.warning(
                    "Failed to register MCP HTTP tool '%s' from %s: %r",
                    getattr(remote, "name", "<unknown>"),
                    url,
                    exc,
                )
        return tools

    async def _discover_stdio(self, srv: MCPStdio) -> dict[str, type[BaseTool]] | None:
        cmd = srv.argv()
        if not cmd:
            logger.warning("MCP stdio server '%s' has invalid/empty command", srv.name)
            return {}

        try:
            remotes = await list_tools_stdio(
                cmd, env=srv.env or None, startup_timeout_sec=srv.startup_timeout_sec
            )
        except Exception as exc:
            logger.warning("MCP stdio discovery failed for %r: %s", cmd, exc)
            return None

        tools: dict[str, type[BaseTool]] = {}
        for remote in remotes:
            try:
                proxy_cls = create_mcp_stdio_proxy_tool_class(
                    command=cmd,
                    remote=remote,
                    alias=srv.name,
                    server_hint=srv.prompt,
                    env=srv.env or None,
                    startup_timeout_sec=srv.startup_timeout_sec,
                    tool_timeout_sec=srv.tool_timeout_sec,
                    sampling_enabled=srv.sampling_enabled,
                )
                tools[proxy_cls.get_name()] = proxy_cls
            except Exception as exc:
                logger.warning(
                    "Failed to register MCP stdio tool '%s' from %r: %r",
                    getattr(remote, "name", "<unknown>"),
                    cmd,
                    exc,
                )
        return tools

    def clear(self) -> None:
        """Drop all cached entries, forcing re-discovery on next use."""
        self._cache.clear()
