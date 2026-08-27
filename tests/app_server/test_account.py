from __future__ import annotations

import asyncio

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
    reconcile_tenant_domains,
)
from vibe.app_server.models import (
    AccountActionKind,
    AccountPlanKind,
    AccountStatus,
    AccountView,
)
from vibe.app_server.protocol import AccountReadParams
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.experiments.models import ExperimentAttributes
from vibe.core.types import Backend
from vibe.setup.auth.whoami import load_cached_whoami, store_cached_whoami


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
async def test_account_controller_no_plan_data_when_no_mistral_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Non-Mistral active model AND no Mistral provider configured at all — not
    # our user. No whoami call, no account UI, and the NO_PLAN_DATA sentinel
    # (not the stale value, not null) so telemetry tells this apart from a
    # failed fetch.
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
    assert account.plan is None
    assert gateway.calls == []
    assert agent_loop.user_plan == "NO_PLAN_DATA"


@pytest.mark.asyncio
async def test_account_controller_fetches_plan_for_non_mistral_active_with_mistral_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Core of the change: the active model is a third-party backend, but a
    # Mistral provider with a key is still configured. We MUST call whoami and
    # capture the real plan for telemetry / GrowthBook — while suppressing the
    # account UI (no plan shown) because the active model is not Mistral.
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    base_config = build_test_vibe_config()
    active = base_config.get_active_provider()
    generic = active.model_copy(update={"backend": Backend.GENERIC})
    mistral = active.model_copy(
        update={"name": "mistral-extra", "backend": Backend.MISTRAL}
    )
    config = base_config.model_copy(update={"providers": [generic, mistral]})
    agent_loop = build_test_agent_loop(config=config)
    gateway = FakeAccountGateway(
        WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="INDIVIDUAL")
    )

    try:
        account = await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    # UI suppressed for the non-Mistral active model ...
    assert account.status is AccountStatus.UNAVAILABLE
    assert account.plan is None
    # ... but whoami WAS called and telemetry captured the real plan.
    assert len(gateway.calls) == 1
    assert agent_loop.user_plan == "Pro"


@pytest.mark.asyncio
async def test_account_read_warms_cross_session_whoami_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A successful live account fetch writes through to the user-scoped on-disk
    # cache so the next session's experiments path can read it without another
    # round-trip.
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    result = WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="INDIVIDUAL")
    gateway = FakeAccountGateway(result)

    try:
        await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert load_cached_whoami("server-secret") == result


@pytest.mark.asyncio
async def test_account_read_reconciles_manager_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The experiments path may have stamped the manager snapshot from a stale
    # disk-cache hit (plan=FREE). The account controller's live fetch must
    # reconcile the whoami-derived fields (plan/customer/org-kind) AND user_plan
    # so they never diverge — while preserving identity-derived fields.
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    agent_loop.experiment_manager.set_attributes(
        ExperimentAttributes(
            userId="u1",
            organizationId="org-1",
            entrypoint="cli",
            agent_version="0",
            os="darwin",
            planType="chat",
            planName="FREE",
        )
    )
    gateway = FakeAccountGateway(
        WhoAmIResult(
            plan_type=AccountPlanKind.API,
            plan_name="PAY_AS_YOU_GO",
            customer_id="cust-1",
            organization_kind="C",
        )
    )

    try:
        await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    attrs = agent_loop.experiment_manager.attributes()
    assert attrs is not None
    # whoami-derived fields updated to the live result ...
    assert attrs.planType == "api"
    assert attrs.planName == "PAY_AS_YOU_GO"
    assert attrs.customerId == "cust-1"
    assert attrs.organizationKind == "C"
    # ... identity-derived fields preserved.
    assert attrs.organizationId == "org-1"
    assert attrs.userId == "u1"
    assert agent_loop.user_plan == "PAYG API"


@pytest.mark.asyncio
async def test_apply_account_whoami_wins_over_in_flight_experiments() -> None:
    # Race guard (Bugbot): a background experiments/plan resolve that finishes
    # with a stale value must not clobber the account's live reconcile.
    # apply_account_whoami awaits the in-flight task first, then writes last.
    agent_loop = build_test_agent_loop()

    async def stale_experiments() -> None:
        # Simulates the experiments path committing a snapshot from a stale hit.
        agent_loop.experiment_manager.set_attributes(
            ExperimentAttributes(
                userId="u1",
                organizationId="org-1",
                entrypoint="cli",
                agent_version="0",
                os="darwin",
                planType="chat",
                planName="FREE",
            )
        )
        agent_loop.set_user_plan("Free")

    agent_loop._experiments_task = asyncio.create_task(stale_experiments())
    try:
        await agent_loop.apply_account_whoami(
            console_base_url="https://console.test",
            api_key="server-secret",
            whoami=WhoAmIResult(
                plan_type=AccountPlanKind.API,
                plan_name="PAY_AS_YOU_GO",
                customer_id="cust-1",
                organization_kind="C",
            ),
        )
    finally:
        await agent_loop.aclose()

    attrs = agent_loop.experiment_manager.attributes()
    assert attrs is not None
    # The account's live values won, despite the stale task finishing first.
    assert attrs.planType == "api"
    assert attrs.planName == "PAY_AS_YOU_GO"
    assert attrs.organizationId == "org-1"  # identity preserved from the snapshot
    assert agent_loop.user_plan == "PAYG API"


@pytest.mark.asyncio
async def test_account_read_unauthorized_clears_stale_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A rejected credential (401/403) must drop any cached plan: user_plan -> None,
    # manager plan fields -> None, and the disk cache entry removed — otherwise a
    # stale plan keeps flowing for the TTL, contradicting null=lookup-failed.
    monkeypatch.setenv("MISTRAL_API_KEY", "server-secret")
    agent_loop = build_test_agent_loop()
    agent_loop.experiment_manager.set_attributes(
        ExperimentAttributes(
            userId="u1",
            organizationId="org-1",
            entrypoint="cli",
            agent_version="0",
            os="darwin",
            planType="chat",
            planName="INDIVIDUAL",
        )
    )
    agent_loop.set_user_plan("Pro")
    store_cached_whoami(
        "server-secret",
        WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="INDIVIDUAL"),
    )
    gateway = FakeAccountGateway(unauthorized=True)

    try:
        account = await AccountController(agent_loop, gateway).read()
    finally:
        await agent_loop.aclose()

    assert account.status is AccountStatus.UNAUTHORIZED
    # Plan telemetry cleared to the failure signal ...
    assert agent_loop.user_plan is None
    attrs = agent_loop.experiment_manager.attributes()
    assert attrs is not None
    assert attrs.planName is None
    assert attrs.planType is None
    assert attrs.organizationId == "org-1"  # identity-derived fields preserved
    # ... and the stale disk cache entry is gone.
    assert load_cached_whoami("server-secret") is None


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
async def test_reconcile_tenant_domains_noop_when_domains_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spies = _ReconcileSpies()
    spies.install(monkeypatch)
    agent_loop = build_test_agent_loop()
    try:
        await reconcile_tenant_domains(
            agent_loop.config_orchestrator,
            WhoAmIResult(plan_type=AccountPlanKind.API, plan_name="FREE"),
        )
    finally:
        await agent_loop.aclose()

    assert spies.provider_calls == []
    assert spies.vibe_calls == []


@pytest.mark.asyncio
async def test_reconcile_tenant_domains_noop_when_values_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spies = _ReconcileSpies()
    spies.install(monkeypatch)

    from vibe.core.config import DEFAULT_PROVIDERS

    provider = DEFAULT_PROVIDERS[0].model_copy(
        update={"api_base": "https://api.tenant.corp/v1"}
    )
    config = build_test_vibe_config(
        vibe_base_url="https://chat.tenant.corp", providers=[provider]
    )
    agent_loop = build_test_agent_loop(config=config)
    try:
        await reconcile_tenant_domains(
            agent_loop.config_orchestrator,
            WhoAmIResult(
                plan_type=AccountPlanKind.API,
                plan_name="FREE",
                api_base="https://api.tenant.corp",
                vibe_base="https://chat.tenant.corp",
            ),
        )
    finally:
        await agent_loop.aclose()

    assert spies.provider_calls == []
    assert spies.vibe_calls == []


@pytest.mark.asyncio
async def test_reconcile_tenant_domains_patches_api_base_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spies = _ReconcileSpies()
    spies.install(monkeypatch)
    agent_loop = build_test_agent_loop()
    try:
        await reconcile_tenant_domains(
            agent_loop.config_orchestrator,
            WhoAmIResult(
                plan_type=AccountPlanKind.API,
                plan_name="FREE",
                api_base="https://api.tenant.corp",
            ),
        )
    finally:
        await agent_loop.aclose()

    assert spies.provider_calls == ["https://api.tenant.corp/v1"]
    assert spies.vibe_calls == []


@pytest.mark.asyncio
async def test_reconcile_tenant_domains_patches_chat_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spies = _ReconcileSpies()
    spies.install(monkeypatch)
    agent_loop = build_test_agent_loop()
    try:
        await reconcile_tenant_domains(
            agent_loop.config_orchestrator,
            WhoAmIResult(
                plan_type=AccountPlanKind.API,
                plan_name="FREE",
                vibe_base="https://chat.tenant.corp",
            ),
        )
    finally:
        await agent_loop.aclose()

    assert spies.provider_calls == []
    assert spies.vibe_calls == ["https://chat.tenant.corp"]


@pytest.mark.asyncio
async def test_reconcile_tenant_domains_still_reconciles_chat_when_no_active_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: earlier code bailed out of the whole function when
    ``get_active_provider`` raised, silently skipping the independent chat
    reconciliation. The two branches must remain independent.
    """
    spies = _ReconcileSpies()
    spies.install(monkeypatch)
    agent_loop = build_test_agent_loop()
    try:
        orchestrator = agent_loop.config_orchestrator

        def _raise_no_active_provider(self):
            raise ValueError("no active provider")

        monkeypatch.setattr(
            type(orchestrator.config), "get_active_provider", _raise_no_active_provider
        )
        await reconcile_tenant_domains(
            orchestrator,
            WhoAmIResult(
                plan_type=AccountPlanKind.API,
                plan_name="FREE",
                api_base="https://api.tenant.corp",
                vibe_base="https://chat.tenant.corp",
            ),
        )
    finally:
        await agent_loop.aclose()

    assert spies.provider_calls == []  # api branch skipped
    assert spies.vibe_calls == ["https://chat.tenant.corp"]  # chat still ran


@pytest.mark.asyncio
async def test_reconcile_tenant_domains_rejects_non_https_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-HTTPS or path-traversal URLs from /whoami must not be persisted."""
    spies = _ReconcileSpies()
    spies.install(monkeypatch)
    agent_loop = build_test_agent_loop()
    try:
        await reconcile_tenant_domains(
            agent_loop.config_orchestrator,
            WhoAmIResult(
                plan_type=AccountPlanKind.API,
                plan_name="FREE",
                api_base="http://api.tenant.corp",
                vibe_base="http://chat.tenant.corp",
            ),
        )
    finally:
        await agent_loop.aclose()

    assert spies.provider_calls == []
    assert spies.vibe_calls == []
