"""Unified Runtime ownership of MCP descriptors, routes, calls, and cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping
import contextlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import inspect
import logging
import math
from pathlib import Path
from typing import Literal

from mistralai_vibe_local_harness.vibe._mcp_cache import MCPDescriptorCache
from mistralai_vibe_local_harness.vibe._mcp_models import (
    MCP_DISCOVERY_PENDING,
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


type _Discovery = Callable[
    [], Coroutine[object, object, tuple[MCPRemoteToolDescriptor, ...]]
]
# The server, and the credential the discovery runs with: a discovery is only
# shared by runtimes that would send the same one.
type _FlightKey = tuple[MCPDescriptorCacheKey, str]


@dataclass(eq=False)
class _Flight:
    task: asyncio.Task[tuple[MCPRemoteToolDescriptor, ...]]
    # Whether the runtime whose connections the discovery uses has closed;
    # always false for one that uses none of its own.
    closed: Callable[[], bool]
    waiters: int = 0


def _never_closed() -> bool:
    return False


class MCPDiscoveryFlights:
    """Discoveries in progress, shared by the runtimes given the same instance.

    A runtime that would contact a server another one is already discovering,
    with the same descriptor cache key, joins that discovery instead: sessions
    opened together contact each server once. Its outcome, a failure included,
    goes to every runtime that joined, unless it ended for a reason of the
    starting runtime's own: that runtime closed and the discovery ran over its
    connections (stdio), or the discovery was cancelled. A runtime still
    waiting then discovers again on its own.
    A runtime that stops waiting leaves the others waiting; the discovery is
    cancelled once no runtime waits for it.

    The key is the descriptor cache key and the credential sent: runtimes
    that would authenticate differently, one with a token another has just
    refreshed say, never share a result, so a rejection of one credential is
    never taken for a rejection of the other.
    """

    def __init__(self) -> None:
        self._flights: dict[_FlightKey, _Flight] = {}

    async def join(
        self, key: _FlightKey, discover: _Discovery, *, closed: Callable[[], bool]
    ) -> tuple[MCPRemoteToolDescriptor, ...]:
        while True:
            flight = self._flights.get(key)
            # A flight of another event loop, one a test closed say, is never
            # joined: its task cannot be awaited from this one.
            joined = flight is not None and not (
                flight.closed()
                or flight.task.cancelled()
                or flight.task.get_loop() is not asyncio.get_running_loop()
            )
            if flight is None or not joined:
                flight = self._start(key, discover, closed)
            flight.waiters += 1
            try:
                return await asyncio.shield(flight.task)
            except (Exception, asyncio.CancelledError):
                if not (joined and self._lost_for_its_starter(flight, closed)):
                    raise
            finally:
                flight.waiters -= 1
                if not flight.waiters and not flight.task.done():
                    flight.task.cancel()
                    self._forget(key, flight.task)

    def _start(
        self, key: _FlightKey, discover: _Discovery, closed: Callable[[], bool]
    ) -> _Flight:
        task = asyncio.create_task(discover())
        flight = _Flight(task=task, closed=closed)
        self._flights[key] = flight
        task.add_done_callback(lambda done: self._forget(key, done))
        return flight

    @staticmethod
    def _lost_for_its_starter(flight: _Flight, closed: Callable[[], bool]) -> bool:
        """Whether a joined discovery ended for its starting runtime's reasons.

        Its runtime closing under it, or its cancellation, which the joiner
        did not ask for. A failure of the server's own is shared as is.
        """
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            return False
        if flight.task.cancelled():
            return True
        return flight.closed != closed and flight.closed()

    def _forget(
        self, key: _FlightKey, task: asyncio.Future[tuple[MCPRemoteToolDescriptor, ...]]
    ) -> None:
        flight = self._flights.get(key)
        if flight is not None and flight.task is task:
            del self._flights[key]
        # Retrieved here so a discovery nobody waits for logs nothing.
        if task.done() and not task.cancelled():
            task.exception()


class MCPRuntime:
    def __init__(  # noqa: PLR0913 - explicit runtime dependencies
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
        discovery_flights: MCPDiscoveryFlights | None = None,
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
        self._discovery_flights = discovery_flights
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
        self._snapshot_deferred = False
        # Servers a cached-only reconfigure routed from descriptors older than
        # the policy's ``refresh_after_s``: ``discover_pending`` refreshes them.
        self._refresh_due: set[str] = set()
        # Servers a ``discover_pending`` is contacting, by name, each set once
        # its result is accepted or dropped. A call to one waits for it.
        self._discovering: dict[str, asyncio.Event] = {}

    @property
    def snapshot(self) -> MCPRouteSnapshot:
        return self._snapshot

    @property
    def discovery_due(self) -> bool:
        """Whether ``discover_pending`` has a server to discover or refresh."""
        return bool(self._refresh_due) or any(
            _is_discovery_pending(source) for source in self._snapshot.sources
        )

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
        cached_only: bool = False,
    ) -> MCPRouteSnapshot:
        """Resolve the catalog and store the resulting route snapshot.

        With ``push_to_core`` false the snapshot is stored but not handed to the
        capability acceptor, so the routes become dispatchable without a Core
        capability reconfigure. This lets a caller ready the routes while Core is
        mid-turn (which rejects reconfigure) and publish them once Core is idle
        via ``push_snapshot_to_core``.

        With ``cached_only`` no server is contacted and no authorization is
        resolved: one without fresh cached descriptors is reported unavailable
        with ``mcp_discovery_pending``. A session can then open without waiting
        on the network and discover those servers with ``discover_pending``.
        """
        if cached_only and force_remote_discovery:
            raise ValueError("cached_only cannot force remote discovery")
        forced_names = (
            frozenset(server.name for server in configuration.servers)
            if force_remote_discovery
            else frozenset()
        )
        async with self._lock:
            self._guard_open()
            return await self._reconfigure(
                configuration,
                forced_names=forced_names,
                push_to_core=push_to_core,
                cached_only=cached_only,
            )

    async def discover_pending(
        self, *, push_to_core: bool, refresh: bool = True
    ) -> MCPRouteSnapshot:
        """Discover the servers a cached-only reconfigure left pending.

        Also rediscovers the servers it routed from descriptors older than the
        policy's ``refresh_after_s``, so a server that changed its tools is
        caught up without every open contacting every server. With ``refresh``
        false those are left for a later call, so waiting for this one waits
        only for the servers with no tools yet.

        The servers are contacted without holding the runtime lock, so a slow or
        unreachable one never holds up a login, a toggle or a reload meanwhile.
        A call to one of them waits for its own discovery instead (see
        ``execute``). A result is kept only if its server is still configured
        the same way and still pending or due a refresh; anything that changed
        it in between has the last word.
        """
        async with self._lock:
            self._guard_open()
            due = tuple(
                server
                for server in self._configuration.servers
                if self._discovery_due_for(server.name, refresh=refresh)
            )
            refreshing = frozenset(
                server.name for server in due if server.name in self._refresh_due
            )
            # A server another call is already discovering is waited for, not
            # contacted again: two accepts would race to replace its routes.
            in_flight = [
                self._discovering[server.name]
                for server in due
                if server.name in self._discovering
            ]
            due = tuple(
                server for server in due if server.name not in self._discovering
            )
            started = {server.name: asyncio.Event() for server in due}
            self._discovering.update(started)
        semaphore = asyncio.Semaphore(self._discovery_concurrency)
        # Each server's result is accepted as it arrives, so a call to it waits
        # for its own discovery, not for the slowest server's. One failing to
        # be accepted leaves the others accepted, and the caller still gets
        # the snapshot that holds them.
        try:
            outcomes = await asyncio.gather(
                *(
                    self._discover_one(
                        server,
                        semaphore,
                        refresh=server.name in refreshing,
                        started=started[server.name],
                        push_to_core=push_to_core,
                    )
                    for server in due
                ),
                *(event.wait() for event in in_flight),
                return_exceptions=True,
            )
        finally:
            # However this ends, a cancellation included, no waiter is left on
            # a discovery that will never finish.
            self._release_discoveries(started)
        for outcome in outcomes:
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
        outcomes = outcomes[: len(due)]
        self._guard_open()
        if not outcomes:
            return self._snapshot
        failures = [
            outcome for outcome in outcomes if isinstance(outcome, BaseException)
        ]
        # As before per-server acceptance: nothing accepted is the caller's
        # failure to handle, not a quiet return.
        if failures and len(failures) == len(outcomes):
            raise failures[0]
        for failure in failures:
            logger.warning("MCP discovery could not be accepted", exc_info=failure)
        return self._snapshot

    async def _discover_one(
        self,
        server: ResolvedMCPServerConfig,
        semaphore: asyncio.Semaphore,
        *,
        refresh: bool,
        started: asyncio.Event,
        push_to_core: bool,
    ) -> None:
        try:
            resolution = await self._resolve_isolated(
                server,
                semaphore,
                # A refresh skips the cache it is refreshing.
                forced=refresh,
                cached_only=False,
            )
            async with self._lock:
                self._guard_open()
                due = server.name in self._refresh_due or _is_discovery_pending(
                    self._source_state(server.name)
                )
                if not due or server not in self._configuration.servers:
                    return
                # A refresh that fails keeps the cached tools: losing them for
                # the session would cost more than a call failing on its own.
                if refresh and resolution[1] is None:
                    self._refresh_due.discard(server.name)
                    return
                configuration = self._configuration
                await self._accept_resolutions(
                    configuration,
                    [
                        resolution
                        if candidate.name == server.name
                        else self._routed_resolution(candidate)
                        for candidate in configuration.servers
                    ],
                    previous_configuration=configuration,
                    push_to_core=push_to_core,
                )
                self._refresh_due.discard(server.name)
        finally:
            self._release_discoveries({server.name: started})

    def _release_discoveries(self, started: Mapping[str, asyncio.Event]) -> None:
        for name, event in started.items():
            if self._discovering.get(name) is event:
                del self._discovering[name]
            event.set()

    async def _wait_for_discovery(self, name: str) -> None:
        """Hold a call to a server ``discover_pending`` is contacting.

        Bounded by the server's startup timeout, the bound its discovery
        already has. Calls to other servers do not wait.
        """
        event = self._discovering.get(name)
        if event is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                event.wait(), timeout=self._server(name).startup_timeout_s
            )

    def _discovery_due_for(self, name: str, *, refresh: bool = True) -> bool:
        return (refresh and name in self._refresh_due) or _is_discovery_pending(
            self._source_state(name)
        )

    async def push_snapshot_to_core(self) -> None:
        """Hand the current route snapshot to the capability acceptor.

        Pairs with a ``reconfigure(push_to_core=False)`` to publish the readied
        routes to Core once it is idle.
        """
        async with self._lock:
            self._guard_open()
            await self._accept(self._snapshot)
            self._snapshot_deferred = False

    async def publish_deferred_snapshot(self) -> None:
        """Hand Core the routes an MCP call withdrew while Core was running.

        An authorization failure during a call arrives while its provided-tool
        Action is still pending, and Core rejects a capability reconfigure until
        no Action is. The withdrawn routes apply to dispatch at once; call this
        once Core is idle so the advertised capabilities catch up.
        """
        async with self._lock:
            if self._closed or not self._snapshot_deferred:
                return
            await self._accept(self._snapshot)
            self._snapshot_deferred = False

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
        route = self._snapshot.routes.get((group_name, tool_name))
        if route is not None:
            await self._wait_for_discovery(route.descriptor.server_name)
            self._guard_open()
            # Read again: the discovery may have replaced the route.
            route = self._snapshot.routes.get((group_name, tool_name))
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
                # Core is running this call's Action and rejects a capability
                # reconfigure; ``publish_deferred_snapshot`` hands Core the
                # withdrawn routes once it is idle.
                await self._suspend_locked(
                    name=server.name,
                    tool_name=None,
                    source_status="needs_auth",
                    descriptor_revision=authorization.descriptor_revision,
                    push_to_core=False,
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
        cached_only: bool = False,
    ) -> MCPRouteSnapshot:
        _validate_configuration(configuration)
        previous_configuration = self._configuration
        # This reconfigure decides afresh which servers are due a refresh.
        self._refresh_due.clear()
        semaphore = asyncio.Semaphore(self._discovery_concurrency)
        results = await asyncio.gather(
            *(
                self._resolve_isolated(
                    server,
                    semaphore,
                    forced=server.name in forced_names,
                    cached_only=cached_only,
                )
                for server in configuration.servers
            )
        )
        return await self._accept_resolutions(
            configuration,
            results,
            previous_configuration=previous_configuration,
            push_to_core=push_to_core,
        )

    async def _resolve_isolated(
        self,
        server: ResolvedMCPServerConfig,
        semaphore: asyncio.Semaphore,
        *,
        forced: bool,
        cached_only: bool,
    ) -> _ServerResolution:
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
                    server, force_remote_discovery=forced, cached_only=cached_only
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

    def _current_resolution(self, server: ResolvedMCPServerConfig) -> _ServerResolution:
        descriptors = self._accepted_descriptors.get(server.name)
        return (
            server,
            descriptors,
            self._source_state(server.name)
            or MCPSourceState(
                name=server.name,
                status="disabled" if server.disabled else "unavailable",
                descriptors=descriptors or (),
                descriptor_revision=server.authorization.descriptor_revision,
            ),
            None,
        )

    def _routed_resolution(self, server: ResolvedMCPServerConfig) -> _ServerResolution:
        """A server as the current snapshot routes it, suspensions included.

        A suspension changes the snapshot alone, so a rebuild from the
        configuration would route again what it took away.
        """
        _, descriptors, source, _ = self._current_resolution(server)
        routed = {
            route.descriptor.remote_name
            for route in self._snapshot.routes.values()
            if route.descriptor.server_name == server.name
        }
        if descriptors is None:
            return server, None, source, None
        if not routed:
            return replace(server, disabled=True), descriptors, source, None
        unrouted = frozenset(
            descriptor.remote_name
            for descriptor in descriptors
            if descriptor.remote_name not in routed
        )
        return (
            replace(server, disabled_tools=server.disabled_tools | unrouted),
            descriptors,
            source,
            None,
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
            resolutions.append(self._current_resolution(server))
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
        # Set with the snapshot, not after the awaits below: one failing or
        # cancelled must not leave routes Core has not seen unflagged.
        self._snapshot_deferred = not push_to_core
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
        cached_only: bool = False,
    ) -> _ServerResolution:
        if cached_only:
            return await self._resolve_from_cache(server)
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
            descriptors = await self._discover_once(server, authorization, key)
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

    async def _resolve_from_cache(
        self, server: ResolvedMCPServerConfig
    ) -> _ServerResolution:
        """Route a server from its cached descriptors, contacting nothing.

        Authorization is not resolved either: refreshing a token is a network
        call. A call through these routes resolves it, as every call does.
        """
        revision = server.authorization.descriptor_revision
        cached = await self._read_descriptor(_cache_key(server), server.name)
        if cached is None:
            return (
                server,
                None,
                MCPSourceState(
                    name=server.name,
                    status="unavailable",
                    descriptor_revision=revision,
                    error=MCP_DISCOVERY_PENDING,
                ),
                None,
            )
        refresh_after_s = self._cache_policy.refresh_after_s
        if (
            refresh_after_s is not None
            and (_utc_now() - cached.discovered_at).total_seconds() >= refresh_after_s
        ):
            self._refresh_due.add(server.name)
        return (
            server,
            cached.descriptors,
            MCPSourceState(
                name=server.name,
                status=_available_status(server),
                descriptors=cached.descriptors,
                descriptor_revision=revision,
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

    async def _discover_once(
        self,
        server: ResolvedMCPServerConfig,
        authorization: MCPAuthorizationSnapshot,
        key: MCPDescriptorCacheKey,
    ) -> tuple[MCPRemoteToolDescriptor, ...]:
        flights = self._discovery_flights
        if flights is None:
            return await self._discover(server, authorization)
        return await flights.join(
            (key, _credential_fingerprint(authorization)),
            lambda: self._discover(server, authorization),
            closed=self._is_closed if server.transport == "stdio" else _never_closed,
        )

    def _is_closed(self) -> bool:
        return self._closed

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
                name=server.name,
                tool_name=None,
                source_status="needs_auth",
                push_to_core=False,
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
        push_to_core: bool = True,
    ) -> MCPRouteSnapshot:
        server = self._server(name)
        # A refresh in flight must not route back what this takes away.
        self._refresh_due.discard(name)
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
        if push_to_core:
            await self._accept(candidate)
        self._snapshot = candidate
        self._snapshot_deferred = not push_to_core
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


def _is_discovery_pending(source: MCPSourceState | None) -> bool:
    # The status as well: a suspension keeps the source's error.
    return (
        source is not None
        and source.status == "unavailable"
        and source.error == MCP_DISCOVERY_PENDING
    )


def _available_status(
    server: ResolvedMCPServerConfig,
) -> Literal["enabled", "connected"]:
    return "connected" if server.authorization.kind == "oauth" else "enabled"


def _credential_fingerprint(authorization: MCPAuthorizationSnapshot) -> str:
    """A digest of the headers a discovery sends, so none is kept in clear."""
    digest = hashlib.sha256()
    for name, value in sorted(authorization.headers.items()):
        digest.update(name.lower().encode())
        digest.update(b"\0")
        digest.update(value.encode())
        digest.update(b"\0")
    return digest.hexdigest()


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
