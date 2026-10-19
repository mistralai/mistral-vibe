from __future__ import annotations

import httpx
import pytest

from vibe.app_server._identity import (
    HttpIdentityGateway,
    IdentityGatewayUnauthorized,
    IdentityGatewayUnavailable,
    IdentityResult,
)
from vibe.app_server.models import IdentityView


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
        base_url="https://api.mistral.ai/v1", api_key="server-secret"
    )

    assert result.id == "user-1"
    assert result.workspace is not None
    assert result.workspace.name == "Analytical Engine"
    assert route.calls.last.request.headers["Authorization"] == "Bearer server-secret"


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
        base_url="https://api.mistral.ai/v1", api_key="server-secret"
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
            base_url="https://api.mistral.ai/v1", api_key="server-secret"
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
            base_url="https://api.mistral.ai/v1", api_key="server-secret"
        )
