"""What a session reaches its client through: the channel attached to it now.

The session's runtime holds this link, never the channel itself: its
callbacks, notifications, client tools, spawn and peer requests and lent
credentials all go through it, so the link alone says which client answers.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import JsonValue

    from vibe.app_server._account import AccountGateway
    from vibe.app_server._identity import IdentityGateway
    from vibe.app_server._model import ProtocolModel
    from vibe.app_server._platform_credential_lenders import LenderAvailability
    from vibe.app_server.models import PublicCallbackEntry
    from vibe.app_server.protocol import (
        ClientCapabilities,
        ClientInfo,
        SessionPeerArchiveParams,
        SessionPeerArchiveResponse,
        SessionPeerListParams,
        SessionPeerListResponse,
        SessionPeerMarkSeenParams,
        SessionPeerMarkSeenResponse,
        SessionPeerReadParams,
        SessionPeerReadResponse,
        SessionPeerRenameParams,
        SessionPeerRenameResponse,
        SessionPeerRestoreParams,
        SessionPeerRestoreResponse,
        SessionPeerSendParams,
        SessionPeerSendResponse,
        SessionSpawnParams,
        SessionSpawnResponse,
    )
    from vibe.app_server.server import AppServer
    from vibe.core.platform_auth import PlatformCredentialProvider


class SessionLink:
    def __init__(self, server: AppServer) -> None:
        self.server = server

    def client_info(self) -> ClientInfo:
        return self.server.client_info()

    def client_capabilities(self) -> ClientCapabilities:
        return self.server.client_capabilities()

    def client_owner(self) -> JsonValue:
        return self.server.client_owner()

    def current_session_id(self) -> str:
        return self.server.current_session_id()

    def account_gateway(self) -> AccountGateway | None:
        return self.server.account_gateway()

    def identity_gateway(self) -> IdentityGateway | None:
        return self.server.identity_gateway()

    def platform_credentials(self, api_key_env_var: str) -> PlatformCredentialProvider:
        return self.server.platform_credentials(api_key_env_var)

    def lender_availability(self) -> LenderAvailability:
        return self.server.lender_availability()

    def task_finished(self, task: asyncio.Task[None]) -> None:
        self.server.task_finished(task)

    async def notify(self, method: str, params: ProtocolModel) -> None:
        await self.server.notify(method, params)

    async def publish_callback(self, callback: PublicCallbackEntry) -> None:
        await self.server.publish_callback(callback)

    async def spawn_session(self, params: SessionSpawnParams) -> SessionSpawnResponse:
        return await self.server.spawn_session(params)

    async def list_peer_sessions(
        self, params: SessionPeerListParams
    ) -> SessionPeerListResponse:
        return await self.server.list_peer_sessions(params)

    async def read_peer_session(
        self, params: SessionPeerReadParams
    ) -> SessionPeerReadResponse:
        return await self.server.read_peer_session(params)

    async def send_to_peer_session(
        self, params: SessionPeerSendParams
    ) -> SessionPeerSendResponse:
        return await self.server.send_to_peer_session(params)

    async def archive_peer_sessions(
        self, params: SessionPeerArchiveParams
    ) -> SessionPeerArchiveResponse:
        return await self.server.archive_peer_sessions(params)

    async def restore_peer_sessions(
        self, params: SessionPeerRestoreParams
    ) -> SessionPeerRestoreResponse:
        return await self.server.restore_peer_sessions(params)

    async def rename_peer_session(
        self, params: SessionPeerRenameParams
    ) -> SessionPeerRenameResponse:
        return await self.server.rename_peer_session(params)

    async def mark_peer_sessions_seen(
        self, params: SessionPeerMarkSeenParams
    ) -> SessionPeerMarkSeenResponse:
        return await self.server.mark_peer_sessions_seen(params)


__all__ = ["SessionLink"]
