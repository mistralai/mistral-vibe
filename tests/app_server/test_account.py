from __future__ import annotations

import httpx
from pydantic import ValidationError
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_account_gateway import FakeAccountGateway
from vibe.app_server._account import (
    AccountController,
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    HttpAccountGateway,
    WhoAmIResult,
)
from vibe.app_server.models import (
    AccountActionKind,
    AccountPlanKind,
    AccountStatus,
    AccountView,
)
from vibe.app_server.protocol import AccountReadParams
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.types import Backend


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "plan_type",
        "plan_name",
        "user_plan",
        "title",
        "offers_upgrade",
        "rate_limit_upgrade",
        "teleport_eligible",
    ),
    [
        (AccountPlanKind.CHAT, "FREE", "Free", "Free", True, False, False),
        (
            AccountPlanKind.CHAT,
            "INDIVIDUAL",
            "Pro",
            "[Subscription] Pro",
            False,
            False,
            True,
        ),
        (
            AccountPlanKind.CHAT,
            "EDU",
            "Student",
            "[Subscription] Pro",
            False,
            False,
            True,
        ),
        (
            AccountPlanKind.CHAT,
            "TEAM",
            "Team",
            "[Subscription] Pro",
            False,
            False,
            True,
        ),
        (AccountPlanKind.API, "FREE", "Free API", "Free", True, True, False),
        (
            AccountPlanKind.API,
            "PAY_AS_YOU_GO",
            "PAYG API",
            "[API] Scale plan",
            True,
            True,
            False,
        ),
        (
            AccountPlanKind.MISTRAL_CODE,
            "F",
            "Free Codestral",
            "Mistral Code Free",
            True,
            True,
            False,
        ),
        (
            AccountPlanKind.MISTRAL_CODE,
            "E",
            "Code Enterprise",
            "Mistral Code Enterprise",
            False,
            False,
            False,
        ),
    ],
)
async def test_account_controller_projects_plan_semantics(
    monkeypatch: pytest.MonkeyPatch,
    plan_type: AccountPlanKind,
    plan_name: str,
    user_plan: str | None,
    title: str | None,
    offers_upgrade: bool,
    rate_limit_upgrade: bool,
    teleport_eligible: bool,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    gateway = FakeAccountGateway(WhoAmIResult(plan_type=plan_type, plan_name=plan_name))

    try:
        account = await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert account.status is AccountStatus.READY
    assert account.plan is not None
    assert account.plan.kind is plan_type
    assert account.plan.name == plan_name
    assert account.plan.title == title
    assert agent_loop.user_plan == user_plan
    assert (account.plan_offer is not None) is offers_upgrade
    assert (account.rate_limit_action is not None) is rate_limit_upgrade
    assert account.teleport_eligible is teleport_eligible
    assert (account.teleport_action is None) is teleport_eligible


@pytest.mark.asyncio
async def test_account_controller_projects_switch_key_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    gateway = FakeAccountGateway(
        WhoAmIResult(
            plan_type=AccountPlanKind.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=True,
        )
    )

    try:
        account = await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert account.plan_offer is not None
    assert account.plan_offer.kind is AccountActionKind.SWITCH_API_KEY
    assert account.teleport_action is not None
    assert account.teleport_action.kind is AccountActionKind.SWITCH_API_KEY
    assert not account.teleport_eligible


@pytest.mark.asyncio
async def test_account_controller_does_not_call_gateway_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_loop = build_test_agent_loop()
    gateway = FakeAccountGateway(
        WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="INDIVIDUAL")
    )
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    try:
        account = await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert account.status is AccountStatus.MISSING_KEY
    assert gateway.calls == []
    assert agent_loop.user_plan is None


@pytest.mark.asyncio
async def test_account_controller_skips_non_mistral_active_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    base_config = build_test_vibe_config()
    provider = base_config.get_active_provider().model_copy(
        update={"backend": Backend.GENERIC}
    )
    config = base_config.model_copy(update={"providers": [provider]})
    agent_loop = build_test_agent_loop(config=config)
    agent_loop.set_user_plan("stale")
    gateway = FakeAccountGateway(
        WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="INDIVIDUAL")
    )

    try:
        account = await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert account.status is AccountStatus.UNAVAILABLE
    assert gateway.calls == []
    assert agent_loop.user_plan is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gateway", "status", "has_rate_limit_action"),
    [
        (FakeAccountGateway(unauthorized=True), AccountStatus.UNAUTHORIZED, True),
        (FakeAccountGateway(unavailable=True), AccountStatus.UNAVAILABLE, False),
    ],
)
async def test_account_controller_handles_gateway_failures(
    monkeypatch: pytest.MonkeyPatch,
    gateway: FakeAccountGateway,
    status: AccountStatus,
    has_rate_limit_action: bool,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()

    try:
        account = await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert account.status is status
    assert (account.rate_limit_action is not None) is has_rate_limit_action
    assert agent_loop.user_plan is None


@pytest.mark.asyncio
async def test_account_read_uses_latest_server_config_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_config = build_test_vibe_config(
        console_base_url="https://console.one", vibe_base_url="https://vibe.one"
    )
    provider = base_config.get_active_provider().model_copy(
        update={"api_key_env_var": "FIRST_KEY"}
    )
    config = base_config.model_copy(update={"providers": [provider]})
    monkeypatch.setenv("FIRST_KEY", "first-secret")
    monkeypatch.setenv("SECOND_KEY", "second-secret")
    agent_loop = build_test_agent_loop(config=config)
    runtime_provider = agent_loop.config.get_active_provider()
    gateway = FakeAccountGateway(
        WhoAmIResult(plan_type=AccountPlanKind.API, plan_name="FREE")
    )
    controller = AccountController(agent_loop, gateway)

    try:
        await controller.read()
        await agent_loop.config_orchestrator.set_field(
            "/console_base_url", "https://console.two", target_layer=OverridesLayer.NAME
        )
        await agent_loop.config_orchestrator.set_field(
            "/vibe_base_url", "https://vibe.two", target_layer=OverridesLayer.NAME
        )
        await agent_loop.config_orchestrator.set_field(
            "/providers",
            [runtime_provider.model_copy(update={"api_key_env_var": "SECOND_KEY"})],
            target_layer=OverridesLayer.NAME,
        )
        agent_loop.agent_manager.invalidate_config()
        account = await controller.read()
    finally:
        await agent_loop.aclose()

    assert gateway.calls == [
        ("https://console.one", "first-secret"),
        ("https://console.two", "second-secret"),
    ]
    assert account.plan_offer is not None
    assert account.plan_offer.url == "https://vibe.two/code/extensions?focus=key"


@pytest.mark.asyncio
async def test_account_resource_round_trips_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    gateway = FakeAccountGateway(
        WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="INDIVIDUAL")
    )
    session = await create_test_app_server_session(agent_loop, account_gateway=gateway)

    try:
        account = await session.resources.account.read()
    finally:
        await session.close()

    assert account.status is AccountStatus.READY
    assert account.plan is not None
    assert account.plan.kind is AccountPlanKind.CHAT
    assert agent_loop.user_plan == "Pro"
    request_json = AccountReadParams(session_id="session").model_dump_json()
    assert "server-secret" not in request_json
    assert "server-secret" not in account.model_dump_json()


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
