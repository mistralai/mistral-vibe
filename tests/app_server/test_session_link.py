from __future__ import annotations

import inspect
from typing import Any, Protocol, cast

from pydantic import JsonValue
import pytest

from vibe.app_server import _session_backend_services
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server._session_link import SessionLink


class _Server:
    def client_owner(self) -> JsonValue:
        return {"entrypoint": "desktop", "account": {"userId": "u"}}


def _optional_services() -> list[type]:
    return [
        value
        for value in vars(_session_backend_services).values()
        if inspect.isclass(value)
        and Protocol in value.__bases__
        and value is not SessionBackendServices
        and getattr(value, "_is_runtime_protocol", False)
    ]


def test_the_link_offers_every_optional_service_of_the_channel() -> None:
    link = SessionLink(cast(Any, _Server()))

    optional = _optional_services()
    assert optional
    for protocol in optional:
        assert isinstance(link, protocol), protocol.__name__
    assert link.client_owner() == {"entrypoint": "desktop", "account": {"userId": "u"}}


@pytest.mark.asyncio
async def test_peer_rename_follows_the_current_attached_channel() -> None:
    from vibe.app_server.protocol import (
        SessionPeerRenameParams,
        SessionPeerRenameResponse,
    )

    class RenamingServer:
        def __init__(self, title: str) -> None:
            self.title = title
            self.calls: list[SessionPeerRenameParams] = []

        async def rename_peer_session(
            self, params: SessionPeerRenameParams
        ) -> SessionPeerRenameResponse:
            self.calls.append(params)
            return SessionPeerRenameResponse(
                session_id=params.session_id, title=self.title
            )

    first = RenamingServer("First authoritative title")
    second = RenamingServer("Second authoritative title")
    link = SessionLink(cast(Any, first))
    params = SessionPeerRenameParams(session_id="other", title="New title")
    assert (await link.rename_peer_session(params)).title == first.title
    link.server = cast(Any, second)
    assert (await link.rename_peer_session(params)).title == second.title
    assert first.calls == second.calls == [params]
