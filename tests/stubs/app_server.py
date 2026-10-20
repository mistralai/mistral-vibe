from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from vibe.app_server._account import AccountGateway
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._model import ProtocolModel
from vibe.app_server._projector import EventProjector
from vibe.app_server._session_spawn import SessionSpawnError
from vibe.app_server.events import AppServerEvent, ClientProjection
from vibe.app_server.models import (
    IdleSessionStatus,
    PublicCallbackEntry,
    PublicHistoryEntry,
    PublicSession,
    PublicSessionState,
)
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    Notification,
    SessionSpawnParams,
    SessionSpawnResponse,
)
from vibe.core.types import BaseEvent


class FakeSessionBackendServices:
    """``SessionBackendServices`` that records notifications instead of sending
    them, so a test can assert on what a backend pushed without a transport.
    """

    def __init__(self) -> None:
        self.notifications: list[tuple[str, ProtocolModel]] = []

    def client_info(self) -> ClientInfo:
        return ClientInfo(name="test", version="1")

    def client_capabilities(self) -> ClientCapabilities:
        return ClientCapabilities()

    def current_session_id(self) -> str:
        return "root"

    def account_gateway(self) -> AccountGateway | None:
        return None

    def identity_gateway(self) -> IdentityGateway | None:
        return None

    def task_finished(self, task: asyncio.Task[None]) -> None:
        return None

    async def notify(self, method: str, params: ProtocolModel) -> None:
        self.notifications.append((method, params))

    async def publish_callback(self, callback: PublicCallbackEntry) -> None:
        return None

    async def spawn_session(self, params: SessionSpawnParams) -> SessionSpawnResponse:
        raise SessionSpawnError("This client cannot start sessions")


class CoreEventProjection:
    def __init__(self) -> None:
        session_id = "session-1"
        self._projector = EventProjector(session_id, "turn-1")
        self._projection = ClientProjection(
            PublicSessionState(
                event_id=0,
                session=PublicSession(
                    id=session_id,
                    status=IdleSessionStatus(),
                    created_at=1,
                    updated_at=1,
                ),
                history=[],
                active_callbacks=[],
                turns=[],
            )
        )
        self._event_id = 0

    def project(self, event: BaseEvent) -> list[AppServerEvent]:
        projected: list[AppServerEvent] = []
        for update in self._projector.project(event):
            self._event_id += 1
            params = update.params.model_copy(update={"event_id": self._event_id})
            notification = Notification(
                method=update.method,
                params=params.model_dump(mode="json", by_alias=True),
            )
            if client_event := self._projection.consume(notification):
                projected.append(client_event)
        return projected

    @property
    def history(self) -> list[PublicHistoryEntry]:
        return self._projection.history

    async def dispatch[ResultT](
        self, event: BaseEvent, consumer: Callable[[AppServerEvent], Awaitable[ResultT]]
    ) -> list[ResultT]:
        return [await consumer(projected) for projected in self.project(event)]
