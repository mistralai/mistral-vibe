from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from vibe.app_server._account import AccountGateway
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._model import ProtocolModel
from vibe.app_server.models import PublicCallbackEntry
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    SessionPeerArchiveParams,
    SessionPeerArchiveResponse,
    SessionPeerListParams,
    SessionPeerListResponse,
    SessionPeerReadParams,
    SessionPeerReadResponse,
    SessionPeerSendParams,
    SessionPeerSendResponse,
    SessionSpawnParams,
    SessionSpawnResponse,
)
from vibe.core.platform_auth import PlatformCredentialProvider


class SessionBackendServices(Protocol):
    def client_info(self) -> ClientInfo: ...

    def client_capabilities(self) -> ClientCapabilities: ...

    def current_session_id(self) -> str: ...

    def account_gateway(self) -> AccountGateway | None: ...

    def identity_gateway(self) -> IdentityGateway | None: ...

    def task_finished(self, task: asyncio.Task[None]) -> None: ...

    async def notify(self, method: str, params: ProtocolModel) -> None: ...

    async def publish_callback(self, callback: PublicCallbackEntry) -> None: ...

    async def spawn_session(
        self, params: SessionSpawnParams
    ) -> SessionSpawnResponse: ...

    async def list_peer_sessions(
        self, params: SessionPeerListParams
    ) -> SessionPeerListResponse: ...

    async def read_peer_session(
        self, params: SessionPeerReadParams
    ) -> SessionPeerReadResponse: ...

    async def send_to_peer_session(
        self, params: SessionPeerSendParams
    ) -> SessionPeerSendResponse: ...

    async def archive_peer_sessions(
        self, params: SessionPeerArchiveParams
    ) -> SessionPeerArchiveResponse: ...


@runtime_checkable
class SessionBackendPlatformCredentials(Protocol):
    def platform_credentials(
        self, api_key_env_var: str
    ) -> PlatformCredentialProvider: ...
