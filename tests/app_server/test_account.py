from __future__ import annotations

import httpx
from pydantic import ValidationError
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._account import (
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    HttpAccountGateway,
    WhoAmIResult,
    reconcile_tenant_domains,
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


class _ReconcileSpies:
    """Records what reconcile_tenant_domains asks the persistence layer to do."""

    def __init__(self) -> None:
        self.provider_calls: list[str] = []  # captured api_base per call
        self.vibe_calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from vibe.app_server import _account as account_module

        async def _fake_apply_provider_to_config(
            _orchestrator, provider, *, reason: str
        ) -> bool:
            self.provider_calls.append(provider.api_base)
            return True

        async def _fake_apply_vibe_base_url(
            _orchestrator, vibe_base_url, *, reason: str
        ) -> bool:
            self.vibe_calls.append(vibe_base_url)
            return True

        monkeypatch.setattr(
            account_module, "apply_provider_to_config", _fake_apply_provider_to_config
        )
        monkeypatch.setattr(
            account_module, "apply_vibe_base_url", _fake_apply_vibe_base_url
        )


@pytest.mark.asyncio
async def test_reconcile_tenant_domains_preserves_api_base_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preserve_api_base flag skips the api_base heal but not the chat heal."""
    spies = _ReconcileSpies()
    spies.install(monkeypatch)

    from vibe.core.config import DEFAULT_PROVIDERS

    provider = DEFAULT_PROVIDERS[0].model_copy(
        update={"api_base": "https://connector.acme:443/v1", "preserve_api_base": True}
    )
    config = build_test_vibe_config(providers=[provider])
    await reconcile_tenant_domains(
        FakeConfigOrchestrator(config),
        WhoAmIResult(
            plan_type=AccountPlanKind.API,
            plan_name="FREE",
            api_base="https://api.tenant.acme",
            vibe_base="https://chat.tenant.acme",
        ),
        provider_name=config.get_active_provider().name,
    )

    assert spies.provider_calls == []  # api heal opted out
    assert spies.vibe_calls == ["https://chat.tenant.acme"]  # chat still healed
