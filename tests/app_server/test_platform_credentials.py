from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

import pytest

from vibe.app_server._connection_protocol import (
    ClientCapabilities,
    ClientInfo,
    PlatformCredentialsCapability,
    PlatformCredentialsResolveParams,
    PlatformCredentialsSnapshot,
    PlatformCredentialsUnavailable,
)
from vibe.app_server._model import validate_wire
from vibe.app_server._platform_credentials import ConnectionPlatformCredentialProvider
from vibe.app_server._runtime import create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    IdentityReadParams,
    IdentityReadResponse,
    ServerRequest,
    SessionStartParams,
    SessionStartResponse,
    SessionStopParams,
)
from vibe.app_server.transport import memory_transport_pair
from vibe.core.identity import IdentityResult
from vibe.core.platform_auth import (
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)


class _ResolvingIdentityGateway:
    def __init__(self) -> None:
        self.snapshots: list[PlatformCredentialSnapshot] = []

    async def read(
        self,
        *,
        base_url: str,
        credentials: PlatformCredentialProvider,
        timeout: float | None = None,
    ) -> IdentityResult:
        del timeout
        resolved = await credentials.resolve(
            PlatformCredentialTarget(base_url=base_url)
        )
        if not isinstance(resolved, PlatformCredentialSnapshot):
            raise RuntimeError("Expected the identity credential to resolve")
        self.snapshots.append(resolved)
        return IdentityResult(id="desktop-user")


@pytest.mark.asyncio
async def test_resolves_desktop_access_token_for_platform_origin() -> None:
    requests: list[PlatformCredentialsResolveParams] = []

    async def resolve(
        params: PlatformCredentialsResolveParams,
    ) -> PlatformCredentialsSnapshot:
        requests.append(params)
        return PlatformCredentialsSnapshot(
            status="ready",
            kind="oauth_access_token",
            token="access-token",
            audience=params.audience,
            expires_at=2_000_000_000_000,
            revision="revision-1",
        )

    provider = ConnectionPlatformCredentialProvider(resolve)
    result = await provider.resolve(
        PlatformCredentialTarget(base_url="https://api.mistral.ai/v1")
    )

    assert isinstance(result, PlatformCredentialSnapshot)
    assert result.headers == {"Authorization": "Bearer access-token"}
    assert result.expires_at == datetime.fromtimestamp(2_000_000_000, tz=UTC)
    assert requests == [
        PlatformCredentialsResolveParams(
            audience="https://api.mistral.ai", minimum_validity_ms=60_000
        )
    ]
    assert "access-token" not in repr(result)


@pytest.mark.asyncio
async def test_rejection_is_revision_aware() -> None:
    rejected_revisions: list[str | None] = []

    async def resolve(
        params: PlatformCredentialsResolveParams,
    ) -> PlatformCredentialsSnapshot:
        rejected_revisions.append(params.rejected_revision)
        return PlatformCredentialsSnapshot(
            status="ready",
            kind="oauth_access_token",
            token="new-access-token",
            audience=params.audience,
            expires_at=2_000_000_000_000,
            revision="revision-2",
        )

    provider = ConnectionPlatformCredentialProvider(resolve)
    target = PlatformCredentialTarget(base_url="https://api.mistral.ai/v1")
    first = await provider.resolve(target)
    assert isinstance(first, PlatformCredentialSnapshot)

    result = await provider.reject(
        target, observed=first.identity, reason="http_unauthorized"
    )

    assert isinstance(result, PlatformCredentialSnapshot)
    assert result.revision == "revision-2"
    assert rejected_revisions == [None, "revision-2"]


@pytest.mark.asyncio
async def test_invalidation_discards_an_in_flight_snapshot() -> None:
    requested = asyncio.Event()
    release = asyncio.Event()

    async def resolve(
        params: PlatformCredentialsResolveParams,
    ) -> PlatformCredentialsSnapshot:
        requested.set()
        await release.wait()
        return PlatformCredentialsSnapshot(
            status="ready",
            kind="oauth_access_token",
            token="stale-access-token",
            audience=params.audience,
            expires_at=2_000_000_000_000,
            revision="stale-revision",
        )

    provider = ConnectionPlatformCredentialProvider(resolve)
    pending = asyncio.create_task(
        provider.resolve(PlatformCredentialTarget("https://api.mistral.ai/v1"))
    )
    await requested.wait()
    provider.invalidate()
    release.set()

    assert await pending == PlatformCredentialRequired(
        reason="reauthentication_required"
    )


@pytest.mark.asyncio
async def test_unavailable_response_never_falls_back_to_an_api_key() -> None:
    async def resolve(
        params: PlatformCredentialsResolveParams,
    ) -> PlatformCredentialsUnavailable:
        del params
        return PlatformCredentialsUnavailable(
            status="unavailable", reason="reauthentication_required"
        )

    provider = ConnectionPlatformCredentialProvider(resolve)

    assert await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    ) == PlatformCredentialRequired(reason="reauthentication_required")


@pytest.mark.asyncio
async def test_app_server_resolves_host_credentials_over_json_rpc() -> None:
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(server_transport, transport_kind="in_process")
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(
        ClientInfo(name="desktop-test", version="1", entrypoint="desktop"),
        ClientCapabilities(
            platform_credentials=PlatformCredentialsCapability(
                version=1, kind="oauth_access_token"
            )
        ),
    )
    await client.notify("initialized")
    provider = server._server.platform_credentials("MISTRAL_API_KEY")
    resolution = asyncio.create_task(
        provider.resolve(PlatformCredentialTarget("https://api.mistral.ai/v1"))
    )
    incoming = client.incoming()

    request = await anext(incoming)
    assert isinstance(request, ServerRequest)
    assert request.method == "platformCredentials/resolve"
    assert request.params == {
        "audience": "https://api.mistral.ai",
        "minimumValidityMs": 60_000,
    }
    await client.respond(
        request.id,
        {
            "status": "ready",
            "kind": "oauth_access_token",
            "token": "desktop-access-token",
            "audience": "https://api.mistral.ai",
            "expiresAt": 2_000_000_000_000,
            "revision": "desktop-revision-1",
        },
    )

    resolved = await resolution
    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers == {"Authorization": "Bearer desktop-access-token"}

    stale_resolution = asyncio.create_task(
        provider.resolve(PlatformCredentialTarget("https://api.mistral.ai/v1"))
    )
    stale_request = await anext(incoming)
    assert isinstance(stale_request, ServerRequest)
    await client.notify("platformCredentials/invalidated", {"reason": "logout"})
    await client.respond(
        stale_request.id,
        {
            "status": "ready",
            "kind": "oauth_access_token",
            "token": "stale-desktop-access-token",
            "audience": "https://api.mistral.ai",
            "expiresAt": 2_000_000_000_000,
            "revision": "stale-desktop-revision",
        },
    )
    assert await stale_resolution == PlatformCredentialRequired(
        reason="reauthentication_required"
    )
    await incoming.aclose()
    await client.close()


@pytest.mark.asyncio
async def test_connection_without_capability_keeps_api_key_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "cli-api-key")
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(server_transport, transport_kind="in_process")
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="cli-test", version="1", entrypoint="cli"))
    await client.notify("initialized")

    provider = server._server.platform_credentials("MISTRAL_API_KEY")
    resolved = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.kind == "api_key"
    assert resolved.headers == {"Authorization": "Bearer cli-api-key"}
    await client.close()


@pytest.mark.asyncio
async def test_identity_read_uses_connection_oauth_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key-that-must-not-be-used")
    gateway = _ResolvingIdentityGateway()
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport, transport_kind="in_process", identity_gateway=gateway
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    requests: list[PlatformCredentialsResolveParams] = []

    async def broker() -> None:
        async for message in client.incoming():
            if not isinstance(message, ServerRequest):
                continue
            if message.method != "platformCredentials/resolve":
                continue
            params = PlatformCredentialsResolveParams.model_validate(message.params)
            requests.append(params)
            await client.respond(
                message.id,
                PlatformCredentialsSnapshot(
                    status="ready",
                    kind="oauth_access_token",
                    token="desktop-identity-token",
                    audience=params.audience,
                    expires_at=2_000_000_000_000,
                    revision="desktop-identity-revision",
                ),
            )

    broker_task: asyncio.Task[None] | None = None
    session_id: str | None = None
    try:
        await client.initialize(
            ClientInfo(name="desktop-test", version="1", entrypoint="desktop"),
            ClientCapabilities(
                platform_credentials=PlatformCredentialsCapability(
                    version=1, kind="oauth_access_token"
                )
            ),
        )
        await client.notify("initialized")
        broker_task = asyncio.create_task(broker())
        started = validate_wire(
            SessionStartResponse,
            await client.request("session/start", SessionStartParams()),
        )
        session_id = started.state.session.id

        identity = validate_wire(
            IdentityReadResponse,
            await client.request(
                "identity/read", IdentityReadParams(session_id=session_id)
            ),
        ).identity

        assert identity is not None
        assert identity.id == "desktop-user"
        assert requests
        assert all(request.audience == "https://api.mistral.ai" for request in requests)
        assert gateway.snapshots
        assert all(
            snapshot.kind == "oauth_access_token" for snapshot in gateway.snapshots
        )
        assert all(
            snapshot.headers == {"Authorization": "Bearer desktop-identity-token"}
            for snapshot in gateway.snapshots
        )
    finally:
        if session_id is not None:
            with suppress(Exception):
                await client.request(
                    "session/stop", SessionStopParams(session_id=session_id)
                )
        await client.close()
        if broker_task is not None:
            broker_task.cancel()
            with suppress(asyncio.CancelledError):
                await broker_task


@pytest.mark.asyncio
async def test_identity_read_without_capability_keeps_api_key_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "cli-identity-api-key")
    gateway = _ResolvingIdentityGateway()
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport, transport_kind="in_process", identity_gateway=gateway
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    session_id: str | None = None
    try:
        await client.initialize(
            ClientInfo(name="cli-test", version="1", entrypoint="cli")
        )
        await client.notify("initialized")
        started = validate_wire(
            SessionStartResponse,
            await client.request("session/start", SessionStartParams()),
        )
        session_id = started.state.session.id

        identity = validate_wire(
            IdentityReadResponse,
            await client.request(
                "identity/read", IdentityReadParams(session_id=session_id)
            ),
        ).identity

        assert identity is not None
        assert identity.id == "desktop-user"
        assert gateway.snapshots
        assert all(snapshot.kind == "api_key" for snapshot in gateway.snapshots)
        assert all(
            snapshot.headers == {"Authorization": "Bearer cli-identity-api-key"}
            for snapshot in gateway.snapshots
        )
    finally:
        if session_id is not None:
            with suppress(Exception):
                await client.request(
                    "session/stop", SessionStopParams(session_id=session_id)
                )
        await client.close()
