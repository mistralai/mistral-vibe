from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

from git import Repo
import httpx2
import pytest

from tests.stubs.fake_utility_completion import FakeUtilityCompletion
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
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    AutoWorktreeInput,
    IdentityReadParams,
    IdentityReadResponse,
    ProtocolError,
    ProtocolErrorCode,
    ServerRequest,
    SessionOptions,
    SessionStartParams,
    SessionStartResponse,
    SessionStopParams,
)
from vibe.app_server.transport import memory_transport_pair
from vibe.core.identity import IdentityResult
from vibe.core.llm import model_probe
from vibe.core.llm.utility_completion import FAST_MODEL_CANDIDATES
from vibe.core.platform_auth import (
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.setup.auth._oidc_config import OidcError
from vibe.setup.auth._oidc_tenant import (
    OidcTenant,
    OidcTenantResolver,
    OidcUnauthorized,
)

# Captured before the conftest fixture stubs it out.
_REAL_PROBE = model_probe._probe


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

    provider = ConnectionPlatformCredentialProvider(
        resolve, tenants=OidcTenantResolver()
    )
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

    provider = ConnectionPlatformCredentialProvider(
        resolve, tenants=OidcTenantResolver()
    )
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

    provider = ConnectionPlatformCredentialProvider(
        resolve, tenants=OidcTenantResolver()
    )
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

    provider = ConnectionPlatformCredentialProvider(
        resolve, tenants=OidcTenantResolver()
    )

    assert await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    ) == PlatformCredentialRequired(reason="reauthentication_required")


@pytest.mark.asyncio
async def test_app_server_resolves_host_credentials_over_json_rpc(
    process_services: ProcessServices,
) -> None:
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
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
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "cli-api-key")
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
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
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key-that-must-not-be-used")
    gateway = _ResolvingIdentityGateway()
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        identity_gateway=gateway,
        process=HarnessProcess(services=process_services),
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
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "cli-identity-api-key")
    gateway = _ResolvingIdentityGateway()
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        identity_gateway=gateway,
        process=HarnessProcess(services=process_services),
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


def _id_token(subject: str = "desktop-user") -> str:
    def part(value: Mapping[str, object]) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    claims = {"iss": "https://auth.mistral.ai", "aud": "desktop", "sub": subject}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


def _http_status_error(status: int) -> httpx2.HTTPStatusError:
    request = httpx2.Request("GET", "https://api.mistral.ai/v1/users/me/workspaces")
    return httpx2.HTTPStatusError(
        f"HTTP {status}",
        request=request,
        response=httpx2.Response(status, request=request),
    )


def _id_token_resolver(
    token: str,
    requests: list[PlatformCredentialsResolveParams] | None = None,
    *,
    organization_id: str | None = None,
    workspace_id: str | None = None,
):
    async def resolve(
        params: PlatformCredentialsResolveParams,
    ) -> PlatformCredentialsSnapshot:
        if requests is not None:
            requests.append(params)
        return PlatformCredentialsSnapshot(
            status="ready",
            kind="oidc_id_token",
            token=token,
            audience=params.audience,
            expires_at=2_000_000_000_000,
            revision=f"revision-{len(requests or [])}",
            organization_id=organization_id,
            workspace_id=workspace_id,
        )

    return resolve


@pytest.mark.asyncio
async def test_desktop_id_token_carries_the_discovered_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    token = _id_token()
    discover = AsyncMock(return_value=OidcTenant("org-1", "ws-1"))
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(token),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(discover),
    )

    first = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )
    second = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(first, PlatformCredentialSnapshot)
    assert first.kind == "oidc_id_token"
    assert first.follows_redirects is False
    assert first.headers == {
        "Authorization": f"Bearer {token}",
        "x-external-organization-id": "org-1",
        "x-external-workspace-id": "ws-1",
    }
    assert first.principal is not None
    assert first.principal.startswith("oidc:v1:")
    assert provider.cached_principal() == first.principal
    assert isinstance(second, PlatformCredentialSnapshot)
    assert second.principal == first.principal
    discover.assert_awaited_once_with("https://api.mistral.ai", token, "")


@pytest.mark.asyncio
async def test_desktop_id_token_honors_the_tenant_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user with workspaces in more than one organization would be resolved
    into the first one in the discovery list, which may be the org without the
    model entitlement. The tenant overrides exist for exactly that, and a
    Desktop whose launch command or environment sets them must not have its
    requests silently land in another organization.
    """
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "chosen-org")
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "chosen-workspace")
    token = _id_token()
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(token),
        kind="oidc_id_token",
        # An override that reaches discovery is a bug: the whole point of the
        # explicit selection is that no guesswork runs at all.
        tenants=OidcTenantResolver(AsyncMock(side_effect=AssertionError)),
    )

    resolved = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers == {
        "Authorization": f"Bearer {token}",
        "x-external-organization-id": "chosen-org",
        "x-external-workspace-id": "chosen-workspace",
    }


@pytest.mark.asyncio
async def test_desktop_id_token_uses_the_tenant_the_desktop_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    token = _id_token()
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(
            token,
            organization_id="5DD1788a-dd1b-43f5-b95c-aeb48676a276",
            workspace_id="f8539dce-729c-4200-a52c-88208cebc654",
        ),
        kind="oidc_id_token",
        # A named tenant that still reaches discovery would defeat it.
        tenants=OidcTenantResolver(AsyncMock(side_effect=AssertionError)),
    )

    resolved = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers == {
        "Authorization": f"Bearer {token}",
        "x-external-organization-id": "5dd1788a-dd1b-43f5-b95c-aeb48676a276",
        "x-external-workspace-id": "f8539dce-729c-4200-a52c-88208cebc654",
    }


@pytest.mark.asyncio
async def test_tenant_env_override_beats_the_desktop_named_tenant(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "pinned-org")
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    token = _id_token()
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(
            token, organization_id="5dd1788a-dd1b-43f5-b95c-aeb48676a276"
        ),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(AsyncMock(side_effect=AssertionError)),
    )

    with caplog.at_level(
        logging.WARNING, logger="vibe.app_server._platform_credentials"
    ):
        resolved = await provider.resolve(
            PlatformCredentialTarget("https://api.mistral.ai/v1")
        )

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers["x-external-organization-id"] == "pinned-org"
    assert "shadows" in caplog.text
    assert "5dd1788a-dd1b-43f5-b95c-aeb48676a276" in caplog.text


@pytest.mark.asyncio
async def test_desktop_id_token_env_workspace_beats_the_desktop_named_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace-only override reaches discovery filtered to it, so that
    workspace's own organization answers and the pair cannot disagree.
    """
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "chosen-workspace")
    token = _id_token()
    discover = AsyncMock(return_value=OidcTenant("org-2", "chosen-workspace"))
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(
            token,
            organization_id="5dd1788a-dd1b-43f5-b95c-aeb48676a276",
            workspace_id="f8539dce-729c-4200-a52c-88208cebc654",
        ),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(discover),
    )

    resolved = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers == {
        "Authorization": f"Bearer {token}",
        "x-external-organization-id": "org-2",
        "x-external-workspace-id": "chosen-workspace",
    }
    discover.assert_awaited_once_with(
        "https://api.mistral.ai", token, "chosen-workspace"
    )


@pytest.mark.asyncio
async def test_malformed_desktop_named_tenant_falls_back_to_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    token = _id_token()
    discover = AsyncMock(return_value=OidcTenant("org-1", "ws-1"))
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(token, organization_id="not-a-uuid"),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(discover),
    )

    resolved = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers["x-external-organization-id"] == "org-1"
    discover.assert_awaited_once_with("https://api.mistral.ai", token, "")


@pytest.mark.asyncio
async def test_malformed_desktop_named_workspace_keeps_the_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    token = _id_token()
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(
            token,
            organization_id="5dd1788a-dd1b-43f5-b95c-aeb48676a276",
            workspace_id="not-a-uuid",
        ),
        kind="oidc_id_token",
        # A named organization that still reaches discovery would defeat it.
        tenants=OidcTenantResolver(AsyncMock(side_effect=AssertionError)),
    )

    resolved = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers == {
        "Authorization": f"Bearer {token}",
        "x-external-organization-id": "5dd1788a-dd1b-43f5-b95c-aeb48676a276",
    }


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError(),
        httpx2.ConnectError("offline"),
        _http_status_error(503),
        _http_status_error(429),
    ],
    ids=["timeout", "offline", "server-error", "rate-limited"],
)
@pytest.mark.asyncio
async def test_desktop_id_token_discovery_without_an_answer_is_transient(
    failure: Exception,
) -> None:
    """*Prepare*: A Desktop ID token whose workspace discovery gets no usable
      answer: it fails, times out, or the platform is down or busy.
    *Do*: Resolve the credential.
    *Assert*: It is ``transient``, which waits and retries, not a request to
      sign in again: nothing said the sign-in was bad.
    """
    # Prepare
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(_id_token()),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(AsyncMock(side_effect=failure)),
    )

    # Do
    result = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    # Assert
    assert result == PlatformCredentialRequired(reason="transient")


@pytest.mark.parametrize(
    "failure",
    [
        OidcError(
            "No accessible OIDC workspace found; check membership or workspace override"
        ),
        _http_status_error(403),
        ValueError("unreadable workspaces page"),
    ],
    ids=["no-workspace", "forbidden", "unreadable-answer"],
)
@pytest.mark.asyncio
async def test_desktop_id_token_discovery_refusal_requires_sign_in(
    failure: Exception,
) -> None:
    """*Prepare*: A Desktop ID token whose workspace discovery answers that the
      user has no workspace, refuses the request, or answers with a page this
      client cannot read.
    *Do*: Resolve the credential.
    *Assert*: It asks to sign in again, as before: the same request fails the
      same way until the user's access changes.
    """
    # Prepare
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(_id_token()),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(AsyncMock(side_effect=failure)),
    )

    # Do
    result = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    # Assert
    assert result == PlatformCredentialRequired(reason="reauthentication_required")


@pytest.mark.parametrize(
    "answer",
    [
        RuntimeError("Desktop platform credential request failed"),
        PlatformCredentialsUnavailable(status="unavailable", reason="transient"),
    ],
    ids=["broker-error", "desktop-transient"],
)
@pytest.mark.asyncio
async def test_a_desktop_that_cannot_answer_is_transient(
    answer: Exception | PlatformCredentialsUnavailable,
) -> None:
    """*Prepare*: A Desktop broker that fails to answer (the app server raises
      for a JSON-RPC error or an unreadable answer), or answers ``transient``.
    *Do*: Resolve the credential.
    *Assert*: It is ``transient``.
    """

    # Prepare
    async def resolve(
        params: PlatformCredentialsResolveParams,
    ) -> PlatformCredentialsUnavailable:
        del params
        if isinstance(answer, Exception):
            raise answer
        return answer

    provider = ConnectionPlatformCredentialProvider(
        resolve, kind="oidc_id_token", tenants=OidcTenantResolver()
    )

    # Do
    result = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    # Assert
    assert result == PlatformCredentialRequired(reason="transient")


@pytest.mark.parametrize(
    "answer",
    [
        {"code": -32603, "message": "broker failed"},
        {
            "status": "ready",
            "kind": "oidc_id_token",
            "token": "t",
            "audience": "https://api.mistral.ai",
            "expiresAt": 2_000_000_000_000,
            "revision": "r",
            "unknownField": "from a newer Desktop",
        },
    ],
    ids=["json-rpc-error", "unknown-field"],
)
@pytest.mark.asyncio
async def test_a_desktop_answer_the_app_server_cannot_use_is_transient(
    process_services: ProcessServices, answer: dict[str, object]
) -> None:
    """*Prepare*: A Desktop connection that answers a credential request with a
      JSON-RPC error, or with a field this app server does not know.
    *Do*: Resolve the credential over the connection.
    *Assert*: It is ``transient``, not a failed connection or a sign-in prompt.
    """
    # Prepare
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
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

    # Do
    if "code" in answer:
        await client.respond(
            request.id,
            error=ProtocolError(code=ProtocolErrorCode.INTERNAL_ERROR, message="x"),
        )
    else:
        await client.respond(request.id, answer)
    result = await resolution

    # Assert
    assert result == PlatformCredentialRequired(reason="transient")
    await incoming.aclose()
    await client.close()


@pytest.mark.asyncio
async def test_desktop_discovery_401_refreshes_the_token_once() -> None:
    """*Prepare*: A Desktop ID token that workspace discovery refuses (401),
      and a Desktop that refreshes it into one discovery accepts.
    *Do*: Resolve the credential.
    *Assert*: Desktop is asked once for a replacement, and that one is sent:
      a refusal is not mistaken for a transient failure.
    """
    # Prepare
    provider, requests, discovery = _refreshing_desktop(refreshed_token_accepted=True)

    # Do
    result = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    # Assert
    assert isinstance(result, PlatformCredentialSnapshot)
    assert [request.rejected_revision for request in requests] == [None, "old"]
    assert discovery.await_count == 2


@pytest.mark.asyncio
async def test_desktop_kind_mismatch_or_opaque_token_is_invalid() -> None:
    access = ConnectionPlatformCredentialProvider(
        _id_token_resolver(_id_token()),
        tenants=OidcTenantResolver(),
        kind="oauth_access_token",
    )
    opaque = ConnectionPlatformCredentialProvider(
        _id_token_resolver("not-a-jwt"),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(AsyncMock(side_effect=AssertionError)),
    )
    target = PlatformCredentialTarget("https://api.mistral.ai/v1")

    assert await access.resolve(target) == PlatformCredentialRequired(reason="invalid")
    assert await opaque.resolve(target) == PlatformCredentialRequired(reason="invalid")


@pytest.mark.asyncio
async def test_invalidation_during_tenant_discovery_discards_that_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def discover(audience: str, id_token: str, workspace_id: str) -> OidcTenant:
        nonlocal calls
        del audience, id_token, workspace_id
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return OidcTenant("stale-org", "stale-ws")
        return OidcTenant("org-2", "ws-2")

    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(_id_token()),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(discover),
    )
    target = PlatformCredentialTarget("https://api.mistral.ai/v1")
    pending = asyncio.create_task(provider.resolve(target))
    await started.wait()
    provider.invalidate()
    joined = asyncio.create_task(provider.resolve(target))
    await asyncio.sleep(0)
    release.set()

    assert await pending == PlatformCredentialRequired(
        reason="reauthentication_required"
    )
    assert await joined == PlatformCredentialRequired(
        reason="reauthentication_required"
    )
    assert provider.cached_principal() is None
    fresh = await provider.resolve(target)
    assert isinstance(fresh, PlatformCredentialSnapshot)
    assert fresh.headers["x-external-organization-id"] == "org-2"
    assert calls == 2


@pytest.mark.asyncio
async def test_desktop_invalidation_rediscovers_the_tenant() -> None:
    discover = AsyncMock(
        side_effect=[OidcTenant("org-1", "ws-1"), OidcTenant("org-2", "ws-2")]
    )
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(_id_token()),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(discover),
    )
    target = PlatformCredentialTarget("https://api.mistral.ai/v1")
    before = await provider.resolve(target)
    provider.invalidate()
    assert provider.cached_principal() is None
    after = await provider.resolve(target)

    assert isinstance(before, PlatformCredentialSnapshot)
    assert isinstance(after, PlatformCredentialSnapshot)
    assert after.headers["x-external-organization-id"] == "org-2"
    assert after.principal != before.principal


@pytest.mark.asyncio
async def test_app_server_accepts_the_desktop_id_token_capability(
    process_services: ProcessServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _id_token()
    monkeypatch.setattr(
        process_services.oidc_tenants,
        "_discover",
        AsyncMock(return_value=OidcTenant("org-1", None)),
    )
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(
        ClientInfo(name="desktop-test", version="1", entrypoint="desktop"),
        ClientCapabilities(
            platform_credentials=PlatformCredentialsCapability(
                version=1, kind="oidc_id_token"
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
    await client.respond(
        request.id,
        {
            "status": "ready",
            "kind": "oidc_id_token",
            "token": token,
            "audience": "https://api.mistral.ai",
            "expiresAt": 2_000_000_000_000,
            "revision": "desktop-id-1",
        },
    )

    resolved = await resolution
    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers == {
        "Authorization": f"Bearer {token}",
        "x-external-organization-id": "org-1",
    }
    await incoming.aclose()
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "https://console.mistral.ai/api/vibe/account-context",
        "https://chat.mistral.ai/api/vibe/managed-config",
    ],
)
async def test_desktop_id_token_reaches_the_registered_web_origins(
    base_url: str,
) -> None:
    # Desktop lends the token for the API audience only; chat and console
    # accept that same token, so the server asks for it and sends it there.
    requests: list[PlatformCredentialsResolveParams] = []
    token = _id_token()
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(token, requests),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(AsyncMock(return_value=OidcTenant("org-1"))),
    )

    resolved = await provider.resolve(PlatformCredentialTarget(base_url))

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers["Authorization"] == f"Bearer {token}"
    assert [request.audience for request in requests] == ["https://api.mistral.ai"]


@pytest.mark.asyncio
async def test_desktop_id_token_of_another_issuer_never_reaches_a_web_origin() -> None:
    def part(value: Mapping[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"iss": "https://auth.globalaegis.net", "aud": "desktop", "sub": "user"}
    foreign = f"{part({'alg': 'RS256'})}.{part(claims)}.signature"
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(foreign),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(AsyncMock(side_effect=AssertionError)),
    )

    resolved = await provider.resolve(
        PlatformCredentialTarget("https://chat.mistral.ai/api/vibe/managed-config")
    )

    assert resolved == PlatformCredentialRequired(reason="invalid")


@pytest.mark.asyncio
async def test_desktop_token_never_reaches_an_unregistered_origin() -> None:
    requests: list[PlatformCredentialsResolveParams] = []
    provider = ConnectionPlatformCredentialProvider(
        _id_token_resolver(_id_token(), requests),
        kind="oidc_id_token",
        tenants=OidcTenantResolver(AsyncMock(return_value=OidcTenant("org-1"))),
    )

    await provider.resolve(PlatformCredentialTarget("https://chat.example.com/x"))

    # Asked for its own origin, which Desktop refuses: nothing is redirected.
    assert [request.audience for request in requests] == ["https://chat.example.com"]


@pytest.mark.asyncio
async def test_a_desktop_connection_starts_a_session_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """Desktop runs on its own sign-in: no API key, no CLI setup beforehand."""
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(
        process_services.oidc_tenants,
        "_discover",
        AsyncMock(return_value=OidcTenant("org-1", "ws-1")),
    )
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    token = _id_token()

    async def broker() -> None:
        async for message in client.incoming():
            if not isinstance(message, ServerRequest):
                continue
            if message.method != "platformCredentials/resolve":
                continue
            params = PlatformCredentialsResolveParams.model_validate(message.params)
            await client.respond(
                message.id,
                PlatformCredentialsSnapshot(
                    status="ready",
                    kind="oidc_id_token",
                    token=token,
                    audience=params.audience,
                    expires_at=2_000_000_000_000,
                    revision="desktop-id-1",
                ),
            )

    broker_task: asyncio.Task[None] | None = None
    session_id: str | None = None
    try:
        await client.initialize(
            ClientInfo(name="desktop-test", version="1", entrypoint="desktop"),
            ClientCapabilities(
                platform_credentials=PlatformCredentialsCapability(
                    version=1, kind="oidc_id_token"
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

        assert session_id
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
async def test_a_desktop_connection_runs_utility_calls_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices, tmp_path: Path
) -> None:
    """Worktree naming and the fast-model probe send Desktop's ID token with
    its tenant headers, as the session's own model calls do, with no API key.
    """
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(
        process_services.oidc_tenants,
        "_discover",
        AsyncMock(return_value=OidcTenant("org-1", "ws-1")),
    )
    completion = FakeUtilityCompletion("pong", "repair-oauth-redirect").install(
        monkeypatch
    )
    # The real probe, which the suite otherwise keeps off the network.
    monkeypatch.setattr(model_probe, "_probe", _REAL_PROBE)
    monkeypatch.setattr(
        model_probe.MODEL_AVAILABILITY, "_source", model_probe.CompletionProbeSource()
    )
    repo = Repo.init(tmp_path, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (tmp_path / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    token = _id_token()

    async def broker() -> None:
        async for message in client.incoming():
            if not isinstance(message, ServerRequest):
                continue
            if message.method != "platformCredentials/resolve":
                continue
            params = PlatformCredentialsResolveParams.model_validate(message.params)
            await client.respond(
                message.id,
                PlatformCredentialsSnapshot(
                    status="ready",
                    kind="oidc_id_token",
                    token=token,
                    audience=params.audience,
                    expires_at=2_000_000_000_000,
                    revision="desktop-id-1",
                ),
            )

    broker_task: asyncio.Task[None] | None = None
    session_id: str | None = None
    try:
        await client.initialize(
            ClientInfo(name="desktop-test", version="1", entrypoint="desktop"),
            ClientCapabilities(
                platform_credentials=PlatformCredentialsCapability(
                    version=1, kind="oidc_id_token"
                )
            ),
        )
        await client.notify("initialized")
        broker_task = asyncio.create_task(broker())

        started = validate_wire(
            SessionStartResponse,
            await client.request(
                "session/start",
                SessionStartParams(
                    agent_config=SessionOptions(
                        cwd=str(tmp_path),
                        workspace_roots=[str(tmp_path)],
                        # Smart approve is what the session-open probe runs for.
                        agent="smart-approve",
                        worktree=AutoWorktreeInput(prompt="fix the oauth redirect"),
                    )
                ),
            ),
        )
        session_id = started.state.session.id
        async with asyncio.timeout(5):
            while len(completion.requests) < 2:
                await asyncio.sleep(0.01)

        calls = {
            request.metadata.get("call_type") if request.metadata else None: request
            for request in completion.requests
        }
        assert set(calls) == {"secondary_call", "worktree_title"}
        for request in calls.values():
            assert request.credential.headers["Authorization"] == f"Bearer {token}"
            assert request.credential.headers["x-external-organization-id"] == "org-1"
            assert request.credential.follow_redirects is False
        # The probe found the fast model, which then named the worktree.
        assert calls["worktree_title"].route.model == FAST_MODEL_CANDIDATES[0].name
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


def _refreshing_desktop(
    *, refreshed_token_accepted: bool
) -> tuple[
    ConnectionPlatformCredentialProvider,
    list[PlatformCredentialsResolveParams],
    AsyncMock,
]:
    """Desktop refreshes a refused ID token, as ``resolveOidcIdToken`` does,
    and answers ``unauthorized_after_refresh`` when the refreshed one is
    refused too. Workspace discovery refuses the first token, and the
    refreshed one unless ``refreshed_token_accepted``.
    """
    old, fresh = _id_token(), _id_token() + "-refreshed"
    requests: list[PlatformCredentialsResolveParams] = []

    async def resolve(
        params: PlatformCredentialsResolveParams,
    ) -> PlatformCredentialsSnapshot | PlatformCredentialsUnavailable:
        requests.append(params)
        if params.rejected_revision == "fresh":
            return PlatformCredentialsUnavailable(
                status="unavailable", reason="unauthorized_after_refresh"
            )
        refreshed = params.rejected_revision == "old"
        return PlatformCredentialsSnapshot(
            status="ready",
            kind="oidc_id_token",
            token=fresh if refreshed else old,
            audience=params.audience,
            expires_at=2_000_000_000_000,
            revision="fresh" if refreshed else "old",
        )

    async def discover(audience: str, token: str, workspace: str) -> OidcTenant:
        del audience, workspace
        if token == fresh and refreshed_token_accepted:
            return OidcTenant("org-1", "ws-1")
        raise OidcUnauthorized("refused")

    discovery = AsyncMock(side_effect=discover)
    provider = ConnectionPlatformCredentialProvider(
        resolve, kind="oidc_id_token", tenants=OidcTenantResolver(discovery)
    )
    return provider, requests, discovery


@pytest.mark.asyncio
async def test_desktop_discovery_401_after_a_refresh_asks_to_update_or_sign_in() -> (
    None
):
    """401 -> Desktop refreshes -> 401 again: Desktop's own answer, the mixed
    reason, is reported; no third token is sent.
    """
    provider, requests, discovery = _refreshing_desktop(refreshed_token_accepted=False)

    result = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert result == PlatformCredentialRequired(reason="unauthorized_after_refresh")
    assert [request.rejected_revision for request in requests] == [None, "old", "fresh"]
    assert discovery.await_count == 2


@pytest.mark.asyncio
async def test_desktop_discovery_401_recovers_when_the_refreshed_token_works() -> None:
    """401 -> Desktop refreshes -> 200: the refreshed token is sent."""
    provider, requests, _ = _refreshing_desktop(refreshed_token_accepted=True)

    result = await provider.resolve(
        PlatformCredentialTarget("https://api.mistral.ai/v1")
    )

    assert isinstance(result, PlatformCredentialSnapshot)
    assert result.revision == "fresh"
    assert [request.rejected_revision for request in requests] == [None, "old"]


@pytest.mark.asyncio
async def test_desktop_reports_its_unauthorized_after_refresh_reason() -> None:
    """A model call's 401 reaches Desktop through ``reject``: Desktop's
    answer for a refreshed token is passed through unchanged.
    """

    async def resolve(
        params: PlatformCredentialsResolveParams,
    ) -> PlatformCredentialsUnavailable:
        del params
        return PlatformCredentialsUnavailable(
            status="unavailable", reason="unauthorized_after_refresh"
        )

    provider = ConnectionPlatformCredentialProvider(
        resolve, kind="oidc_id_token", tenants=OidcTenantResolver()
    )
    target = PlatformCredentialTarget("https://api.mistral.ai/v1")
    observed = PlatformCredentialSnapshot(
        kind="oidc_id_token",
        headers={"Authorization": "Bearer t"},
        cache_namespace="oidc_id_token:desktop_connection",
        revision="fresh",
        source="test",
    ).identity

    result = await provider.reject(
        target, observed=observed, reason="http_unauthorized"
    )

    assert result == PlatformCredentialRequired(reason="unauthorized_after_refresh")
