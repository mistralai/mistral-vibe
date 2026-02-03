from __future__ import annotations

import httpx
import pytest
import respx

from vibe.cli.plan_offer.adapters.http_whoami_gateway import HttpWhoAmIGateway
from vibe.cli.plan_offer.ports.whoami_gateway import (
    WhoAmIGatewayError,
    WhoAmIGatewayUnauthorized,
    WhoAmIResponse,
)


@pytest.mark.asyncio
async def test_returns_plan_flags(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("http://test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "is_pro_plan": True,
                "advertise_pro_plan": False,
                "prompt_switching_to_pro_plan": False,
            },
        )
    )

    gateway = HttpWhoAmIGateway(base_url="http://test")
    response = await gateway.whoami("api-key")

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer api-key"
    assert response.is_pro_plan is True
    assert response.advertise_pro_plan is False
    assert response.prompt_switching_to_pro_plan is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_raises_on_unauthorized(
    respx_mock: respx.MockRouter, status_code: int
) -> None:
    respx_mock.get("http://test/api/vibe/whoami").mock(
        return_value=httpx.Response(status_code, json={"error": "unauthorized"})
    )

    gateway = HttpWhoAmIGateway(base_url="http://test")

    with pytest.raises(WhoAmIGatewayUnauthorized):
        await gateway.whoami("bad-key")


@pytest.mark.asyncio
async def test_raises_on_non_success(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("http://test/api/vibe/whoami").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )

    gateway = HttpWhoAmIGateway(base_url="http://test")

    with pytest.raises(WhoAmIGatewayError):
        await gateway.whoami("api-key")


@pytest.mark.asyncio
async def test_incomplete_payload_defaults_missing_flags_to_false(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("http://test/api/vibe/whoami").mock(
        return_value=httpx.Response(200, json={"is_pro_plan": True})
    )

    gateway = HttpWhoAmIGateway(base_url="http://test")
    response = await gateway.whoami("api-key")
    assert response == WhoAmIResponse(
        is_pro_plan=True, advertise_pro_plan=False, prompt_switching_to_pro_plan=False
    )


@pytest.mark.asyncio
async def test_wraps_request_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("http://test/api/vibe/whoami").mock(
        side_effect=httpx.ConnectError("boom")
    )

    gateway = HttpWhoAmIGateway(base_url="http://test")

    with pytest.raises(WhoAmIGatewayError):
        await gateway.whoami("api-key")


@pytest.mark.asyncio
async def test_parses_boolean_strings(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("http://test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "is_pro_plan": "true",
                "advertise_pro_plan": "false",
                "prompt_switching_to_pro_plan": "true",
            },
        )
    )

    gateway = HttpWhoAmIGateway(base_url="http://test")
    response = await gateway.whoami("api-key")
    assert response == WhoAmIResponse(
        is_pro_plan=True, advertise_pro_plan=False, prompt_switching_to_pro_plan=True
    )


@pytest.mark.asyncio
async def test_raises_on_invalid_boolean_string(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("http://test/api/vibe/whoami").mock(
        return_value=httpx.Response(200, json={"is_pro_plan": "yes"})
    )

    gateway = HttpWhoAmIGateway(base_url="http://test")

    with pytest.raises(WhoAmIGatewayError):
        await gateway.whoami("api-key")
