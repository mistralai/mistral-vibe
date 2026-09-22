"""Host-scoped MCP stdio pool without MCP SDK imports."""

import asyncio
from dataclasses import dataclass
import hashlib

from mistralai_vibe_local_harness.vibe._mcp_models import (
    JsonObject,
    MCPClientConnection,
    MCPNormalizedResult,
    MCPRemoteToolDescriptor,
    MCPSamplingCallback,
    MCPSamplingCompletion,
    MCPTransportDisconnected,
    MCPTransportFactory,
    ResolvedMCPServerConfig,
)


@dataclass(slots=True)
class _Request:
    kind: str
    name: str | None
    arguments: JsonObject | None
    timeout_s: float
    future: asyncio.Future[object]


class _PooledConnection:
    def __init__(
        self,
        factory: MCPTransportFactory,
        server: ResolvedMCPServerConfig,
        sampling_callback: MCPSamplingCallback | None,
    ) -> None:
        self._factory = factory
        self._server = server
        self._sampling_callback = sampling_callback
        self._requests: asyncio.Queue[_Request | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._connection: MCPClientConnection | None = None
        self._inflight: _Request | None = None

    async def list_tools(self) -> tuple[MCPRemoteToolDescriptor, ...]:
        result = await self._request("list", None, None, self._server.startup_timeout_s)
        if not isinstance(result, tuple):
            raise TypeError("MCP list request returned an invalid result")
        return result

    async def call_tool(self, name: str, arguments: JsonObject) -> MCPNormalizedResult:
        result = await self._request("call", name, arguments, self._server.tool_timeout_s)
        if not isinstance(result, MCPNormalizedResult):
            raise TypeError("MCP call request returned an invalid result")
        return result

    async def _request(
        self,
        kind: str,
        name: str | None,
        arguments: JsonObject | None,
        timeout_s: float,
    ) -> object:
        self._ensure_worker()
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        await self._requests.put(_Request(kind, name, arguments, timeout_s, future))
        return await future

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name=f"mcp-stdio-{self._server.name}")

    async def _run(self) -> None:
        try:
            while True:
                request = await self._requests.get()
                if request is None:
                    return
                self._inflight = request
                try:
                    result = await self._execute_with_reconnect(request)
                except BaseException as exc:
                    if not request.future.done():
                        request.future.set_exception(exc)
                else:
                    if not request.future.done():
                        request.future.set_result(result)
                finally:
                    self._inflight = None
        finally:
            await self._close_connection()
            self._fail_pending()

    async def _execute_with_reconnect(self, request: _Request) -> object:
        try:
            return await self._execute(request)
        except MCPTransportDisconnected:
            await self._close_connection()
            return await self._execute(request)

    async def _execute(self, request: _Request) -> object:
        connection = await self._ensure_connection()
        if request.kind == "list":
            return await connection.list_tools()
        if request.name is None or request.arguments is None:
            raise TypeError("MCP call request is incomplete")
        return await connection.call_tool(
            request.name, request.arguments, timeout_s=request.timeout_s
        )

    async def _ensure_connection(self) -> MCPClientConnection:
        if self._connection is None:
            self._connection = await self._factory.open_stdio(
                self._server, sampling_callback=self._sampling_callback
            )
        return self._connection

    async def _close_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.aclose()

    def _fail_pending(self) -> None:
        error = MCPTransportDisconnected("MCP stdio connection closed")
        if self._inflight is not None and not self._inflight.future.done():
            self._inflight.future.set_exception(error)
        while not self._requests.empty():
            request = self._requests.get_nowait()
            if request is not None and not request.future.done():
                request.future.set_exception(error)

    async def aclose(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:
            return
        if worker.done():
            await worker
            return
        await self._requests.put(None)
        try:
            await asyncio.wait_for(worker, timeout=5.0)
        except TimeoutError:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass


class MCPStdioPool:
    """One process per server identity, shared by every Session in a Host.

    Callers hold a :class:`MCPStdioPoolLease` rather than the pool itself. A process
    outlives the lease that opened it and is torn down when the last lease releases it,
    so one Session disabling a server cannot take the process out from under another.
    """

    def __init__(
        self,
        factory: MCPTransportFactory | None = None,
        *,
        sampling_completion: MCPSamplingCompletion | None = None,
    ) -> None:
        self._factory = factory
        self._sampling_completion = sampling_completion
        self._connections: dict[str, _PooledConnection] = {}
        self._holders: dict[str, set["MCPStdioPoolLease"]] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def lease(self) -> "MCPStdioPoolLease":
        return MCPStdioPoolLease(self)

    async def acquire(
        self, lease: "MCPStdioPoolLease", server: ResolvedMCPServerConfig
    ) -> _PooledConnection:
        key = _stdio_key(server)
        async with self._lock:
            # Checked under the lock: a spawn racing ``aclose`` would outlive the pool
            # that is supposed to reap it.
            if self._closed:
                raise MCPTransportDisconnected("MCP stdio pool is closed")
            connection = self._connections.get(key)
            if connection is None:
                connection = _PooledConnection(
                    self._resolve_factory(), server, self._sampling_callback(server)
                )
                self._connections[key] = connection
            self._holders.setdefault(key, set()).add(lease)
            return connection

    async def release(self, lease: "MCPStdioPoolLease", server: ResolvedMCPServerConfig) -> None:
        async with self._lock:
            connection = self._release_locked(lease, _stdio_key(server))
        if connection is not None:
            await connection.aclose()

    async def release_all(self, lease: "MCPStdioPoolLease") -> None:
        async with self._lock:
            released = [
                connection
                for key in tuple(self._holders)
                if (connection := self._release_locked(lease, key)) is not None
            ]
        await _close_all(released, "Failed to release MCP stdio connections")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            self._holders.clear()
        await _close_all(connections, "Failed to close MCP stdio pool")

    def _release_locked(self, lease: "MCPStdioPoolLease", key: str) -> _PooledConnection | None:
        holders = self._holders.get(key)
        if holders is None:
            return None
        holders.discard(lease)
        if holders:
            return None
        del self._holders[key]
        return self._connections.pop(key, None)

    def _resolve_factory(self) -> MCPTransportFactory:
        if self._factory is None:
            from mistralai_vibe_local_harness.vibe._mcp_transport import (
                UnifiedMCPTransportFactory,
            )

            self._factory = UnifiedMCPTransportFactory()
        return self._factory

    def _sampling_callback(self, server: ResolvedMCPServerConfig) -> MCPSamplingCallback | None:
        # Derived here rather than accepted per call: a connection outlives the request
        # that opened it, so a caller-supplied callback would answer sampling requests
        # raised by another Session's tool call. Deriving it from Host configuration
        # keeps every lease's callback identical, which is what makes sharing sound --
        # wiring a Session-scoped ``sampling_completion`` would break that.
        if not server.sampling_enabled or self._sampling_completion is None:
            return None
        from mistralai_vibe_local_harness.vibe._mcp_sampling import build_sampling_callback

        return build_sampling_callback(self._sampling_completion)


class MCPStdioPoolLease:
    """One MCP Runtime's hold on the Host's stdio processes."""

    def __init__(self, pool: MCPStdioPool) -> None:
        self._pool = pool

    async def list_tools(
        self,
        server: ResolvedMCPServerConfig,
        *,
        persistent: bool = False,
    ) -> tuple[MCPRemoteToolDescriptor, ...]:
        connection = await self._pool.acquire(self, server)
        try:
            return await connection.list_tools()
        finally:
            if not persistent:
                await self.close_server(server)

    async def call_tool(
        self,
        server: ResolvedMCPServerConfig,
        *,
        name: str,
        arguments: JsonObject,
    ) -> MCPNormalizedResult:
        connection = await self._pool.acquire(self, server)
        return await connection.call_tool(name, arguments)

    async def close_server(self, server: ResolvedMCPServerConfig) -> None:
        await self._pool.release(self, server)

    async def close_replaced_server(
        self,
        previous: ResolvedMCPServerConfig,
        replacement: ResolvedMCPServerConfig,
    ) -> None:
        """Release a process when the pool's complete connection identity changes."""
        if _stdio_key(previous) == _stdio_key(replacement):
            return
        await self.close_server(previous)

    async def aclose(self) -> None:
        await self._pool.release_all(self)


async def _close_all(connections: list[_PooledConnection], message: str) -> None:
    if not connections:
        return
    results = await asyncio.gather(
        *(connection.aclose() for connection in connections),
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, BaseException)]
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(message, errors)


def _stdio_key(server: ResolvedMCPServerConfig) -> str:
    raw = "\0".join(
        (
            server.authorization.server_fingerprint,
            server.command or "",
            *server.args,
            "\x01",
            *(f"{key}={value}" for key, value in sorted(server.env.items())),
            "\x01",
            str(server.cwd or ""),
            # A connection is opened with a sampling callback or without one, so the flag
            # that decides it belongs to the identity two leases have to agree on.
            f"sampling={server.sampling_enabled}",
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["MCPStdioPool", "MCPStdioPoolLease"]
