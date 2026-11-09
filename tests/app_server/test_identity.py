from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from vibe.app_server._identity import (
    HttpIdentityGateway,
    IdentityGatewayUnauthorized,
    IdentityGatewayUnavailable,
    IdentityResult,
)
from vibe.app_server.models import IdentityView
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
    PlatformCredentialSnapshot,
)


@pytest.mark.parametrize(
    ("first_name", "last_name", "email", "expected"),
    [
        ("Ada", "Lovelace", "ada@example.com", "Ada Lovelace"),
        ("Ada", None, "ada@example.com", "Ada"),
        (None, "Lovelace", "ada@example.com", "ada@example.com"),
        (None, None, "ada@example.com", "ada@example.com"),
        (None, None, None, None),
    ],
)
def test_identity_view_name_resolution(
    first_name: str | None,
    last_name: str | None,
    email: str | None,
    expected: str | None,
) -> None:
    view = IdentityView(
        id="user-1", email=email, first_name=first_name, last_name=last_name
    )
    assert view.name == expected


@pytest.mark.asyncio
async def test_http_identity_gateway_parses_response(respx_mock) -> None:
    route = respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "user-1",
                "email": "ada@example.com",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "workspace": {"id": "ws-1", "name": "Analytical Engine"},
                "organization": {"id": "org-1", "name": "Mistral"},
            },
        )
    )

    result = await HttpIdentityGateway().read(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("server-secret"),
    )

    assert result.id == "user-1"
    assert result.workspace is not None
    assert result.workspace.name == "Analytical Engine"
    assert route.calls.last.request.headers["Authorization"] == "Bearer server-secret"


@pytest.mark.asyncio
async def test_http_identity_gateway_accepts_an_expiring_oauth_snapshot(
    respx_mock,
) -> None:
    route = respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(200, json={"id": "user-1"})
    )
    credentials = AsyncMock(spec=PlatformCredentialProvider)
    credentials.resolve.return_value = PlatformCredentialSnapshot(
        kind="oauth_access_token",
        headers={"Authorization": "Bearer oauth-access-token"},
        cache_namespace="oauth:desktop-connection",
        revision="oauth:revision-1",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        source="desktop",
    )

    result = await HttpIdentityGateway().read(
        base_url="https://api.mistral.ai/v1", credentials=credentials
    )

    assert result.id == "user-1"
    assert route.calls.last.request.headers["Authorization"] == (
        "Bearer oauth-access-token"
    )
    credentials.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_identity_gateway_ignores_extra_response_fields(respx_mock) -> None:
    respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "user-1",
                "email": "ada@example.com",
                "api_key": "leaked-secret",
            },
        )
    )

    result = await HttpIdentityGateway().read(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("server-secret"),
    )

    assert result == IdentityResult(id="user-1", email="ada@example.com")
    assert "leaked-secret" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_http_identity_gateway_maps_unauthorized_status(
    respx_mock, status_code: int
) -> None:
    respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(status_code)
    )

    with pytest.raises(IdentityGatewayUnauthorized):
        await HttpIdentityGateway().read(
            base_url="https://api.mistral.ai/v1",
            credentials=ApiKeyPlatformCredentialProvider.from_api_key("server-secret"),
        )


@pytest.mark.asyncio
async def test_http_identity_gateway_maps_server_error_to_unavailable(
    respx_mock,
) -> None:
    respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(IdentityGatewayUnavailable):
        await HttpIdentityGateway().read(
            base_url="https://api.mistral.ai/v1",
            credentials=ApiKeyPlatformCredentialProvider.from_api_key("server-secret"),
        )


@pytest.mark.asyncio
async def test_http_identity_gateway_maps_malformed_response_to_unavailable(
    respx_mock,
) -> None:
    # Producer contract: dashboard/users/public/schemas.py::UserIdentity requires
    # a string id. Exercise the client-side guard with a response that violates it.
    respx_mock.get("https://api.mistral.ai/v1/users/me").mock(
        return_value=httpx.Response(200, json={"id": 123})
    )

    with pytest.raises(IdentityGatewayUnavailable, match="Invalid identity response"):
        await HttpIdentityGateway().read(
            base_url="https://api.mistral.ai/v1",
            credentials=ApiKeyPlatformCredentialProvider.from_api_key("server-secret"),
        )


@pytest.mark.asyncio
async def test_http_identity_gateway_maps_transport_failure_to_unavailable(
    respx_mock,
) -> None:
    request = httpx.Request("GET", "https://api.mistral.ai/v1/users/me")
    respx_mock.get(request.url).mock(
        side_effect=httpx.ConnectError("identity unavailable", request=request)
    )

    with pytest.raises(IdentityGatewayUnavailable):
        await HttpIdentityGateway().read(
            base_url="https://api.mistral.ai/v1",
            credentials=ApiKeyPlatformCredentialProvider.from_api_key("server-secret"),
        )
