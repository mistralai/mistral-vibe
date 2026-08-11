from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

from vibe.app_server._model import validate_wire
from vibe.app_server.client import AppServerClient
from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.models import PublicSessionState
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    SessionResumeParams,
    SessionResumeResponse,
)


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    previous: PublicSessionState
    current: PublicSessionState


class AppServerConnection:
    def __init__(
        self,
        client: AppServerClient,
        state: ClientSessionState,
        client_info: ClientInfo,
        capabilities: ClientCapabilities,
        client_factory: Callable[[], AppServerClient] | None = None,
    ) -> None:
        self._client: AppServerClient | None = client
        self._state = state
        self._client_info = client_info
        self._capabilities = capabilities
        self._client_factory = client_factory
        self._initialized = False
        self._attached_session_id: str | None = None
        self._snapshot: ConnectionSnapshot | None = None
        self._lock = asyncio.Lock()

    @property
    def current(self) -> AppServerClient | None:
        return self._client

    async def connect(self) -> AppServerClient:
        async with self._lock:
            client = self._client
            if client is None:
                raise RuntimeError("App-server connection is closed")
            if not self._initialized:
                try:
                    await client.start()
                    await client.initialize(self._client_info, self._capabilities)
                    await client.notify("initialized")
                except Exception:
                    await client.close()
                    self._client = None
                    raise
                self._initialized = True
            if self._attached_session_id != self._state.session_id:
                previous = self._state.projection.state
                session_id = self._state.session_id
                response = validate_wire(
                    SessionResumeResponse,
                    await client.request(
                        "session/resume", SessionResumeParams(session_id=session_id)
                    ),
                )
                current = response.state
                self._state.projection.replace_state(current)
                self._snapshot = ConnectionSnapshot(previous, current)
                self._attached_session_id = self._state.session_id
            return client

    def mark_session_attached(self) -> None:
        self._attached_session_id = self._state.session_id

    def adopt_initialized_session(self, session_id: str) -> None:
        self._initialized = True
        self._attached_session_id = session_id

    def take_snapshot(self) -> ConnectionSnapshot | None:
        snapshot = self._snapshot
        self._snapshot = None
        return snapshot

    async def reconnect(self, failed_client: AppServerClient) -> bool:
        async with self._lock:
            if self._client is not failed_client:
                return self._client is not None
            with suppress(Exception):
                await failed_client.close()
            if self._client_factory is None:
                self._client = None
                return False
            self._client = self._client_factory()
            self._initialized = False
            self._attached_session_id = None
            return True

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.close()
        self._client = None


class AppServerResourceConnection:
    def __init__(
        self,
        connection: AppServerConnection,
        connect_session: Callable[[], Awaitable[AppServerClient]],
    ) -> None:
        self._connection = connection
        self._connect_session = connect_session

    async def connect(self) -> AppServerClient:
        return await self._connect_session()

    def mark_session_attached(self) -> None:
        self._connection.mark_session_attached()
