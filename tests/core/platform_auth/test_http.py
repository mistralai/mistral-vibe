from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import httpx2
import pytest

from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    MistralPlatformHTTPClient,
    PlatformCredentialHeaderConflict,
    PlatformCredentialProvider,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
    PlatformCredentialUnavailable,
    UntrustedPlatformDestination,
)
from vibe.utils.http import VibeAsyncHTTPClient


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
    *,
    base_url: str = "https://api.mistral.ai/v1",
    api_key: str = "secret",
) -> MistralPlatformHTTPClient:
    return MistralPlatformHTTPClient(
        base_url=base_url,
        credentials=ApiKeyPlatformCredentialProvider.from_api_key(api_key),
        transport=httpx2.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_plain_http_client_does_not_attach_ambient_platform_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx2.Request] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "ambient-secret")

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200)

    async with VibeAsyncHTTPClient(transport=httpx2.MockTransport(handler)) as client:
        await client.get("https://api.mistral.ai/v1/models")

    assert "Authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_injects_authorization_only_into_the_bound_platform_path() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200)

    async with _client(handler) as client:
        response = await client.get("users/me")

    assert response.status_code == 200
    assert seen[0].url == "https://api.mistral.ai/v1/users/me"
    assert seen[0].headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_uses_a_replaced_credential_without_reconstructing_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_authorization: list[str] = []
    credentials = ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY")
    monkeypatch.setenv("MISTRAL_API_KEY", "first-secret")

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_authorization.append(request.headers["Authorization"])
        return httpx2.Response(200)

    async with MistralPlatformHTTPClient(
        base_url="https://api.mistral.ai/v1",
        credentials=credentials,
        transport=httpx2.MockTransport(handler),
    ) as client:
        await client.get("users/me")
        monkeypatch.setenv("MISTRAL_API_KEY", "replacement-secret")
        await client.get("users/me")

    assert seen_authorization == ["Bearer first-secret", "Bearer replacement-secret"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://untrusted.example/v1/users/me",
        "https://api.mistral.ai/v2/users/me",
        "https://api.mistral.ai/v1/%2e%2e/private",
        "https://api.mistral.ai/v1/%252e%252e/private",
        "https://api.mistral.ai/v1/%2525252e%2525252e/private",
        "https://api.mistral.ai/v1/users%2f..%2fprivate",
        "https://api.mistral.ai/v1/users%5c..%5cprivate",
    ],
)
async def test_refuses_destinations_outside_the_bound_platform_path(url: str) -> None:
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200)

    async with _client(handler) as client:
        with pytest.raises(UntrustedPlatformDestination):
            await client.get(url)

    assert calls == 0


@pytest.mark.asyncio
async def test_explicit_port_zero_is_not_treated_as_the_default_port() -> None:
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200)

    async with _client(handler, base_url="https://api.mistral.ai:0/v1") as client:
        with pytest.raises(UntrustedPlatformDestination):
            await client.get("https://api.mistral.ai/v1/users/me")

    assert calls == 0


@pytest.mark.asyncio
async def test_does_not_follow_redirects_or_send_credentials_to_the_location() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(302, headers={"Location": "https://untrusted.example/"})

    async with _client(handler) as client:
        response = await client.get("users/me")

    assert response.status_code == 302
    assert len(seen) == 1
    assert seen[0].headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_rejects_per_request_redirect_and_authentication_overrides() -> None:
    async with _client(lambda _request: httpx2.Response(200)) as client:
        with pytest.raises(ValueError, match="does not follow redirects"):
            await client.get("users/me", follow_redirects=True)
        with pytest.raises(ValueError, match="owns request authentication"):
            await client.get("users/me", auth=None)


def test_client_owns_tls_verification() -> None:
    with pytest.raises(ValueError, match="owns TLS verification"):
        MistralPlatformHTTPClient(
            base_url="https://api.mistral.ai/v1",
            credentials=ApiKeyPlatformCredentialProvider.from_api_key("secret"),
            verify=False,
        )


@pytest.mark.asyncio
async def test_rejects_a_caller_supplied_credential_header() -> None:
    async with _client(lambda _request: httpx2.Response(200)) as client:
        with pytest.raises(PlatformCredentialHeaderConflict):
            await client.get(
                "users/me", headers={"Authorization": "Bearer caller-secret"}
            )


@pytest.mark.asyncio
async def test_missing_credential_fails_before_network_io() -> None:
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200)

    async with _client(handler, api_key="") as client:
        with pytest.raises(PlatformCredentialUnavailable) as error:
            await client.get("users/me")

    assert error.value.required.reason == "missing"
    assert calls == 0


@pytest.mark.asyncio
async def test_unauthorized_revision_is_rejected_without_replaying_the_request() -> (
    None
):
    calls = 0
    credentials = ApiKeyPlatformCredentialProvider.from_api_key("rejected-secret")

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(401)

    async with MistralPlatformHTTPClient(
        base_url="https://api.mistral.ai/v1",
        credentials=credentials,
        transport=httpx2.MockTransport(handler),
    ) as client:
        assert (await client.get("users/me")).status_code == 401
        with pytest.raises(PlatformCredentialUnavailable) as error:
            await client.get("users/me")

    assert error.value.required.reason == "rejected"
    assert calls == 1


@pytest.mark.asyncio
async def test_unauthorized_rejection_identifies_the_complete_snapshot() -> None:
    snapshot = PlatformCredentialSnapshot(
        kind="oauth_access_token",
        headers={"Authorization": "Bearer access-token"},
        cache_namespace="oauth:desktop-connection",
        revision="shared-revision",
    )
    credentials = AsyncMock(spec=PlatformCredentialProvider)
    credentials.resolve.return_value = snapshot

    async with MistralPlatformHTTPClient(
        base_url="https://api.mistral.ai/v1",
        credentials=credentials,
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(401)),
    ) as client:
        assert (await client.get("users/me")).status_code == 401

    credentials.reject.assert_awaited_once_with(
        PlatformCredentialTarget(base_url="https://api.mistral.ai/v1"),
        observed=snapshot.identity,
        reason="http_unauthorized",
    )


@pytest.mark.asyncio
async def test_forbidden_response_does_not_reject_the_credential() -> None:
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(403)

    async with _client(handler) as client:
        assert (await client.get("users/me")).status_code == 403
        assert (await client.get("users/me")).status_code == 403

    assert calls == 2


@pytest.mark.asyncio
async def test_accepts_an_enterprise_platform_base_url() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200)

    async with _client(
        handler, base_url="https://mistral.enterprise.example/custom/v1"
    ) as client:
        await client.get("users/me")

    assert seen[0].url == "https://mistral.enterprise.example/custom/v1/users/me"
    assert seen[0].headers["Authorization"] == "Bearer secret"


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://api.mistral.ai/v1",
        "https://user:password@api.mistral.ai/v1",
        "https://api.mistral.ai/v1?destination=other",
        "https://api.mistral.ai/v1#fragment",
        "https://api.mistral.ai:invalid/v1",
        "https://api.mistral.ai/v1/%2e%2e/private",
        "https://api.mistral.ai/v1/%2525252e%2525252e/private",
        "https://api.mistral.ai/v1/users%2fprivate",
    ],
)
def test_rejects_untrusted_provider_config_base_urls(base_url: str) -> None:
    # ProviderConfig.api_base is a plain string populated by config layers. This
    # boundary must validate it before using it to select where secrets are sent.
    with pytest.raises(ValueError):
        _client(lambda _request: httpx2.Response(200), base_url=base_url)
