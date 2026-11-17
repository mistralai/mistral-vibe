from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest

from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_backend_port import (
    SessionBackendEvent,
    SessionBackendHost,
)
from vibe.app_server.events import SessionUpdated
from vibe.app_server.models import IdleSessionStatus, PublicSession
from vibe.app_server.protocol import ClientCapabilities, SessionUpdatedParams
from vibe.app_server.server import AppServer
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config.harness_files import HarnessFilesManager


def _attached_server(
    *, child_session_events: bool, process_services: ProcessServices
) -> tuple[AppServer, AsyncIterator[dict[str, Any]]]:
    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, object()),
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=("user", "project")),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        ),
        oidc_tenants=process_services.oidc_tenants,
    )
    # A session is attached and its client finished the handshake.
    server._client_capabilities = ClientCapabilities(
        child_session_events=child_session_events
    )
    server._connection_attached = True
    return server, client_transport.messages()


def _session_updated(session_id: str, *, child_session: bool) -> SessionBackendEvent:
    session = PublicSession(
        id=session_id, status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    params = SessionUpdatedParams(
        event_id=1, session_id=session_id, emitted_at=1, patch=[]
    )
    return SessionBackendEvent(
        event=SessionUpdated(previous=session, session=session, patch=[]),
        method="session/updated",
        params=params,
        session_id=session_id,
        event_id=1,
        child_session=child_session,
    )


async def _next(messages: AsyncIterator[dict[str, Any]]) -> dict[str, Any]:
    return await asyncio.wait_for(anext(messages), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("child_session_events", [True, False])
async def test_only_a_client_that_asks_receives_subagent_events(
    child_session_events: bool, process_services: ProcessServices
) -> None:
    server, messages = _attached_server(
        child_session_events=child_session_events, process_services=process_services
    )

    await server._forward_backend_event(
        _session_updated("session-child", child_session=True)
    )
    await server._forward_backend_event(
        _session_updated("session-root", child_session=False)
    )

    expected = (
        ["session-child", "session-root"] if child_session_events else ["session-root"]
    )
    received = [(await _next(messages))["params"]["sessionId"] for _ in expected]
    assert received == expected
