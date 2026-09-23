"""Unified Runtime ownership of MCP descriptors, routes, calls, and cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
import inspect
import logging
import math
from pathlib import Path
from typing import Literal

from mistralai_vibe_local_harness.vibe._mcp_cache import MCPDescriptorCache
from mistralai_vibe_local_harness.vibe._mcp_models import (
    JsonObject,
    MCPAuthorizationProvider,
    MCPAuthorizationRejected,
    MCPAuthorizationRequired,
    MCPAuthorizationRequiredSignal,
    MCPAuthorizationSnapshot,
    MCPDescriptorCacheKey,
    MCPDescriptorCachePolicy,
    MCPDescriptorCacheRecordV1,
    MCPHTTPTransportPolicy,
    MCPNormalizedResult,
    MCPRemoteToolDescriptor,
    MCPRouteSnapshot,
    MCPRuntimeEventSink,
    MCPRuntimeFailure,
    MCPSamplingCallback,
    MCPSamplingCompletion,
    MCPSourceState,
    MCPTransportFactory,
    ResolvedMCPCatalog,
    ResolvedMCPServerConfig,
)
from mistralai_vibe_local_harness.vibe._mcp_naming import (
    NAMING_VERSION,
    build_route_snapshot,
)
from mistralai_vibe_local_harness.vibe._mcp_pool import MCPStdioPool, MCPStdioPoolLease

logger = logging.getLogger(__name__)

_DEFAULT_HTTP_TRANSPORT_POLICY = MCPHTTPTransportPolicy()

type _ServerResolution = tuple[
    ResolvedMCPServerConfig,
    tuple[MCPRemoteToolDescriptor, ...] | None,
    MCPSourceState,
    MCPAuthorizationRequired | None,
]


class MCPRuntime:
    def __init__(
        self,
        *,
        cache_root: Path,
        cache_policy: MCPDescriptorCachePolicy,
        authorization_provider: MCPAuthorizationProvider,
        http_transport_policy: MCPHTTPTransportPolicy = _DEFAULT_HTTP_TRANSPORT_POLICY,
        transport_factory: MCPTransportFactory | None = None,
        stdio_pool: MCPStdioPool | None = None,
        sampling_completion: MCPSamplingCompletion | None = None,
        event_sink: MCPRuntimeEventSink | None = None,
        claimed_groups: frozenset[str] = frozenset(),
        discovery_concurrency: int = 4,
    ) -> None:
        if discovery_concurrency < 1:
            raise ValueError("MCP discovery concurrency must be positive")
        self._cache = MCPDescriptorCache(cache_root, cache_policy)
        self._cache_policy = cache_policy
        self._authorization_provider = authorization_provider
        self._http_transport_policy = http_transport_policy
        self._transport_factory = transport_factory
        self._shared_pool = stdio_pool
        self._owned_pool: MCPStdioPool | None = None
        self._lease: MCPStdioPoolLease | None = None
        self._sampling_completion = sampling_completion
        self._event_sink = event_sink
        self._claimed_groups = claimed_groups
        self._discovery_concurrency = discovery_concurrency
        self._memory_cache: dict[MCPDescriptorCacheKey, MCPDescriptorCacheRecordV1] = {}
        self._configuration = ResolvedMCPCatalog(revision="empty", servers=())
        self._accepted_descriptors: dict[str, tuple[MCPRemoteToolDescriptor, ...]] = {}
        self._snapshot = build_route_snapshot(
            catalog_revision="empty",
            resolved=(),
            sources=(),
            claimed_groups=claimed_groups,
        )
        self._lock = asyncio.Lock()
        self._closed = False
        self._accept_snapshot: Callable[[MCPRouteSnapshot], Awaitable[None]] | None = (
            None
        )

    @property
    def snapshot(self) -> MCPRouteSnapshot:
        return self._snapshot

    def bind_snapshot_acceptor(
        self, acceptor: Callable[[MCPRouteSnapshot], Awaitable[None]]
    ) -> None:
        if self._accept_snapshot is not None:
            raise RuntimeError("MCP snapshot acceptor is already bound")
        self._accept_snapshot = acceptor

    async def reconfigure(
        self,
        configuration: ResolvedMCPCatalog,
        *,
        force_remote_discovery: bool,
        push_to_core: bool = True,
    ) -> MCPRouteSnapshot:
        """Resolve the catalog and store the resulting route snapshot.

        With ``push_to_core`` false the snapshot is stored but not handed to the
        capability acceptor, so the routes become dispatchable without a Core
        capability reconfigure. This lets a caller ready the routes while Core is
        mid-turn (which rejects reconfigure) and publish them once Core is idle
        via ``push_snapshot_to_core``.
        """
        forced_names = (
            frozenset(server.name for server in configuration.servers)
            if force_remote_discovery
            else frozenset()
        )
        async with self._lock:
            self._guard_open()
            return await self._reconfigure(
                configuration, forced_names=forced_names, push_to_core=push_to_core
            )

    async def push_snapshot_to_core(self) -> None:
        """Hand the current route snapshot to the capability acceptor.

        Pairs with a ``reconfigure(push_to_core=False)`` to publish the readied
        routes to Core once it is idle.
        """
        async with self._lock:
            self._guard_open()
            await self._accept(self._snapshot)

    async def authorization_changed(
        self, *, name: str, descriptor_revision: str
    ) -> MCPRouteSnapshot:
        async with self._lock:
            self._guard_open()
            server = self._server(name)
            updated_server = replace(
                server,
                authorization=replace(
                    server.authorization, descriptor_revision=descriptor_revision
                ),
            )
            configuration = replace(
                self._configuration,
                servers=tuple(
                    updated_server if candidate.name == name else candidate
                    for candidate in self._configuration.servers
                ),
            )
            if server.disabled:
                self._configuration = configuration
                self._invalidate_memory(server)
                self._snapshot = replace(
                    self._snapshot,
                    sources=tuple(
                        replace(
                            source,
                            status="disabled",
                            descriptor_revision=descriptor_revision,
                            error=None,
                        )
                        if source.name == name
                        else source
                        for source in self._snapshot.sources
                    ),
                )
                return self._snapshot
            authorization = await self._authorization_provider.resolve(
                updated_server.authorization
            )
            if authorization.descriptor_revision != descriptor_revision:
                raise MCPRuntimeFailure(
                    "mcp_stale_authorization",
                    "MCP authorization descriptor revision is stale",
                )
            self._invalidate_memory(server)
            if isinstance(authorization, MCPAuthorizationRequired):
                self._configuration = configuration
                self._accepted_descriptors.pop(name, None)
                snapshot = await self._suspend_locked(
                    name=name,
                    tool_name=None,
                    source_status="needs_auth",
                    descriptor_revision=descriptor_revision,
                )
                await self._authorization_required(updated_server, authorization)
                return snapshot
            return await self._reconfigure_authorization(
                configuration, name=name, authorization=authorization
            )

    async def suspend(
        self, *, name: str, tool_name: str | None, source_status: str = "disabled"
    ) -> MCPRouteSnapshot:
        async with self._lock:
            self._guard_open()
            return await self._suspend_locked(
                name=name, tool_name=tool_name, source_status=source_status
            )

    async def execute(
        self, *, group_name: str, tool_name: str, arguments: JsonObject
    ) -> MCPNormalizedResult:
        self._guard_open()
        snapshot = self._snapshot
        route = snapshot.routes.get((group_name, tool_name))
        if route is None:
            raise MCPRuntimeFailure(
                "mcp_unknown_route", "The provided tool route is not available"
            )
        server = self._server(route.descriptor.server_name)
        if server.transport == "stdio":
            return await self._stdio_lease().call_tool(
                server, name=route.descriptor.remote_name, arguments=arguments
            )
        sampling_callback = self._sampling_callback(server)
        authorization = await self._authorization_provider.resolve(server.authorization)
        if isinstance(authorization, MCPAuthorizationRequired):
            async with self._lock:
                self._accepted_descriptors.pop(server.name, None)
                await self._suspend_locked(
                    name=server.name,
                    tool_name=None,
                    source_status="needs_auth",
                    descriptor_revision=authorization.descriptor_revision,
                )
            await self._authorization_required(server, authorization)
            raise MCPRuntimeFailure(
                "mcp_authorization_required", "MCP server requires authorization"
            )
        try:
            return await self._call_http(
                server,
                authorization,
                route.descriptor.remote_name,
                arguments,
                sampling_callback,
            )
        except MCPAuthorizationRejected as rejected:
            return await self._retry_rejected_call(
                server,
                authorization,
                rejected,
                route.descriptor.remote_name,
                arguments,
                sampling_callback,
            )

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            lease, self._lease = self._lease, None
            owned, self._owned_pool = self._owned_pool, None
        if lease is not None:
            await lease.aclose()
        if owned is not None:
            await owned.aclose()

    async def _reconfigure(
        self,
        configuration: ResolvedMCPCatalog,
        *,
        forced_names: frozenset[str],
        push_to_core: bool = True,
    ) -> MCPRouteSnapshot:
        _validate_configuration(configuration)
        previous_configuration = self._configuration
        semaphore = asyncio.Semaphore(self._discovery_concurrency)

        async def resolve(server: ResolvedMCPServerConfig) -> _ServerResolution:
            forced = server.name in forced_names
            if server.disabled and not forced:
                descriptors = self._accepted_descriptors.get(server.name)
                return (
                    server,
                    descriptors,
                    MCPSourceState(
                        name=server.name,
                        status="disabled",
                        descriptors=descriptors or (),
                        descriptor_revision=server.authorization.descriptor_revision,
                    ),
                    None,
                )
            async with semaphore:
                try:
                    result = await self._resolve_server(
                        server, force_remote_discovery=forced
                    )
                except Exception as exc:
                    # A single source must never abort reconfiguration of the others:
                    # a failing MCP connection is isolated as an unavailable source so
                    # the session and every healthy source stay usable. CancelledError
                    # is a BaseException and is intentionally left to propagate.
                    result = self._unavailable_resolution(server, error=exc)
            if server.disabled and result[1] is not None:
                return result[0], result[1], replace(result[2], status="disabled"), None
            return result

        results = await asyncio.gather(
            *(resolve(server) for server in configuration.servers)
        )
        return await self._accept_resolutions(
            configuration,
            results,
            previous_configuration=previous_configuration,
            push_to_core=push_to_core,
        )

    def _authorization_required_resolution(
        self, server: ResolvedMCPServerConfig, required: MCPAuthorizationRequired
    ) -> _ServerResolution:
        """Map a rejected credential to a source state based on the auth kind.

        Only an OAuth source can be recovered interactively (via `/mcp login`), so it
        enters needs_auth and emits the auth-required signal that drives the login
        prompt. A static or unauthenticated source whose credential is rejected has no
        such remedy here, so it is reported as unavailable with an auth error rather
        than prompting a login that cannot succeed.
        """
        if server.authorization.kind == "oauth":
            return (
                server,
                None,
                MCPSourceState(
                    name=server.name,
                    status="needs_auth",
                    descriptor_revision=required.descriptor_revision,
                ),
                required,
            )
        logger.debug(
            "MCP source %r (kind=%s) rejected; reporting unavailable with auth error",
            server.name,
            server.authorization.kind,
        )
        return (
            server,
            None,
            MCPSourceState(
                name=server.name,
                status="unavailable",
                descriptor_revision=required.descriptor_revision,
                error="authentication was rejected",
            ),
            None,
        )

    def _unavailable_resolution(
        self, server: ResolvedMCPServerConfig, *, error: BaseException
    ) -> _ServerResolution:
        code = (
            error.code
            if isinstance(error, MCPRuntimeFailure)
            else "mcp_transport_failed"
        )
        logger.warning(
            "MCP source %r failed to resolve and was isolated as unavailable: %s",
            server.name,
            code,
            exc_info=error,
        )
        return (
            server,
            None,
            MCPSourceState(
                name=server.name,
                status="unavailable",
                descriptor_revision=server.authorization.descriptor_revision,
                error=code,
            ),
            None,
        )

    async def _reconfigure_authorization(
        self,
        configuration: ResolvedMCPCatalog,
        *,
        name: str,
        authorization: MCPAuthorizationSnapshot,
    ) -> MCPRouteSnapshot:
        previous_configuration = self._configuration
        resolutions: list[_ServerResolution] = []
        for server in configuration.servers:
            if server.name == name:
                resolutions.append(
                    await self._resolve_server(
                        server, force_remote_discovery=True, authorization=authorization
                    )
                )
                continue
            descriptors = self._accepted_descriptors.get(server.name)
            source = self._source_state(server.name)
            resolutions.append((
                server,
                descriptors,
                source
                or MCPSourceState(
                    name=server.name,
                    status="disabled" if server.disabled else "unavailable",
                    descriptors=descriptors or (),
                    descriptor_revision=server.authorization.descriptor_revision,
                ),
                None,
            ))
        return await self._accept_resolutions(
            configuration, resolutions, previous_configuration=previous_configuration
        )

    async def _accept_resolutions(
        self,
        configuration: ResolvedMCPCatalog,
        resolutions: list[_ServerResolution],
        *,
        previous_configuration: ResolvedMCPCatalog,
        push_to_core: bool = True,
    ) -> MCPRouteSnapshot:
        callable_descriptors = [
            (server, descriptors)
            for server, descriptors, _, _ in resolutions
            if descriptors is not None and not server.disabled
        ]
        sources = tuple(state for _, _, state, _ in resolutions)
        candidate = build_route_snapshot(
            catalog_revision=configuration.revision,
            resolved=callable_descriptors,
            sources=sources,
            claimed_groups=self._claimed_groups,
            tool_filter=configuration.tool_filter,
        )
        if push_to_core:
            await self._accept(candidate)
        self._configuration = configuration
        self._accepted_descriptors = {
            server.name: descriptors
            for server, descriptors, _, _ in resolutions
            if descriptors is not None
        }
        self._snapshot = candidate
        for server, _, _, required in resolutions:
            if required is not None:
                await self._authorization_required(server, required)
        await self._close_removed_stdio_connections(
            previous_configuration, configuration
        )
        return candidate

    def _source_state(self, name: str) -> MCPSourceState | None:
        return next(
            (source for source in self._snapshot.sources if source.name == name), None
        )

    async def _resolve_server(
        self,
        server: ResolvedMCPServerConfig,
        *,
        force_remote_discovery: bool,
        authorization: MCPAuthorizationSnapshot | None = None,
    ) -> _ServerResolution:
        authorization_result: MCPAuthorizationSnapshot | MCPAuthorizationRequired = (
            authorization
            if authorization is not None
            else await self._authorization_provider.resolve(server.authorization)
        )
        if isinstance(authorization_result, MCPAuthorizationRequired):
            return self._authorization_required_resolution(server, authorization_result)
        authorization = authorization_result
        if (
            authorization.descriptor_revision
            != server.authorization.descriptor_revision
        ):
            raise MCPRuntimeFailure(
                "mcp_stale_authorization",
                "MCP authorization descriptor revision is stale",
            )
        key = _cache_key(server)
        if not force_remote_discovery:
            cached = await self._read_descriptor(key, server.name)
            if cached is not None:
                return (
                    server,
                    cached.descriptors,
                    MCPSourceState(
                        name=server.name,
                        status=_available_status(server),
                        descriptors=cached.descriptors,
                        descriptor_revision=authorization.descriptor_revision,
                    ),
                    None,
                )
        try:
            descriptors = await self._discover(server, authorization)
            discovery_authorization = authorization
        except MCPAuthorizationRejected as rejected:
            retried = await self._retry_rejected_discovery(
                server, authorization, rejected
            )
            if isinstance(retried, MCPAuthorizationRequired):
                return self._authorization_required_resolution(server, retried)
            descriptors, discovery_authorization = retried
        except MCPRuntimeFailure as exc:
            return (
                server,
                None,
                MCPSourceState(
                    name=server.name,
                    status="unavailable",
                    descriptor_revision=authorization.descriptor_revision,
                    error=exc.code,
                ),
                None,
            )
        _validate_descriptors(descriptors, self._cache_policy)
        key = _cache_key(server)
        now = _utc_now()
        record = MCPDescriptorCacheRecordV1(
            key=key,
            source_name=server.name,
            discovered_at=now,
            last_used_at=now,
            descriptors=descriptors,
        )
        self._memory_cache[key] = record
        await self._cache.write(
            key, source_name=server.name, descriptors=descriptors, now=now
        )
        return (
            server,
            descriptors,
            MCPSourceState(
                name=server.name,
                status=_available_status(server),
                descriptors=descriptors,
                descriptor_revision=discovery_authorization.descriptor_revision,
            ),
            None,
        )

    async def _read_descriptor(
        self, key: MCPDescriptorCacheKey, source_name: str
    ) -> MCPDescriptorCacheRecordV1 | None:
        now = _utc_now()
        if record := self._memory_cache.get(key):
            age = (now - record.discovered_at).total_seconds()
            if (
                self._cache_policy.ttl_s > 0
                and record.discovered_at <= now
                and age < self._cache_policy.ttl_s
            ):
                touched = replace(record, last_used_at=now)
                self._memory_cache[key] = touched
                await self._cache.touch(
                    key,
                    source_name=source_name,
                    discovered_at=record.discovered_at,
                    now=now,
                )
                return touched
            self._memory_cache.pop(key, None)
        record = await self._cache.read(key, source_name=source_name, now=now)
        if record is not None:
            self._memory_cache[key] = record
        return record

    async def _discover(
        self, server: ResolvedMCPServerConfig, authorization: MCPAuthorizationSnapshot
    ) -> tuple[MCPRemoteToolDescriptor, ...]:
        if server.transport == "stdio":
            return await self._stdio_lease().list_tools(server)
        from mistralai_vibe_local_harness.vibe._mcp_transport import discover_http

        return await discover_http(
            server,
            authorization,
            http_transport_policy=self._http_transport_policy,
            sampling_callback=self._sampling_callback(server),
        )

    async def _call_http(
        self,
        server: ResolvedMCPServerConfig,
        authorization: MCPAuthorizationSnapshot,
        tool_name: str,
        arguments: JsonObject,
        sampling_callback: MCPSamplingCallback | None,
    ) -> MCPNormalizedResult:
        from mistralai_vibe_local_harness.vibe._mcp_transport import call_http

        return await call_http(
            server,
            authorization,
            http_transport_policy=self._http_transport_policy,
            tool_name=tool_name,
            arguments=arguments,
            sampling_callback=sampling_callback,
        )

    def _stdio_lease(self) -> MCPStdioPoolLease:
        if self._lease is None:
            pool = self._shared_pool
            if pool is None:
                pool = MCPStdioPool(
                    self._transport_factory,
                    sampling_completion=self._sampling_completion,
                )
                self._owned_pool = pool
            self._lease = pool.lease()
        return self._lease

    def _sampling_callback(
        self, server: ResolvedMCPServerConfig
    ) -> MCPSamplingCallback | None:
        if not server.sampling_enabled or self._sampling_completion is None:
            return None
        from mistralai_vibe_local_harness.vibe._mcp_sampling import (
            build_sampling_callback,
        )

        return build_sampling_callback(self._sampling_completion)

    async def _authorization_required(
        self, server: ResolvedMCPServerConfig, required: MCPAuthorizationRequired
    ) -> None:
        if self._event_sink is None:
            return
        result = self._event_sink(
            MCPAuthorizationRequiredSignal(
                server_name=server.name,
                reason=required.reason,
                descriptor_revision=required.descriptor_revision,
                observed_connection_revision=required.observed_connection_revision,
            )
        )
        if inspect.isawaitable(result):
            await result

    async def _invalidate_descriptor(
        self, server: ResolvedMCPServerConfig, descriptor_revision: str
    ) -> None:
        key = MCPDescriptorCacheKey(
            format_version=1,
            naming_version=NAMING_VERSION,
            server_fingerprint=server.authorization.server_fingerprint,
            authorization_descriptor_revision=descriptor_revision,
        )
        self._memory_cache.pop(key, None)
        await self._cache.invalidate(key)

    async def _retry_rejected_discovery(
        self,
        server: ResolvedMCPServerConfig,
        authorization: MCPAuthorizationSnapshot,
        rejected: MCPAuthorizationRejected,
    ) -> (
        tuple[tuple[MCPRemoteToolDescriptor, ...], MCPAuthorizationSnapshot]
        | MCPAuthorizationRequired
    ):
        replacement = await self._authorization_provider.reject(
            server.authorization,
            observed_connection_revision=authorization.connection_revision,
            reason=rejected.reason,
        )
        await self._invalidate_descriptor(server, authorization.descriptor_revision)
        if (
            isinstance(replacement, MCPAuthorizationSnapshot)
            and replacement.connection_revision != authorization.connection_revision
        ):
            try:
                return await self._discover(server, replacement), replacement
            except MCPAuthorizationRejected as retry_rejected:
                replacement = await self._authorization_provider.reject(
                    server.authorization,
                    observed_connection_revision=replacement.connection_revision,
                    reason=retry_rejected.reason,
                )
        return _authorization_required_result(
            replacement, observed_connection_revision=authorization.connection_revision
        )

    async def _retry_rejected_call(
        self,
        server: ResolvedMCPServerConfig,
        authorization: MCPAuthorizationSnapshot,
        rejected: MCPAuthorizationRejected,
        tool_name: str,
        arguments: JsonObject,
        sampling_callback: MCPSamplingCallback | None,
    ) -> MCPNormalizedResult:
        replacement = await self._authorization_provider.reject(
            server.authorization,
            observed_connection_revision=authorization.connection_revision,
            reason=rejected.reason,
        )
        if (
            isinstance(replacement, MCPAuthorizationSnapshot)
            and replacement.connection_revision != authorization.connection_revision
        ):
            try:
                return await self._call_http(
                    server, replacement, tool_name, arguments, sampling_callback
                )
            except MCPAuthorizationRejected as retry_rejected:
                replacement = await self._authorization_provider.reject(
                    server.authorization,
                    observed_connection_revision=replacement.connection_revision,
                    reason=retry_rejected.reason,
                )
        required = _authorization_required_result(
            replacement, observed_connection_revision=authorization.connection_revision
        )
        await self._invalidate_descriptor(server, authorization.descriptor_revision)
        async with self._lock:
            await self._suspend_locked(
                name=server.name, tool_name=None, source_status="needs_auth"
            )
        await self._authorization_required(server, required)
        raise MCPRuntimeFailure(
            "mcp_authorization_required", "MCP server rejected authorization"
        ) from rejected

    async def _suspend_locked(
        self,
        *,
        name: str,
        tool_name: str | None,
        source_status: str,
        descriptor_revision: str | None = None,
    ) -> MCPRouteSnapshot:
        server = self._server(name)
        if tool_name is None:
            replacement = replace(server, disabled=True)
        else:
            replacement = replace(
                server, disabled_tools=server.disabled_tools | frozenset({tool_name})
            )
        servers = tuple(
            replacement if candidate.name == name else candidate
            for candidate in self._configuration.servers
        )
        descriptors = [
            (candidate, self._accepted_descriptors[candidate.name])
            for candidate in servers
            if not candidate.disabled and candidate.name in self._accepted_descriptors
        ]
        sources = tuple(
            replace(
                source,
                status=source_status,
                descriptor_revision=(
                    descriptor_revision
                    if descriptor_revision is not None
                    else source.descriptor_revision
                ),
            )
            if source.name == name and tool_name is None
            else source
            for source in self._snapshot.sources
        )
        candidate = build_route_snapshot(
            catalog_revision=self._configuration.revision,
            resolved=descriptors,
            sources=sources,
            claimed_groups=self._claimed_groups,
            tool_filter=self._configuration.tool_filter,
        )
        await self._accept(candidate)
        self._snapshot = candidate
        if (
            tool_name is None
            and self._lease is not None
            and server.transport == "stdio"
        ):
            await self._lease.close_server(server)
        return candidate

    def _invalidate_memory(self, server: ResolvedMCPServerConfig) -> None:
        for key in tuple(self._memory_cache):
            if key.server_fingerprint == server.authorization.server_fingerprint:
                self._memory_cache.pop(key, None)

    async def _close_removed_stdio_connections(
        self, previous: ResolvedMCPCatalog, configuration: ResolvedMCPCatalog
    ) -> None:
        lease = self._lease
        if lease is None:
            return
        active_by_name = {
            server.name: server
            for server in configuration.servers
            if not server.disabled and server.transport == "stdio"
        }
        for server in previous.servers:
            if server.transport != "stdio":
                continue
            active = active_by_name.get(server.name)
            if active is None:
                await lease.close_server(server)
                continue
            await lease.close_replaced_server(server, active)

    def _server(self, name: str) -> ResolvedMCPServerConfig:
        for server in self._configuration.servers:
            if server.name == name:
                return server
        raise MCPRuntimeFailure("mcp_unknown_server", "MCP server is not configured")

    def _guard_open(self) -> None:
        if self._closed:
            raise MCPRuntimeFailure("mcp_runtime_closed", "MCP Runtime is closed")

    async def _accept(self, snapshot: MCPRouteSnapshot) -> None:
        if self._accept_snapshot is not None:
            await self._accept_snapshot(snapshot)


def _available_status(
    server: ResolvedMCPServerConfig,
) -> Literal["enabled", "connected"]:
    return "connected" if server.authorization.kind == "oauth" else "enabled"


def _cache_key(server: ResolvedMCPServerConfig) -> MCPDescriptorCacheKey:
    return MCPDescriptorCacheKey(
        format_version=1,
        naming_version=NAMING_VERSION,
        server_fingerprint=server.authorization.server_fingerprint,
        authorization_descriptor_revision=server.authorization.descriptor_revision,
    )


def _validate_configuration(configuration: ResolvedMCPCatalog) -> None:
    names: set[str] = set()
    for server in configuration.servers:
        if not server.name or server.name in names:
            raise MCPRuntimeFailure(
                "mcp_invalid_configuration", "MCP server names must be unique"
            )
        names.add(server.name)
        for timeout in (server.startup_timeout_s, server.tool_timeout_s):
            if not math.isfinite(timeout) or timeout <= 0:
                raise MCPRuntimeFailure(
                    "mcp_invalid_configuration",
                    "MCP timeout values must be finite and positive",
                )
        if server.transport == "stdio" and not server.command:
            raise MCPRuntimeFailure(
                "mcp_invalid_configuration", "MCP stdio server requires a command"
            )
        if server.transport != "stdio" and not server.url:
            raise MCPRuntimeFailure(
                "mcp_invalid_configuration", "MCP HTTP server requires a URL"
            )


def _validate_descriptors(
    descriptors: tuple[MCPRemoteToolDescriptor, ...], policy: MCPDescriptorCachePolicy
) -> None:
    if len(descriptors) > policy.max_tools_per_record:
        raise MCPRuntimeFailure(
            "mcp_descriptor_limit", "MCP server returned too many tool descriptors"
        )
    names: set[str] = set()
    for descriptor in descriptors:
        if not descriptor.remote_name or descriptor.remote_name in names:
            raise MCPRuntimeFailure(
                "mcp_invalid_descriptor", "MCP tool names must be non-empty and unique"
            )
        names.add(descriptor.remote_name)


def _authorization_required_result(
    result: MCPAuthorizationSnapshot | MCPAuthorizationRequired,
    *,
    observed_connection_revision: str,
) -> MCPAuthorizationRequired:
    if isinstance(result, MCPAuthorizationRequired):
        return result
    return MCPAuthorizationRequired(
        reason="rejected",
        descriptor_revision=result.descriptor_revision,
        observed_connection_revision=observed_connection_revision,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["MCPRuntime"]
