"""Legacy-harness MCP discovery, raw-descriptor caching, and proxy construction."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import inspect
from pathlib import Path
from typing import cast

from vibe.core.config import MCPHttp, MCPOAuth, MCPServer, MCPStdio, MCPStreamableHttp
from vibe.core.tools.base import BaseTool
from vibe.core.tools.mcp.authorization import (
    MCPAuthorizationProvider,
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationRequiredSink,
    MCPAuthorizationSnapshot,
)
from vibe.core.tools.mcp.descriptor_cache import (
    LegacyMCPDescriptorCache,
    descriptor_cache_key,
)
from vibe.core.tools.mcp.tools import (
    MCPHttpAuthorizationRuntime,
    authorization_required_result,
    create_mcp_http_proxy_tool_class,
    create_mcp_stdio_proxy_tool_class,
    is_authorization_rejection,
    list_tools_http,
    list_tools_stdio,
)
from vibe.core.tools.remote import AuthStatus, RemoteTool
from vibe.core.utils import run_sync
from vibe.observability.logging import logger


@dataclass(frozen=True, slots=True)
class _MemoryDescriptorRecord:
    key: str
    source_name: str
    discovered_at: datetime
    last_used_at: datetime
    descriptors: tuple[RemoteTool, ...]


class MCPRegistry:
    """Host-scoped legacy discovery with an independent persistent raw cache."""

    def __init__(
        self,
        *,
        descriptor_cache_root: Path | None = None,
        descriptor_cache_ttl_s: float = 86_400.0,
    ) -> None:
        self._cache: dict[str, dict[str, type[BaseTool]]] = {}
        self._memory_records: dict[str, _MemoryDescriptorRecord] = {}
        self._cache_keys_by_alias: dict[str, set[str]] = {}
        self._servers_by_alias: dict[str, MCPServer] = {}
        self._needs_auth: set[str] = set()
        self._descriptor_revisions: dict[str, str] = {}
        self._failed: dict[str, str] = {}
        self._force_refresh: set[str] = set()
        self._authorization_provider: MCPAuthorizationProvider | None = None
        self._authorization_refs: dict[str, MCPAuthorizationRef] = {}
        self._authorization_required_sink: MCPAuthorizationRequiredSink | None = None
        self._descriptor_cache_root = descriptor_cache_root
        self._descriptor_cache_ttl_s = descriptor_cache_ttl_s
        self._descriptor_cache = (
            LegacyMCPDescriptorCache(
                descriptor_cache_root, ttl_s=descriptor_cache_ttl_s
            )
            if descriptor_cache_root is not None
            else None
        )

    def configure_authorization(
        self,
        provider: MCPAuthorizationProvider,
        references: Mapping[str, MCPAuthorizationRef],
        *,
        required_sink: MCPAuthorizationRequiredSink | None = None,
        descriptor_cache_root: Path | None = None,
        descriptor_cache_ttl_s: float = 86_400.0,
    ) -> None:
        self._authorization_provider = provider
        self._authorization_refs = dict(references)
        self._authorization_required_sink = required_sink
        self._descriptor_cache_root = descriptor_cache_root
        self._descriptor_cache_ttl_s = descriptor_cache_ttl_s
        self._descriptor_cache = (
            LegacyMCPDescriptorCache(
                descriptor_cache_root, ttl_s=descriptor_cache_ttl_s
            )
            if descriptor_cache_root is not None
            else None
        )

    @staticmethod
    def _format_mcp_error(exc: BaseException) -> str:
        if isinstance(exc, BaseExceptionGroup):
            messages = [
                formatted
                for child in exc.exceptions
                if (formatted := MCPRegistry._format_mcp_error(child))
            ]
            return "; ".join(messages)
        return str(exc)

    @staticmethod
    def _format_failed(exc: BaseException) -> str:
        return MCPRegistry._format_mcp_error(exc) or type(exc).__name__

    @staticmethod
    def _server_key(srv: MCPServer, descriptor_revision: str = "fallback") -> str:
        raw = f"{srv.model_dump_json(exclude_none=False)}\0{descriptor_revision}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_tools(self, servers: list[MCPServer]) -> dict[str, type[BaseTool]]:
        return run_sync(self.get_tools_async(servers))

    async def get_tools_async(
        self, servers: list[MCPServer]
    ) -> dict[str, type[BaseTool]]:
        self.sync_active_servers(servers)
        result: dict[str, type[BaseTool]] = {}
        to_discover: list[
            tuple[str, MCPServer, MCPAuthorizationSnapshot, MCPAuthorizationRef]
        ] = []
        now = _utc_now()
        for server in servers:
            reference = self._authorization_refs.get(server.name)
            if server.disabled and server.name not in self._force_refresh:
                if reference is not None:
                    self._descriptor_revisions[server.name] = (
                        reference.descriptor_revision
                    )
                self._needs_auth.discard(server.name)
                continue
            resolved = await self._resolve_authorization(server)
            if isinstance(resolved, MCPAuthorizationRequired):
                self._descriptor_revisions[server.name] = resolved.descriptor_revision
                await self._publish_authorization_required(server.name, resolved)
                continue
            authorization, reference = resolved
            self._descriptor_revisions[server.name] = authorization.descriptor_revision
            key = descriptor_cache_key(
                server_fingerprint=reference.server_fingerprint,
                descriptor_revision=authorization.descriptor_revision,
            )
            if server.name not in self._force_refresh:
                memory = await self._memory_hit(key, server.name, now)
                if memory is not None:
                    result.update(memory)
                    continue
                persistent = await self._persistent_hit(
                    key, server, authorization, reference, now
                )
                if persistent is not None:
                    result.update(persistent)
                    continue
            to_discover.append((key, server, authorization, reference))

        if to_discover:
            result.update(await self._discover_all(to_discover))
        return result

    def clone_configuration(self) -> MCPRegistry:
        """Create an independent registry configured before deferred discovery."""
        clone = MCPRegistry(
            descriptor_cache_root=self._descriptor_cache_root,
            descriptor_cache_ttl_s=self._descriptor_cache_ttl_s,
        )
        if self._authorization_provider is not None:
            clone.configure_authorization(
                self._authorization_provider,
                self._authorization_refs,
                required_sink=self._authorization_required_sink,
                descriptor_cache_root=self._descriptor_cache_root,
                descriptor_cache_ttl_s=self._descriptor_cache_ttl_s,
            )
        return clone

    async def _memory_hit(
        self, key: str, source_name: str, now: datetime
    ) -> dict[str, type[BaseTool]] | None:
        record = self._memory_records.get(key)
        if record is None:
            return self._cache.get(key)
        age = (now - record.discovered_at).total_seconds()
        if (
            self._descriptor_cache_ttl_s <= 0
            or record.discovered_at > now
            or age >= self._descriptor_cache_ttl_s
        ):
            self._drop_key(key, source_name)
            return None
        touched = _MemoryDescriptorRecord(
            key=record.key,
            source_name=record.source_name,
            discovered_at=record.discovered_at,
            last_used_at=now,
            descriptors=record.descriptors,
        )
        self._memory_records[key] = touched
        if self._descriptor_cache is not None:
            await self._descriptor_cache.touch(
                key,
                source_name=source_name,
                discovered_at=record.discovered_at,
                now=now,
            )
        return self._cache.get(key)

    async def _persistent_hit(
        self,
        key: str,
        server: MCPServer,
        authorization: MCPAuthorizationSnapshot,
        reference: MCPAuthorizationRef,
        now: datetime,
    ) -> dict[str, type[BaseTool]] | None:
        if self._descriptor_cache is None:
            return None
        record = await self._descriptor_cache.read(
            key, source_name=server.name, now=now
        )
        if record is None:
            return None
        tools = self._build_tools(server, record.descriptors, reference)
        self._store_cache_entry(
            key,
            server.name,
            tools,
            descriptors=record.descriptors,
            discovered_at=record.discovered_at,
            last_used_at=record.last_used_at,
        )
        self._needs_auth.discard(server.name)
        return tools

    async def _discover_all(
        self,
        servers: list[
            tuple[str, MCPServer, MCPAuthorizationSnapshot, MCPAuthorizationRef]
        ],
    ) -> dict[str, type[BaseTool]]:
        results = await asyncio.gather(
            *(
                self._discover_server(server, authorization, reference)
                for _, server, authorization, reference in servers
            ),
            return_exceptions=True,
        )
        output: dict[str, type[BaseTool]] = {}
        for (key, server, _, reference), discovered in zip(
            servers, results, strict=True
        ):
            if isinstance(discovered, BaseException):
                formatted = self._format_failed(discovered)
                logger.warning(
                    "MCP discovery failed for server %r: %s", server.name, formatted
                )
                self._failed[server.name] = formatted
                continue
            if discovered is None:
                continue
            descriptors, tools, effective_authorization = discovered
            self._descriptor_revisions[server.name] = (
                effective_authorization.descriptor_revision
            )
            effective_key = descriptor_cache_key(
                server_fingerprint=reference.server_fingerprint,
                descriptor_revision=effective_authorization.descriptor_revision,
            )
            now = _utc_now()
            self._store_cache_entry(
                effective_key,
                server.name,
                tools,
                descriptors=descriptors,
                discovered_at=now,
                last_used_at=now,
            )
            if self._descriptor_cache is not None:
                await self._descriptor_cache.write(
                    effective_key,
                    source_name=server.name,
                    descriptors=descriptors,
                    now=now,
                )
            self._needs_auth.discard(server.name)
            self._force_refresh.discard(server.name)
            output.update(tools)
            if key != effective_key:
                self._drop_key(key, server.name)
        return output

    async def _discover_server(
        self,
        server: MCPServer,
        authorization: MCPAuthorizationSnapshot,
        reference: MCPAuthorizationRef,
    ) -> (
        tuple[
            tuple[RemoteTool, ...], dict[str, type[BaseTool]], MCPAuthorizationSnapshot
        ]
        | None
    ):
        match server.transport:
            case "http" | "streamable-http":
                return await self._discover_http(
                    cast(MCPHttp | MCPStreamableHttp, server), authorization, reference
                )
            case "stdio":
                descriptors = await self._discover_stdio(cast(MCPStdio, server))
                if descriptors is None:
                    return None
                return (
                    descriptors,
                    self._build_tools(server, descriptors, reference),
                    authorization,
                )
            case _:
                logger.warning("Unsupported MCP transport: %r", server.transport)
                return None

    async def _discover_http(
        self,
        server: MCPHttp | MCPStreamableHttp,
        authorization: MCPAuthorizationSnapshot,
        reference: MCPAuthorizationRef,
    ) -> (
        tuple[
            tuple[RemoteTool, ...], dict[str, type[BaseTool]], MCPAuthorizationSnapshot
        ]
        | None
    ):
        try:
            descriptors = tuple(
                await list_tools_http(
                    server.url,
                    headers=dict(authorization.headers),
                    startup_timeout_sec=server.startup_timeout_sec,
                )
            )
        except Exception as exc:
            if not is_authorization_rejection(exc):
                raise
            retried = await self._reject_and_retry_discovery(
                server, reference, authorization, exc
            )
            if retried is None:
                return None
            descriptors, authorization = retried
        return (
            descriptors,
            self._build_tools(server, descriptors, reference),
            authorization,
        )

    async def _reject_and_retry_discovery(
        self,
        server: MCPHttp | MCPStreamableHttp,
        reference: MCPAuthorizationRef,
        authorization: MCPAuthorizationSnapshot,
        rejected: Exception,
    ) -> tuple[tuple[RemoteTool, ...], MCPAuthorizationSnapshot] | None:
        provider = self._authorization_provider
        if provider is None:
            required = MCPAuthorizationRequired(
                reason="rejected",
                descriptor_revision=authorization.descriptor_revision,
                observed_connection_revision=authorization.connection_revision,
            )
            await self._publish_authorization_required(server.name, required)
            return None
        replacement = await provider.reject(
            reference,
            observed_connection_revision=authorization.connection_revision,
            reason="http_unauthorized",
        )
        if (
            isinstance(replacement, MCPAuthorizationSnapshot)
            and replacement.connection_revision != authorization.connection_revision
        ):
            try:
                return (
                    tuple(
                        await list_tools_http(
                            server.url,
                            headers=dict(replacement.headers),
                            startup_timeout_sec=server.startup_timeout_sec,
                        )
                    ),
                    replacement,
                )
            except Exception as retry_exc:
                if not is_authorization_rejection(retry_exc):
                    raise
                replacement = await provider.reject(
                    reference,
                    observed_connection_revision=replacement.connection_revision,
                    reason="http_unauthorized",
                )
        required = authorization_required_result(
            replacement, observed_connection_revision=authorization.connection_revision
        )
        await self._publish_authorization_required(server.name, required)
        logger.warning("MCP authorization rejected for server %r", server.name)
        return None

    async def _discover_stdio(self, server: MCPStdio) -> tuple[RemoteTool, ...] | None:
        command = server.argv()
        if not command:
            return ()
        try:
            return tuple(
                await list_tools_stdio(
                    command,
                    env=server.env or None,
                    cwd=server.cwd,
                    startup_timeout_sec=server.startup_timeout_sec,
                )
            )
        except Exception as exc:
            formatted = self._format_failed(exc)
            logger.warning("MCP stdio discovery failed for %r: %s", command, formatted)
            self._failed[server.name] = formatted
            return None

    def _build_tools(
        self,
        server: MCPServer,
        descriptors: tuple[RemoteTool, ...],
        reference: MCPAuthorizationRef,
    ) -> dict[str, type[BaseTool]]:
        tools: dict[str, type[BaseTool]] = {}
        for remote in descriptors:
            try:
                if isinstance(server, MCPStdio):
                    proxy = create_mcp_stdio_proxy_tool_class(
                        command=server.argv(),
                        remote=remote,
                        alias=server.name,
                        server_hint=server.prompt,
                        env=server.env or None,
                        cwd=server.cwd,
                        startup_timeout_sec=server.startup_timeout_sec,
                        tool_timeout_sec=server.tool_timeout_sec,
                        sampling_enabled=server.sampling_enabled,
                    )
                else:
                    provider = self._authorization_provider
                    proxy = create_mcp_http_proxy_tool_class(
                        url=server.url,
                        remote=remote,
                        alias=server.name,
                        server_hint=server.prompt,
                        headers=(server.http_headers() if provider is None else None),
                        authorization_runtime=(
                            MCPHttpAuthorizationRuntime(
                                provider=provider,
                                reference=reference,
                                required_sink=self._publish_authorization_required,
                            )
                            if provider is not None
                            else None
                        ),
                        startup_timeout_sec=server.startup_timeout_sec,
                        tool_timeout_sec=server.tool_timeout_sec,
                        sampling_enabled=server.sampling_enabled,
                    )
                tools[proxy.get_name()] = proxy
            except Exception as exc:
                logger.warning(
                    "Failed to register MCP tool %r from %r: %s",
                    remote.name,
                    server.name,
                    self._format_failed(exc),
                )
        return tools

    async def _resolve_authorization(
        self, server: MCPServer
    ) -> (
        tuple[MCPAuthorizationSnapshot, MCPAuthorizationRef] | MCPAuthorizationRequired
    ):
        reference = self._authorization_refs.get(server.name)
        provider = self._authorization_provider
        if reference is not None and provider is not None:
            result = await provider.resolve(reference)
            if isinstance(result, MCPAuthorizationRequired):
                self.mark_needs_auth(server.name)
                return result
            if result.descriptor_revision != reference.descriptor_revision:
                required = MCPAuthorizationRequired(
                    reason="invalid", descriptor_revision=result.descriptor_revision
                )
                self.mark_needs_auth(server.name)
                return required
            self._needs_auth.discard(server.name)
            return result, reference
        reference = MCPAuthorizationRef(
            server_name=server.name,
            server_fingerprint=self._server_key(server),
            kind=(
                "oauth"
                if isinstance(server, MCPHttp | MCPStreamableHttp)
                and isinstance(server.auth, MCPOAuth)
                else "static"
                if isinstance(server, MCPHttp | MCPStreamableHttp)
                else "none"
            ),
            descriptor_revision="legacy-static",
        )
        if reference.kind == "oauth":
            required = MCPAuthorizationRequired(
                reason="missing", descriptor_revision="legacy-oauth-unconfigured"
            )
            self.mark_needs_auth(server.name)
            return required
        headers = (
            server.http_headers()
            if isinstance(server, MCPHttp | MCPStreamableHttp)
            else {}
        )
        return (
            MCPAuthorizationSnapshot(
                headers=headers,
                connection_revision="legacy-static",
                descriptor_revision="legacy-static",
            ),
            reference,
        )

    async def _publish_authorization_required(
        self, name: str, required: MCPAuthorizationRequired
    ) -> None:
        self.mark_needs_auth(name, required.descriptor_revision)
        if self._authorization_required_sink is None:
            return
        result = self._authorization_required_sink(name, required)
        if inspect.isawaitable(result):
            await result

    def _store_cache_entry(
        self,
        key: str,
        alias: str,
        tools: dict[str, type[BaseTool]],
        *,
        descriptors: tuple[RemoteTool, ...] = (),
        discovered_at: datetime | None = None,
        last_used_at: datetime | None = None,
    ) -> None:
        self._cache[key] = tools
        self._cache_keys_by_alias.setdefault(alias, set()).add(key)
        if discovered_at is not None and last_used_at is not None:
            self._memory_records[key] = _MemoryDescriptorRecord(
                key=key,
                source_name=alias,
                discovered_at=discovered_at,
                last_used_at=last_used_at,
                descriptors=descriptors,
            )

    def _drop_key(self, key: str, alias: str) -> None:
        self._cache.pop(key, None)
        self._memory_records.pop(key, None)
        keys = self._cache_keys_by_alias.get(alias)
        if keys is not None:
            keys.discard(key)
            if not keys:
                self._cache_keys_by_alias.pop(alias, None)

    def _drop_alias_cache(self, alias: str) -> None:
        for key in tuple(self._cache_keys_by_alias.pop(alias, set())):
            self._cache.pop(key, None)
            self._memory_records.pop(key, None)

    def pop_failed(self) -> dict[str, str]:
        errors = dict(self._failed)
        self._failed.clear()
        return errors

    def count_loaded(self, servers: list[MCPServer]) -> int:
        return sum(
            bool(self._cache_keys_by_alias.get(server.name)) for server in servers
        )

    def descriptors_for(self, alias: str) -> tuple[RemoteTool, ...]:
        descriptors: dict[str, RemoteTool] = {}
        for key in sorted(self._cache_keys_by_alias.get(alias, ())):
            record = self._memory_records.get(key)
            if record is None:
                continue
            for descriptor in record.descriptors:
                descriptors.setdefault(descriptor.name, descriptor)
        return tuple(descriptor for _, descriptor in sorted(descriptors.items()))

    def clear(self) -> None:
        self._cache.clear()
        self._memory_records.clear()
        self._cache_keys_by_alias.clear()
        self._needs_auth.clear()
        self._descriptor_revisions.clear()
        self._failed.clear()
        self._force_refresh.update(self._servers_by_alias)

    def sync_active_servers(self, servers: list[MCPServer]) -> None:
        active = {server.name: server for server in servers}
        active_oauth = {
            alias
            for alias, server in active.items()
            if isinstance(server, MCPHttp | MCPStreamableHttp)
            and isinstance(server.auth, MCPOAuth)
        }
        removed = set(self._servers_by_alias) - set(active)
        for alias in removed:
            self._drop_alias_cache(alias)
            self._descriptor_revisions.pop(alias, None)
        self._servers_by_alias = active
        self._needs_auth.intersection_update(active_oauth)
        self._force_refresh.intersection_update(active)

    @property
    def needs_auth(self) -> set[str]:
        return set(self._needs_auth)

    def mark_needs_auth(
        self, alias: str, descriptor_revision: str | None = None
    ) -> None:
        self._drop_alias_cache(alias)
        self._needs_auth.add(alias)
        if descriptor_revision is not None:
            self._descriptor_revisions[alias] = descriptor_revision

    def record_disabled_authorization(
        self, alias: str, descriptor_revision: str
    ) -> None:
        self._drop_alias_cache(alias)
        self._needs_auth.discard(alias)
        self._force_refresh.discard(alias)
        self._descriptor_revisions[alias] = descriptor_revision

    def descriptor_revision(self, alias: str) -> str:
        return self._descriptor_revisions.get(alias, "")

    def invalidate(self, alias: str) -> None:
        self._drop_alias_cache(alias)
        self._force_refresh.add(alias)

    def disabled_aliases(self) -> set[str]:
        return {
            alias for alias, server in self._servers_by_alias.items() if server.disabled
        }

    def status(self) -> dict[str, AuthStatus]:
        statuses: dict[str, AuthStatus] = {}
        for alias, server in self._servers_by_alias.items():
            if isinstance(server, MCPStdio):
                statuses[alias] = AuthStatus.STDIO
            elif isinstance(server.auth, MCPOAuth):
                statuses[alias] = (
                    AuthStatus.NEEDS_AUTH
                    if alias in self._needs_auth
                    else AuthStatus.OK
                )
            else:
                statuses[alias] = AuthStatus.STATIC
        return statuses


def _utc_now() -> datetime:
    return datetime.now(UTC)
