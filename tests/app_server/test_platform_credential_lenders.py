from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from unittest.mock import AsyncMock

import pytest

from vibe.app_server._connection_protocol import (
    ClientAccount,
    ClientCapabilities,
    ClientInfo,
    InitializeParams,
    PlatformCredentialsCapability,
    PlatformCredentialsKind,
    PlatformCredentialsLifetime,
    TransportKind,
)
from vibe.app_server._platform_credential_lenders import (
    PlatformCredentialLenders,
    account_key,
)
from vibe.app_server._platform_credentials import ConnectionPlatformCredentialProvider
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import (
    HarnessProcess,
    HarnessServer,
    create_harness_server,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import JsonRpcSuccessResponse, ServerRequest
from vibe.app_server.transport import memory_transport_pair
from vibe.core.platform_auth import (
    PlatformCredentialIdentity,
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.setup.auth._oidc_tenant import OidcTenant

_TARGET = PlatformCredentialTarget("https://api.mistral.ai/v1")
_ALICE = ClientAccount(organization_id="org-1", workspace_id="ws-1", user_id="alice")
_BOB = ClientAccount(organization_id="org-1", workspace_id="ws-1", user_id="bob")
_ORG_A = "5dd1788a-dd1b-43f5-b95c-aeb48676a276"
_ORG_B = "9a1f3c52-0b7e-4d6a-8f21-3c4d5e6f7a8b"
_WORKSPACE = "f8539dce-729c-4200-a52c-88208cebc654"
_CAROL = ClientAccount(organization_id=_ORG_A, workspace_id=_WORKSPACE, user_id="carol")


def _id_token(subject: str) -> str:
    def part(value: Mapping[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"iss": "https://auth.mistral.ai", "aud": "desktop", "sub": subject}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


@dataclass
class _Channel:
    process: HarnessProcess
    server: HarnessServer
    client: AppServerClient
    resolves: asyncio.Queue[ServerRequest]
    pump: asyncio.Task[None]

    def credentials(self) -> PlatformCredentialProvider:
        """What a session of this channel's process sends its model calls with."""
        return self.process.credentials("MISTRAL_API_KEY")

    async def next_resolve(self, timeout: float = 5) -> ServerRequest:
        return await asyncio.wait_for(self.resolves.get(), timeout)

    async def lend(self, token: str) -> None:
        request = await self.next_resolve()
        await self.client.respond(
            request.id,
            {
                "status": "ready",
                "kind": "oauth_access_token",
                "token": token,
                "audience": "https://api.mistral.ai",
                "expiresAt": 2_000_000_000_000,
                "revision": f"{token}-revision",
            },
        )

    async def lend_id_token(
        self, subject: str, *, organization_id: str = _ORG_A
    ) -> None:
        request = await self.next_resolve()
        await self.client.respond(
            request.id,
            {
                "status": "ready",
                "kind": "oidc_id_token",
                "token": _id_token(subject),
                "audience": "https://api.mistral.ai",
                "expiresAt": 2_000_000_000_000,
                "revision": f"{subject}-{organization_id}",
                "organizationId": organization_id,
                "workspaceId": _WORKSPACE,
            },
        )

    async def settle(self, channels: list[_Channel]) -> None:
        """Turn the server's own startup reads away until they are done, so a
        later request is one a test caused.
        """
        await self.client.request("config/schema")
        startup = set(self.server._server._crash_reporting_tasks)
        turning_away = [asyncio.create_task(c._turn_away()) for c in channels]
        try:
            if startup:
                await asyncio.wait(startup)
        finally:
            for task in turning_away:
                task.cancel()
            await asyncio.gather(*turning_away, return_exceptions=True)

    async def _turn_away(self) -> None:
        while True:
            request = await self.resolves.get()
            await self.client.respond(
                request.id, {"status": "unavailable", "reason": "transient"}
            )

    async def asked_nothing(self) -> bool:
        try:
            await self.next_resolve(timeout=0.2)
        except TimeoutError:
            return True
        return False

    async def close(self) -> None:
        await self.client.close()


@asynccontextmanager
async def _desktop_channels(services: ProcessServices) -> AsyncIterator[list[_Channel]]:
    channels: list[_Channel] = []
    try:
        yield channels
    finally:
        for channel in channels:
            channel.pump.cancel()
            await channel.close()


async def _open(
    channels: list[_Channel],
    services: ProcessServices,
    account: ClientAccount | None,
    *,
    lends: bool = True,
    transport_kind: TransportKind = "stdio",
    kind: PlatformCredentialsKind = "oauth_access_token",
    lifetime: PlatformCredentialsLifetime = "connection",
    left_turn_session: str | None = None,
) -> _Channel:
    client_transport, server_transport = memory_transport_pair()
    process = HarnessProcess(services=services)
    server = await create_harness_server(
        server_transport,
        transport_kind=transport_kind,
        process=process,
        left_turn_session=left_turn_session,
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.request(
        "initialize",
        InitializeParams(
            client_info=ClientInfo(
                name="vibe_desktop", version="1", entrypoint="desktop"
            ),
            capabilities=ClientCapabilities(
                platform_credentials=PlatformCredentialsCapability(
                    version=1, kind=kind, lifetime=lifetime
                )
                if lends
                else None
            ),
            account=account,
        ),
    )
    await client.notify("initialized")
    resolves: asyncio.Queue[ServerRequest] = asyncio.Queue()

    async def pump() -> None:
        async for message in client.incoming():
            if (
                isinstance(message, ServerRequest)
                and message.method == "platformCredentials/resolve"
            ):
                resolves.put_nowait(message)

    channel = _Channel(
        process=process,
        server=server,
        client=client,
        resolves=resolves,
        pump=asyncio.create_task(pump()),
    )
    channels.append(channel)
    await channel.settle(channels)
    return channel


async def _closed(channel: _Channel) -> None:
    await channel.close()
    # Let the server side see the end of the stream and close the channel.
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_session_on_a_closed_channel_borrows_its_owner_s_live_channel(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        live = await _open(channels, process_services, _ALICE)
        await _closed(parked)

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await live.lend("alice-token")

        resolved = await resolution
        assert isinstance(resolved, PlatformCredentialSnapshot)
        assert resolved.headers == {"Authorization": "Bearer alice-token"}


@pytest.mark.asyncio
async def test_a_live_channel_lends_its_own_sessions_first(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        own = await _open(channels, process_services, _ALICE)
        sibling = await _open(channels, process_services, _ALICE)

        resolution = asyncio.create_task(own.credentials().resolve(_TARGET))
        await own.lend("own-token")

        resolved = await resolution
        assert isinstance(resolved, PlatformCredentialSnapshot)
        assert resolved.headers == {"Authorization": "Bearer own-token"}
        assert await sibling.asked_nothing()


@pytest.mark.asyncio
async def test_another_account_s_channel_never_lends(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        other = await _open(channels, process_services, _BOB)
        await _closed(parked)

        assert await parked.credentials().resolve(
            _TARGET
        ) == PlatformCredentialRequired(reason="transient")
        assert await other.asked_nothing()


@pytest.mark.asyncio
async def test_a_channel_without_an_account_neither_lends_nor_borrows(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        anonymous = await _open(channels, process_services, None)
        live = await _open(channels, process_services, _ALICE)
        await _closed(anonymous)

        assert await anonymous.credentials().resolve(
            _TARGET
        ) == PlatformCredentialRequired(reason="transient")
        assert await live.asked_nothing()

        parked = await _open(channels, process_services, _ALICE)
        await _closed(parked)
        await _closed(live)
        await _open(channels, process_services, None)
        assert await parked.credentials().resolve(
            _TARGET
        ) == PlatformCredentialRequired(reason="transient")


@pytest.mark.asyncio
async def test_a_closed_lender_stops_lending(process_services: ProcessServices) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        lender = await _open(channels, process_services, _ALICE)
        await _closed(parked)
        await _closed(lender)

        assert await parked.credentials().resolve(
            _TARGET
        ) == PlatformCredentialRequired(reason="transient")


@pytest.mark.asyncio
async def test_a_borrowed_token_s_rejection_goes_to_the_channel_that_lent_it(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        live = await _open(channels, process_services, _ALICE)
        await _closed(parked)
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await live.lend("refused-token")
        refused = await resolution
        assert isinstance(refused, PlatformCredentialSnapshot)

        rejection = asyncio.create_task(
            parked.credentials().reject(
                _TARGET,
                observed=PlatformCredentialIdentity(
                    kind=refused.kind,
                    cache_namespace=refused.cache_namespace,
                    revision=refused.revision,
                ),
                reason="http_unauthorized",
            )
        )
        request = await live.next_resolve()
        assert request.params == {
            "audience": "https://api.mistral.ai",
            "minimumValidityMs": 60_000,
            "rejectedRevision": "refused-token-revision",
        }
        await live.client.respond(
            request.id, {"status": "unavailable", "reason": "reauthentication_required"}
        )
        assert await rejection == PlatformCredentialRequired(
            reason="reauthentication_required"
        )


@pytest.mark.asyncio
async def test_a_lender_that_closes_mid_request_answers_transient(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        lender = await _open(channels, process_services, _ALICE)
        await _closed(parked)
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await lender.next_resolve()

        await _closed(lender)

        assert await asyncio.wait_for(resolution, 5) == PlatformCredentialRequired(
            reason="transient"
        )


@pytest.mark.asyncio
async def test_a_rejection_goes_to_the_current_lender_once_the_issuer_closed(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        issuer = await _open(channels, process_services, _ALICE)
        await _closed(parked)
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await issuer.lend("issued-token")
        issued = await resolution
        assert isinstance(issued, PlatformCredentialSnapshot)
        current = await _open(channels, process_services, _ALICE)
        await _closed(issuer)

        rejection = asyncio.create_task(
            parked.credentials().reject(
                _TARGET,
                observed=PlatformCredentialIdentity(
                    kind=issued.kind,
                    cache_namespace=issued.cache_namespace,
                    revision=issued.revision,
                ),
                reason="http_unauthorized",
            )
        )
        await current.lend("replacement-token")

        replaced = await rejection
        assert isinstance(replaced, PlatformCredentialSnapshot)
        assert replaced.headers == {"Authorization": "Bearer replacement-token"}


@pytest.mark.asyncio
async def test_a_reconnectable_connection_stops_lending_when_its_client_leaves(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        await _closed(parked)
        reconnectable = await _open(
            channels, process_services, _ALICE, transport_kind="in_process"
        )
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await reconnectable.lend("reconnectable-token")
        assert isinstance(await resolution, PlatformCredentialSnapshot)

        await _closed(reconnectable)

        assert await parked.credentials().resolve(
            _TARGET
        ) == PlatformCredentialRequired(reason="transient")


@pytest.mark.asyncio
async def test_a_borrow_in_flight_ends_before_the_lender_s_session_shuts_down(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        lender = await _open(channels, process_services, _ALICE)
        await _closed(parked)
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await lender.next_resolve()
        shutdown_started = asyncio.Event()
        release_shutdown = asyncio.Event()
        close_root = lender.server._server._close_root

        async def slow_close_root() -> None:
            shutdown_started.set()
            await release_shutdown.wait()
            await close_root()

        monkeypatch.setattr(lender.server._server, "_close_root", slow_close_root)

        closing = asyncio.create_task(lender.close())
        await asyncio.wait_for(shutdown_started.wait(), 5)
        try:
            assert await asyncio.wait_for(resolution, 1) == PlatformCredentialRequired(
                reason="transient"
            )
        finally:
            release_shutdown.set()
            await closing


@pytest.mark.asyncio
async def test_a_reply_to_a_resolve_abandoned_by_a_close_is_dropped(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        lender = await _open(channels, process_services, _ALICE)
        await _closed(parked)
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        request = await lender.next_resolve()
        app_server = lender.server._server
        app_server._stop_lending("The client disconnected")
        assert await asyncio.wait_for(resolution, 5) == PlatformCredentialRequired(
            reason="transient"
        )

        await app_server._handle_response(
            JsonRpcSuccessResponse(
                jsonrpc="2.0",
                id=request.id,
                result={"status": "unavailable", "reason": "transient"},
            )
        )


@pytest.mark.asyncio
async def test_the_process_knows_whether_an_account_has_a_durable_lender(
    process_services: ProcessServices,
) -> None:
    lenders = process_services.platform_credential_lenders
    async with _desktop_channels(process_services) as channels:
        assert lenders.availability(account_key(_ALICE)) == "none"
        chat = await _open(channels, process_services, _ALICE)
        assert lenders.availability(account_key(_ALICE)) == "connections_only"
        catalogue = await _open(channels, process_services, _ALICE, lifetime="process")
        assert lenders.availability(account_key(_ALICE)) == "durable"
        assert lenders.availability(account_key(_BOB)) == "none"

        await _closed(catalogue)
        assert lenders.availability(account_key(_ALICE)) == "connections_only"
        await _closed(chat)
        assert lenders.availability(account_key(_ALICE)) == "none"


@pytest.mark.asyncio
async def test_a_session_whose_chat_closed_borrows_from_the_durable_lender(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        catalogue = await _open(channels, process_services, _ALICE, lifetime="process")
        chat = await _open(channels, process_services, _ALICE)
        await _closed(chat)

        resolution = asyncio.create_task(chat.credentials().resolve(_TARGET))
        await catalogue.lend("catalogue-token")

        resolved = await resolution
        assert isinstance(resolved, PlatformCredentialSnapshot)
        assert resolved.headers == {"Authorization": "Bearer catalogue-token"}


@pytest.mark.asyncio
async def test_a_lender_whose_token_names_another_tenant_never_lends(
    process_services: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Desktop switched organization; this channel still reports the old one.
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        stale = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        await _closed(parked)

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await stale.lend_id_token("carol-sub", organization_id=_ORG_B)

        assert await resolution == PlatformCredentialRequired(reason="transient")
        assert "does not match the account" in caplog.text


@pytest.mark.asyncio
async def test_a_lender_whose_token_belongs_to_another_user_never_lends(
    process_services: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await parked.lend_id_token("carol-sub")
        assert isinstance(await resolution, PlatformCredentialSnapshot)
        impostor = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        await _closed(parked)

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await impostor.lend_id_token("mallory-sub")

        assert await resolution == PlatformCredentialRequired(reason="transient")
        assert "does not match the account" in caplog.text


@pytest.mark.asyncio
async def test_a_matching_id_token_is_lent(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await parked.lend_id_token("carol-sub")
        assert isinstance(await resolution, PlatformCredentialSnapshot)
        lender = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        await _closed(parked)

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await lender.lend_id_token("carol-sub")

        resolved = await resolution
        assert isinstance(resolved, PlatformCredentialSnapshot)
        assert resolved.headers["Authorization"] == f"Bearer {_id_token('carol-sub')}"


@pytest.mark.asyncio
async def test_an_operator_tenant_override_does_not_block_borrowing(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", _ORG_B)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        lender = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        await _closed(parked)

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await lender.lend_id_token("carol-sub")

        assert isinstance(await resolution, PlatformCredentialSnapshot)


@pytest.mark.asyncio
async def test_a_process_lifetime_lender_is_declared_on_the_wire(
    process_services: ProcessServices,
) -> None:
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="stdio",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    try:
        response = await client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "vibe_desktop",
                    "version": "1",
                    "entrypoint": "desktop",
                },
                "capabilities": {
                    "platformCredentials": {
                        "kind": "oidc_id_token",
                        "version": 1,
                        "lifetime": "process",
                    }
                },
                "account": {
                    "organizationId": "org-1",
                    "workspaceId": "ws-1",
                    "userId": "alice",
                },
            },
        )

        assert response["platformCredentials"] is True
        lenders = process_services.platform_credential_lenders
        assert lenders.availability(account_key(_ALICE)) == "durable"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_session_link_tells_whether_its_account_can_still_borrow(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        chat = await _open(channels, process_services, _ALICE)
        link = chat.server._server.session_link
        assert link.lender_availability() == "connections_only"

        catalogue = await _open(channels, process_services, _ALICE, lifetime="process")
        await _closed(chat)
        assert link.lender_availability() == "durable"

        await _closed(catalogue)
        assert link.lender_availability() == "none"

        anonymous = await _open(channels, process_services, None)
        assert anonymous.server._server.session_link.lender_availability() == "none"


@pytest.mark.asyncio
async def test_the_account_s_user_is_remembered_after_its_lenders_leave(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await parked.lend_id_token("carol-sub")
        assert isinstance(await resolution, PlatformCredentialSnapshot)
        await _closed(parked)
        impostor = await _open(channels, process_services, _CAROL, kind="oidc_id_token")

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await impostor.lend_id_token("mallory-sub")

        assert await resolution == PlatformCredentialRequired(reason="transient")


@pytest.mark.asyncio
async def test_the_first_borrowed_user_is_the_account_s(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        lender = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        await _closed(parked)
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await lender.lend_id_token("carol-sub")
        assert isinstance(await resolution, PlatformCredentialSnapshot)
        await _closed(lender)
        impostor = await _open(channels, process_services, _CAROL, kind="oidc_id_token")

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await impostor.lend_id_token("mallory-sub")

        assert await resolution == PlatformCredentialRequired(reason="transient")


@pytest.mark.asyncio
async def test_a_refused_durable_lender_does_not_hide_a_matching_one(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    lenders = process_services.platform_credential_lenders
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await parked.lend_id_token("carol-sub")
        assert isinstance(await resolution, PlatformCredentialSnapshot)
        chat = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        stale = await _open(
            channels, process_services, _CAROL, kind="oidc_id_token", lifetime="process"
        )
        await _closed(parked)

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await stale.lend_id_token("carol-sub", organization_id=_ORG_B)
        await chat.lend_id_token("carol-sub")

        resolved = await resolution
        assert isinstance(resolved, PlatformCredentialSnapshot)
        assert lenders.availability(account_key(_CAROL)) == "connections_only"


@pytest.mark.asyncio
async def test_a_connection_that_lends_nothing_reports_no_lender(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        await _open(channels, process_services, _ALICE, lifetime="process")
        silent = await _open(channels, process_services, _ALICE, lends=False)

        assert silent.server._server.session_link.lender_availability() == "none"


@pytest.mark.asyncio
async def test_an_id_token_without_a_named_organization_is_not_borrowed(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        lender = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        await _closed(parked)
        process_services.oidc_tenants._discover = AsyncMock(  # type: ignore[method-assign]
            return_value=OidcTenant(_ORG_A, None)
        )

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        request = await lender.next_resolve()
        await lender.client.respond(
            request.id,
            {
                "status": "ready",
                "kind": "oidc_id_token",
                "token": _id_token("carol-sub"),
                "audience": "https://api.mistral.ai",
                "expiresAt": 2_000_000_000_000,
                "revision": "unnamed",
            },
        )

        assert await resolution == PlatformCredentialRequired(reason="transient")


@pytest.mark.asyncio
async def test_a_session_keeps_the_user_it_ran_as_after_a_sign_in_change(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        carol = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        resolution = asyncio.create_task(carol.credentials().resolve(_TARGET))
        await carol.lend_id_token("carol-sub")
        assert isinstance(await resolution, PlatformCredentialSnapshot)
        await _closed(carol)
        relogged = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        resolution = asyncio.create_task(relogged.credentials().resolve(_TARGET))
        await relogged.lend_id_token("carol-new-sub")
        assert isinstance(await resolution, PlatformCredentialSnapshot)

        resolution = asyncio.create_task(carol.credentials().resolve(_TARGET))
        await relogged.lend_id_token("carol-new-sub")

        assert await resolution == PlatformCredentialRequired(reason="transient")


@pytest.mark.asyncio
async def test_a_lender_skipped_for_its_user_is_not_asked_again_until_it_signs_in(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _CAROL, kind="oidc_id_token")
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await parked.lend_id_token("carol-sub")
        assert isinstance(await resolution, PlatformCredentialSnapshot)
        await _closed(parked)
        impostor = await _open(
            channels, process_services, _CAROL, kind="oidc_id_token", lifetime="process"
        )
        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await impostor.lend_id_token("mallory-sub")
        assert await resolution == PlatformCredentialRequired(reason="transient")
        link = parked.server._server.session_link

        assert link.lender_availability() == "none"
        assert await parked.credentials().resolve(_TARGET) == (
            PlatformCredentialRequired(reason="transient")
        )
        assert await impostor.asked_nothing()

        await impostor.client.notify(
            "platformCredentials/invalidated", {"reason": "account_changed"}
        )
        for _ in range(20):
            await asyncio.sleep(0)
        assert link.lender_availability() == "durable"


def _lender() -> ConnectionPlatformCredentialProvider:
    return ConnectionPlatformCredentialProvider(
        AsyncMock(), tenants=AsyncMock(), kind="oidc_id_token"
    )


@pytest.mark.asyncio
async def test_a_change_future_is_done_when_its_account_gains_or_loses_a_lender() -> (
    None
):
    lenders = PlatformCredentialLenders()
    key, other = account_key(_CAROL), account_key(_ALICE)
    lender = _lender()

    changed = lenders.changed(key)
    lenders.register(other, _lender())
    assert not changed.done()
    lenders.register(key, lender, durable=True)
    assert changed.done()

    changed = lenders.changed(key)
    lenders.unregister(lender)
    assert changed.done()


@pytest.mark.asyncio
async def test_a_change_future_is_done_when_a_lender_is_refused_or_reinstated() -> None:
    lenders = PlatformCredentialLenders()
    key = account_key(_CAROL)
    lender = _lender()
    lenders.register(key, lender, durable=True)

    changed = lenders.changed(key)
    lenders.refuse(lender, user="mallory")
    assert changed.done()
    changed = lenders.changed(key)
    lenders.refuse(lender, user="mallory")
    assert not changed.done()

    lenders.refuse(lender)
    assert changed.done()

    changed = lenders.changed(key)
    lenders.reinstate(lender)
    assert changed.done()


@pytest.mark.asyncio
async def test_a_cancelled_or_finished_change_future_leaves_nothing_behind() -> None:
    lenders = PlatformCredentialLenders()
    key = account_key(_CAROL)

    lenders.changed(key).cancel()
    lenders.changed(key)
    lenders.register(key, _lender())
    await asyncio.sleep(0)

    assert not lenders._waiters


@pytest.mark.asyncio
async def test_a_channel_the_server_opens_for_a_left_turn_borrows_but_never_lends(
    process_services: ProcessServices,
) -> None:
    async with _desktop_channels(process_services) as channels:
        parked = await _open(channels, process_services, _ALICE)
        live = await _open(channels, process_services, _ALICE)
        server_side = await _open(
            channels, process_services, _ALICE, left_turn_session="session-1"
        )
        await _closed(parked)

        resolution = asyncio.create_task(parked.credentials().resolve(_TARGET))
        await live.lend("alice-token")

        resolved = await resolution
        assert isinstance(resolved, PlatformCredentialSnapshot)
        assert resolved.headers == {"Authorization": "Bearer alice-token"}
        assert await server_side.asked_nothing()
