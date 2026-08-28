from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from vibe.app_server._account import AccountGateway
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._legacy_composition import create_legacy_app_server
from vibe.app_server._legacy_session_backend import LegacySessionBackend
from vibe.app_server._projector import EventProjector
from vibe.app_server._runtime import AgentRuntimeFactory, RootOpenRequest
from vibe.app_server.client import AppServerClient
from vibe.app_server.connector_catalog import ConnectorCatalogService
from vibe.app_server.events import AppServerEvent, ClientProjection
from vibe.app_server.models import (
    IdleSessionStatus,
    PublicHistoryEntry,
    PublicSession,
    PublicSessionState,
)
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    Notification,
    SessionOptions,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair
from vibe.core.agent_loop import AgentLoop
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import BaseEvent, ToolCallEvent, ToolResultEvent


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
        match event:
            case ToolCallEvent(presentation=None):
                event = event.model_copy(
                    update={
                        "presentation": ToolUIDataAdapter(
                            event.tool_class
                        ).get_call_presentation(event)
                    }
                )
            case ToolResultEvent(presentation=None):
                event = event.model_copy(
                    update={
                        "presentation": ToolUIDataAdapter(
                            event.tool_class
                        ).get_result_presentation(event)
                    }
                )
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


def start_test_app_server(
    agent_loop: AgentLoop,
    *,
    account_gateway: AccountGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
    connector_catalog_service: ConnectorCatalogService | None = None,
) -> AppServerClient:
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(
        agent_loop,
        server_transport,
        account_gateway=account_gateway,
        identity_gateway=identity_gateway,
        connector_catalog_service=connector_catalog_service,
    )
    return AppServerClient(client_transport, run_peer=server.serve)


def build_test_app_server(
    agent_loop: AgentLoop,
    transport: JsonRpcTransport,
    *,
    account_gateway: AccountGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
    connector_catalog_service: ConnectorCatalogService | None = None,
) -> AppServer:
    runtime_factory = AgentRuntimeFactory()

    async def open_root(request: RootOpenRequest) -> AgentLoop:
        session_id = request.session_id
        if request.continue_latest:
            session_id = runtime_factory.resolve_latest(
                agent_loop, Path(request.options.cwd or agent_loop.cwd)
            )
        if session_id is not None:
            await runtime_factory.resume_root(agent_loop, session_id)
        return agent_loop

    return create_legacy_app_server(
        transport,
        open_root=open_root,
        runtime_factory=runtime_factory,
        account_gateway=account_gateway,
        identity_gateway=identity_gateway,
        connector_catalog_service=connector_catalog_service,
    )


def legacy_backend(server: AppServer) -> LegacySessionBackend:
    root = server._root
    assert isinstance(root, LegacySessionBackend)
    return root


async def create_test_app_server_session(
    agent_loop: AgentLoop,
    *,
    account_gateway: AccountGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
    connector_catalog_service: ConnectorCatalogService | None = None,
) -> AppServerSession:
    return await attach_test_app_server_session(
        start_test_app_server(
            agent_loop,
            account_gateway=account_gateway,
            identity_gateway=identity_gateway,
            connector_catalog_service=connector_catalog_service,
        )
    )


async def attach_test_app_server_session(
    client: AppServerClient,
    *,
    resume_session_id: str | None = None,
    session_options: SessionOptions | None = None,
) -> AppServerSession:
    return await AppServerSession.start(
        client,
        client_info=ClientInfo(name="vibe_test", version="0"),
        capabilities=ClientCapabilities(callback_kinds=["approval", "user_input"]),
        resume_session_id=resume_session_id,
        session_options=session_options,
    )
