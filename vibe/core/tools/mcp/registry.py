from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from enum import StrEnum, auto
import hashlib
from typing import cast

from mcp.client.auth import OAuthClientProvider, OAuthFlowError
from vibe.core.auth.mcp_oauth import (
    Fingerprint,
    KeyringTokenStorage,
    MCPOAuthError,
    MCPOAuthInvalidGrant,
    MCPOAuthLoginFailed,
    MCPOAuthTransientRefreshError,
    build_oauth_provider,
    perform_oauth_login,
    unwrap_oauth_refresh_error,
)
from vibe.core.config import MCPHttp, MCPOAuth, MCPServer, MCPStdio, MCPStreamableHttp
from vibe.core.logger import logger
from vibe.core.tools.base import BaseTool
from vibe.core.tools.mcp.tools import (
    MCPHttpOAuthRuntime,
    create_mcp_http_proxy_tool_class,
    create_mcp_stdio_proxy_tool_class,
    list_tools_http,
    list_tools_stdio,
)
from vibe.core.tools.remote import RemoteTool
from vibe.core.utils import run_sync


class AuthStatus(StrEnum):
    OK = auto()
    NEEDS_AUTH = auto()
    STATIC = auto()
    STDIO = auto()


class MCPRegistry:
    """Shared cache for MCP server tool discovery.

    Survives agent switches so that shift-tab does not re-discover
    servers whose config has not changed.  The cache is keyed by a
    stable fingerprint derived from each server's full config.
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, type[BaseTool]]] = {}
        self._cache_keys_by_alias: dict[str, set[str]] = {}
        self._servers_by_alias: dict[str, MCPServer] = {}
        self._needs_auth: set[str] = set()
        self._oauth_locks: dict[str, asyncio.Lock] = {}
        self._failed: dict[str, str] = {}

    @staticmethod
    def _server_key(srv: MCPServer) -> str:
        raw = srv.model_dump_json(exclude_none=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_tools(self, servers: list[MCPServer]) -> dict[str, type[BaseTool]]:
        """Return proxy tool classes for *servers*, using cache when possible."""
        return run_sync(self.get_tools_async(servers))

    async def get_tools_async(
        self, servers: list[MCPServer]
    ) -> dict[str, type[BaseTool]]:
        """Async variant of :meth:`get_tools`."""
        result: dict[str, type[BaseTool]] = {}
        to_discover: list[tuple[str, MCPServer]] = []

        self.sync_active_servers(servers)
        for srv in servers:
            key = self._server_key(srv)
            if key in self._cache:
                result.update(self._cache[key])
            else:
                to_discover.append((key, srv))

        if to_discover:
            discovered = await self._discover_all(to_discover)
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
                self._failed[srv.name] = str(result)
                continue
            if result is None:
                continue
            self._store_cache_entry(key, srv.name, result)
            out.update(result)
        return out

    def pop_failed(self) -> dict[str, str]:
        """Return and clear the per-server discovery errors accumulated so far."""
        errors = dict(self._failed)
        self._failed.clear()
        return errors

    def _store_cache_entry(
        self, key: str, alias: str, tools: dict[str, type[BaseTool]]
    ) -> None:
        self._cache[key] = tools
        self._cache_keys_by_alias.setdefault(alias, set()).add(key)

    def _drop_alias_cache(self, alias: str) -> None:
        for key in self._cache_keys_by_alias.pop(alias, set()):
            self._cache.pop(key, None)

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

        if isinstance(srv.auth, MCPOAuth):
            return await self._discover_oauth_http(srv, url=url)

        self._needs_auth.discard(srv.name)
        headers = srv.http_headers()
        try:
            remotes = await list_tools_http(
                url, headers=headers, startup_timeout_sec=srv.startup_timeout_sec
            )
        except Exception as exc:
            logger.warning("MCP HTTP discovery failed for %s: %s", url, exc)
            self._failed[srv.name] = str(exc)
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

    async def _discover_oauth_http(
        self, srv: MCPHttp | MCPStreamableHttp, *, url: str
    ) -> dict[str, type[BaseTool]] | None:
        alias = srv.name
        self._servers_by_alias[alias] = srv
        lock = self.oauth_lock_for(alias)

        try:
            storage = KeyringTokenStorage(alias=alias)
            current_fingerprint = Fingerprint.compute(srv)
            saved_fingerprint = await Fingerprint.load(alias)
            tokens = await storage.get_tokens()
        except MCPOAuthError as exc:
            logger.warning("%s", exc)
            self.mark_needs_auth(alias)
            return None

        if saved_fingerprint != current_fingerprint:
            await storage.delete_tokens()
            await storage.delete_client_info()
            await Fingerprint.delete(alias)
            self._drop_alias_cache(alias)
            self.mark_needs_auth(alias)
            return None

        if tokens is None:
            self.mark_needs_auth(alias)
            return None

        async def redirect_handler(_url: str) -> None:
            raise OAuthFlowError(
                f"MCP server {alias!r} requires interactive OAuth login"
            )

        async def callback_handler() -> tuple[str, str | None]:
            raise OAuthFlowError(
                f"MCP server {alias!r} requires interactive OAuth login"
            )

        provider = build_oauth_provider(
            srv, redirect_handler=redirect_handler, callback_handler=callback_handler
        )
        remotes = await self._list_oauth_remotes(srv, url=url, provider=provider)
        if remotes is None:
            return None

        self._needs_auth.discard(alias)
        tools: dict[str, type[BaseTool]] = {}
        for remote in remotes:
            try:
                proxy_cls = create_mcp_http_proxy_tool_class(
                    url=url,
                    remote=remote,
                    alias=alias,
                    server_hint=srv.prompt,
                    headers=srv.http_headers(),
                    auth=provider,
                    oauth_runtime=MCPHttpOAuthRuntime(
                        lock=lock, failure_callback=self.mark_oauth_failure
                    ),
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

    async def _list_oauth_remotes(
        self,
        srv: MCPHttp | MCPStreamableHttp,
        *,
        url: str,
        provider: OAuthClientProvider,
    ) -> list[RemoteTool] | None:
        alias = srv.name
        try:
            return await list_tools_http(
                url,
                headers=srv.http_headers(),
                auth=provider,
                startup_timeout_sec=srv.startup_timeout_sec,
            )
        except Exception as exc:
            # auth-flow errors arrive wrapped in an ExceptionGroup; unwrap to decide
            match unwrap_oauth_refresh_error(exc):
                case MCPOAuthInvalidGrant() as matched:
                    await self.mark_oauth_failure(alias)
                    logger.warning(
                        "MCP OAuth refresh permanently failed for %s: %s",
                        alias,
                        matched,
                    )
                case MCPOAuthTransientRefreshError() as matched:
                    self.mark_needs_auth(alias)
                    logger.warning(
                        "MCP OAuth refresh transiently failed for %s: %s",
                        alias,
                        matched,
                    )
                case OAuthFlowError() as matched:
                    self.mark_needs_auth(alias)
                    logger.warning(
                        "MCP OAuth discovery failed for %s: %s", alias, matched
                    )
                case None:
                    logger.warning("MCP HTTP discovery failed for %s: %s", url, exc)
                    self._failed[alias] = str(exc)
        return None

    async def _discover_stdio(self, srv: MCPStdio) -> dict[str, type[BaseTool]] | None:
        cmd = srv.argv()
        if not cmd:
            logger.warning("MCP stdio server '%s' has invalid/empty command", srv.name)
            return {}

        try:
            remotes = await list_tools_stdio(
                cmd,
                env=srv.env or None,
                cwd=srv.cwd,
                startup_timeout_sec=srv.startup_timeout_sec,
            )
        except Exception as exc:
            logger.warning("MCP stdio discovery failed for %r: %s", cmd, exc)
            self._failed[srv.name] = str(exc)
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
                    cwd=srv.cwd,
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

    def count_loaded(self, servers: list[MCPServer]) -> int:
        """Return how many of *servers* were successfully discovered (cached)."""
        return sum(self._server_key(srv) in self._cache for srv in servers)

    def clear(self) -> None:
        """Drop all cached entries, forcing re-discovery on next use."""
        self._cache.clear()
        self._cache_keys_by_alias.clear()
        self._servers_by_alias.clear()
        self._needs_auth.clear()
        self._failed.clear()

    def sync_active_servers(self, servers: list[MCPServer]) -> None:
        active = {srv.name: srv for srv in servers}
        self._servers_by_alias = active
        active_oauth_aliases = {
            srv.name
            for srv in servers
            if srv.transport in {"http", "streamable-http"}
            and isinstance(cast("MCPHttp | MCPStreamableHttp", srv).auth, MCPOAuth)
        }
        self._needs_auth.intersection_update(active_oauth_aliases)

    @property
    def needs_auth(self) -> set[str]:
        return set(self._needs_auth)

    def mark_needs_auth(self, alias: str) -> None:
        self._drop_alias_cache(alias)
        self._needs_auth.add(alias)

    async def mark_oauth_failure(self, alias: str) -> None:
        with suppress(MCPOAuthError):
            await KeyringTokenStorage(alias=alias).delete_tokens()
        self.mark_needs_auth(alias)

    def oauth_lock_for(self, alias: str) -> asyncio.Lock:
        if alias not in self._oauth_locks:
            self._oauth_locks[alias] = asyncio.Lock()
        return self._oauth_locks[alias]

    def disabled_aliases(self) -> set[str]:
        return {alias for alias, srv in self._servers_by_alias.items() if srv.disabled}

    def status(self) -> dict[str, AuthStatus]:
        statuses: dict[str, AuthStatus] = {}
        for alias, srv in self._servers_by_alias.items():
            match srv.transport:
                case "stdio":
                    statuses[alias] = AuthStatus.STDIO
                case "http" | "streamable-http":
                    if isinstance(srv.auth, MCPOAuth):
                        statuses[alias] = (
                            AuthStatus.NEEDS_AUTH
                            if alias in self._needs_auth
                            or self._server_key(srv) not in self._cache
                            else AuthStatus.OK
                        )
                    else:
                        statuses[alias] = AuthStatus.STATIC
        return statuses

    def _require_oauth_server(self, alias: str) -> MCPHttp | MCPStreamableHttp:
        srv = self._servers_by_alias.get(alias)
        if srv is None:
            raise ValueError(f"Unknown MCP server: {alias}")
        if srv.transport not in {"http", "streamable-http"}:
            raise ValueError(f"MCP server {alias!r} does not use HTTP transport")
        http_srv = cast("MCPHttp | MCPStreamableHttp", srv)
        if not isinstance(http_srv.auth, MCPOAuth):
            raise ValueError(f"MCP server {alias!r} is not configured for OAuth")
        return http_srv

    async def login(
        self, alias: str, *, on_url: Callable[[str], Awaitable[None]]
    ) -> None:
        srv = self._require_oauth_server(alias)
        async with self.oauth_lock_for(alias):
            await perform_oauth_login(srv, on_url=on_url)
            self._drop_alias_cache(alias)
            tools = await self._discover_server(srv)
            if tools is None:
                self.mark_needs_auth(alias)
                raise MCPOAuthLoginFailed(
                    server_alias=alias,
                    reason="login completed but tool discovery failed",
                )
            self._needs_auth.discard(alias)
            self._store_cache_entry(self._server_key(srv), alias, tools)

    async def logout(self, alias: str) -> None:
        srv = self._require_oauth_server(alias)
        async with self.oauth_lock_for(alias):
            storage = KeyringTokenStorage(alias=alias)
            await storage.delete_tokens()
            await storage.delete_client_info()
            await Fingerprint.delete(alias)
            self._drop_alias_cache(alias)
            self.mark_needs_auth(alias)
            self._servers_by_alias[alias] = srv
