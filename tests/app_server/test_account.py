from __future__ import annotations

import httpx
from pydantic import ValidationError
import pytest

from vibe.app_server._account import (
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    HttpAccountGateway,
    WhoAmIResult,
)
from vibe.app_server.models import AccountPlanKind, AccountView
from vibe.app_server.protocol import AccountReadParams


@pytest.mark.asyncio
async def test_http_account_gateway_parses_strict_response(respx_mock) -> None:
    route = respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_type": "CHAT",
                "plan_name": "INDIVIDUAL",
                "prompt_switching_to_pro_plan": False,
            },
        )
    )

    result = await HttpAccountGateway().read(
        base_url="https://console.test", api_key="server-secret"
    )

    assert result == WhoAmIResult(
        plan_type=AccountPlanKind.CHAT,
        plan_name="INDIVIDUAL",
        prompt_switching_to_pro_plan=False,
    )
    assert route.calls.last.request.headers["Authorization"] == "Bearer server-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_http_account_gateway_maps_unauthorized_status(
    respx_mock, status_code: int
) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(status_code)
    )

    with pytest.raises(AccountGatewayUnauthorized):
        await HttpAccountGateway().read(
            base_url="https://console.test", api_key="server-secret"
        )


@pytest.mark.asyncio
async def test_http_account_gateway_maps_server_error_to_unavailable(
    respx_mock,
) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(AccountGatewayUnavailable):
        await HttpAccountGateway().read(
            base_url="https://console.test", api_key="server-secret"
        )


@pytest.mark.asyncio
async def test_http_account_gateway_ignores_extra_response_fields(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200, json={"plan_type": "CHAT", "plan_name": "FREE", "legacy": True}
        )
    )

    result = await HttpAccountGateway().read(
        base_url="https://console.test", api_key="server-secret"
    )

    assert result == WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="FREE")


@pytest.mark.asyncio
async def test_http_account_gateway_rejects_unknown_plan_type(respx_mock) -> None:
    respx_mock.get("https://console.test/api/vibe/whoami").mock(
        return_value=httpx.Response(
            200, json={"plan_type": "NEW_PLAN", "plan_name": "future"}
        )
    )

    with pytest.raises(AccountGatewayUnavailable):
        await HttpAccountGateway().read(
            base_url="https://console.test", api_key="server-secret"
        )


def test_account_wire_models_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AccountView.model_validate({"status": "ready", "legacy": True})
    with pytest.raises(ValidationError):
        AccountReadParams.model_validate({
            "sessionId": "session",
            "apiKey": "client-secret",
        })
