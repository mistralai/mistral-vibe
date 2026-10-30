from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

from authlib.integrations.httpx_client import AsyncOAuth2Client
import httpx2
from joserfc import jwt
from joserfc.jwk import RSAKey
import keyring
import pytest

from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import ClientInfo
from vibe.app_server.server import AppServer
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.setup.auth._oidc_callback import OidcCallback
from vibe.setup.auth._oidc_config import OidcConfig
from vibe.setup.auth._oidc_service import OidcService

_ISSUER = "https://issuer.example"
_CLIENT_ID = "cli-lifecycle"


class FakeBackendHost:
    async def shutdown(self) -> None:
        pass


@dataclass
class FakeOidcIssuer:
    key: RSAKey
    nonce: str = ""
    token_requests: int = 0

    def reply(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "issuer.example"
        match request.url.path:
            case "/.well-known/openid-configuration":
                return httpx2.Response(
                    200,
                    json={
                        "issuer": _ISSUER,
                        "authorization_endpoint": f"{_ISSUER}/authorize",
                        "token_endpoint": f"{_ISSUER}/token",
                        "jwks_uri": f"{_ISSUER}/jwks",
                    },
                )
            case "/jwks":
                return httpx2.Response(
                    200, json={"keys": [self.key.as_dict(private=False)]}
                )
            case "/token":
                self.token_requests += 1
                now = int(time.time())
                signed = jwt.encode(
                    {"alg": "RS256", "kid": "lifecycle"},
                    {
                        "iss": _ISSUER,
                        "aud": _CLIENT_ID,
                        "sub": "lifecycle-user",
                        "iat": now,
                        "exp": now + 3600,
                        "nonce": self.nonce,
                    },
                    self.key,
                )
                return httpx2.Response(
                    200,
                    json={
                        "access_token": "synthetic-access",
                        "refresh_token": "synthetic-refresh",
                        "id_token": signed,
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            case _:
                raise AssertionError(f"Unexpected issuer request: {request.url}")


@dataclass
class LoginFixture:
    issuer: FakeOidcIssuer
    services: list[OidcService] = field(default_factory=list)
    callbacks: list[OidcCallback] = field(default_factory=list)
    sessions: list[httpx2.AsyncClient] = field(default_factory=list)
    stored: dict[tuple[str, str], str] = field(default_factory=dict)


@pytest.fixture
def login(monkeypatch) -> LoginFixture:
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.delenv("VIBE_TEST_DISABLE_KEYRING", raising=False)
    fixture = LoginFixture(
        FakeOidcIssuer(RSAKey.generate_key(2048, parameters={"kid": "lifecycle"}))
    )
    original_factory = OidcService.from_env
    original_callback = OidcCallback

    def make_service() -> OidcService:
        service = original_factory()
        service.client.config = OidcConfig(
            _ISSUER, _CLIENT_ID, "", "http://127.0.0.1:0/callback"
        )
        fixture.services.append(service)
        return service

    def make_session(**kwargs: Any) -> AsyncOAuth2Client:
        session = AsyncOAuth2Client(
            transport=httpx2.MockTransport(fixture.issuer.reply), **kwargs
        )
        fixture.sessions.append(cast(httpx2.AsyncClient, session))
        return session

    def make_callback(redirect_uri: str, state: str) -> OidcCallback:
        callback = original_callback(redirect_uri, state)
        fixture.callbacks.append(callback)
        return callback

    monkeypatch.setattr(OidcService, "from_env", make_service)
    monkeypatch.setattr("vibe.setup.auth._oidc_client.AsyncOAuth2Client", make_session)
    monkeypatch.setattr("vibe.setup.auth._oidc_service.OidcCallback", make_callback)
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, entry: fixture.stored.get((service, entry)),
    )
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, entry, value: fixture.stored.__setitem__(
            (service, entry), value
        ),
    )
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda *_: pytest.fail("Teardown must retain stored credentials"),
    )
    return fixture


async def start_login(client: AppServerClient) -> dict[str, Any]:
    started = await client.request("setup/oidc-start", {})
    async with asyncio.timeout(2):
        while True:
            result = await client.request("setup/oidc-poll", started)
            assert result["status"] == "waiting", result
            if result["authorizationUrl"] is not None:
                return result
            await asyncio.sleep(0)


def callback_address(callback: OidcCallback) -> tuple[str, int]:
    assert callback._server is not None
    host, port = callback._server.sockets[0].getsockname()[:2]
    return host, port


@pytest.mark.asyncio
@pytest.mark.parametrize("close_on_disconnect", [False, True])
@pytest.mark.parametrize("explicit_close", [False, True])
async def test_pending_login_is_cancelled_by_connection_teardown(
    login, close_on_disconnect, explicit_close, process_services: ProcessServices
):
    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=()), org_config=process_services.org_config
        ),
        session_backend_host_factory=lambda _: cast(
            SessionBackendHost, FakeBackendHost()
        ),
    )
    serving = asyncio.create_task(
        server.serve_connection(
            server_transport, close_on_disconnect=close_on_disconnect
        )
    )
    client = AppServerClient(client_transport)
    await client.initialize(ClientInfo(name="lifecycle", version="1"))
    await client.notify("initialized")
    try:
        await start_login(client)
        callback = login.callbacks[-1]
        host, port = callback_address(callback)
        if explicit_close:
            await server.close()
        else:
            await client_transport.close()
        await asyncio.wait_for(serving, 2)
        assert callback.result.cancelled()
        assert all(session.is_closed for session in login.sessions)
        assert login.issuer.token_requests == 0
        assert login.stored == {}
        with pytest.raises(OSError):
            await asyncio.open_connection(host, port)
    finally:
        await server.close()
        await client.close()


@pytest.mark.asyncio
async def test_shared_host_disconnect_does_not_cancel_other_connection_login(
    login, process_services: ProcessServices
):
    host = HostRequestHandler(
        HarnessFilesManager(sources=()), org_config=process_services.org_config
    )
    clients = []
    servers = []
    serving_tasks = []
    for _ in range(2):
        client_transport, server_transport = memory_transport_pair()
        server = AppServer(
            server_transport,
            host_handler=host,
            session_backend_host_factory=lambda _: cast(
                SessionBackendHost, FakeBackendHost()
            ),
        )
        servers.append(server)
        serving_tasks.append(
            asyncio.create_task(
                server.serve_connection(server_transport, close_on_disconnect=False)
            )
        )
        client = AppServerClient(client_transport)
        await client.initialize(ClientInfo(name="same-client", version="1"))
        await client.notify("initialized")
        clients.append(client)
    try:
        await start_login(clients[0])
        await start_login(clients[1])
        first, second = login.callbacks
        await clients[0].close()
        await asyncio.wait_for(serving_tasks[0], 2)
        assert first.result.cancelled()
        assert not second.result.done()
        assert second._server is not None and second._server.is_serving()
        assert len(login.services) == 2
        await servers[1].close()
        await asyncio.wait_for(serving_tasks[1], 2)
        assert second.result.cancelled()
        assert login.stored == {}
    finally:
        await asyncio.gather(*(server.close() for server in servers))
        await asyncio.gather(*(client.close() for client in clients))
        await asyncio.gather(*serving_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_completed_login_credentials_survive_connection_teardown(
    login, process_services: ProcessServices
):
    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=()), org_config=process_services.org_config
        ),
        session_backend_host_factory=lambda _: cast(
            SessionBackendHost, FakeBackendHost()
        ),
    )
    serving = asyncio.create_task(server.serve())
    client = AppServerClient(client_transport)
    await client.initialize(ClientInfo(name="lifecycle", version="1"))
    await client.notify("initialized")
    try:
        result = await start_login(client)
        query = parse_qs(urlsplit(result["authorizationUrl"]).query)
        login.issuer.nonce = query["nonce"][0]
        callback = login.callbacks[-1]
        host, port = callback_address(callback)
        reader, writer = await asyncio.open_connection(host, port)
        try:
            target = "/callback?" + urlencode({
                "state": query["state"][0],
                "code": "synthetic-code",
            })
            writer.write(f"GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
            await writer.drain()
            await asyncio.wait_for(reader.read(), 1)
        finally:
            writer.close()
            await writer.wait_closed()
        task = login.services[0]._attempt.task
        assert task is not None
        await asyncio.wait_for(task, 2)
        assert login.stored
        stored = dict(login.stored)
        await server.close()
        await asyncio.wait_for(serving, 2)
        assert login.stored == stored
        assert all(session.is_closed for session in login.sessions)
    finally:
        await server.close()
        await client.close()
