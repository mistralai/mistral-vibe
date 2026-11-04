from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_billing_gateway import FakeBillingGateway
from vibe.app_server._account import AccountController
from vibe.app_server._billing import (
    BillingRead,
    BudgetDetail,
    BudgetResult,
    HttpBillingGateway,
    SubscriptionResult,
)
from vibe.app_server.models import AccountBudgetView, AccountPlanKind, AccountStatus
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.setup.auth.whoami import WhoAmIResult

_API = "https://api.test/v1"
_SECRET = ApiKeyPlatformCredentialProvider.from_api_key("secret")


async def _sent_bearer(credentials: PlatformCredentialProvider, url: str) -> str:
    snapshot = await credentials.resolve(PlatformCredentialTarget(url))
    assert isinstance(snapshot, PlatformCredentialSnapshot)
    return snapshot.headers["Authorization"]


# Producer: dashboard billing_api_public (BudgetOUTJSON / SubscriptionOUTJSON).
_SUBSCRIPTION = {
    "plan_name": "INDIVIDUAL",
    "payg_enabled": True,
    "vibe_payg_enabled": True,
    "enterprise": False,
}
_BUDGET = {
    "vibe_budget": {
        "usage_percentage": 42.5,
        "initial_budget": 300.0,
        "currency": "EUR",
        "reset_at": "2026-11-01T00:00:00Z",
        "payg_enabled": False,
    },
    "api_budget": {
        "usage_percentage": 3.0,
        "initial_budget": 50.0,
        "currency": "EUR",
        "reset_at": "2026-11-01T00:00:00Z",
    },
}


@pytest.mark.asyncio
async def test_http_billing_gateway_reads_both_routes_on_the_api_base(
    respx_mock,
) -> None:
    subscription = respx_mock.get(f"{_API}/billing/subscription").mock(
        return_value=httpx.Response(200, json=_SUBSCRIPTION)
    )
    budget = respx_mock.get(f"{_API}/billing/budget").mock(
        return_value=httpx.Response(200, json=_BUDGET)
    )

    read = await HttpBillingGateway().read(api_base=_API, credentials=_SECRET)

    assert read.subscription == SubscriptionResult(
        plan_name="INDIVIDUAL", vibe_payg_enabled=True
    )
    assert read.budget is not None and read.budget.vibe_budget is not None
    assert read.budget.vibe_budget.usage_percentage == 42.5
    assert read.budget.vibe_budget.payg_enabled is False
    for route in (subscription, budget):
        assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [403, 404, 500])
async def test_http_billing_gateway_keeps_a_route_that_answers(
    respx_mock, status_code: int
) -> None:
    """A member without the usage permission gets 403 on /budget only."""
    respx_mock.get(f"{_API}/billing/subscription").mock(
        return_value=httpx.Response(200, json=_SUBSCRIPTION)
    )
    respx_mock.get(f"{_API}/billing/budget").mock(
        return_value=httpx.Response(status_code)
    )

    read = await HttpBillingGateway().read(api_base=_API, credentials=_SECRET)

    assert read.subscription is not None
    assert read.budget is None


@pytest.mark.asyncio
async def test_http_billing_gateway_tolerates_older_and_broken_bodies(
    respx_mock,
) -> None:
    respx_mock.get(f"{_API}/billing/subscription").mock(
        return_value=httpx.Response(
            200, json={"plan_name": "TEAM", "vibe_payg_enabled": None}
        )
    )
    respx_mock.get(f"{_API}/billing/budget").mock(
        return_value=httpx.Response(200, text="not json")
    )

    read = await HttpBillingGateway().read(api_base=_API, credentials=_SECRET)

    assert read.subscription == SubscriptionResult(plan_name="TEAM")
    assert read.budget is None


class _UserToken:
    """A signed-in user's ID token with its tenant, as Desktop or the CLI
    session resolves it.
    """

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        del target
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers={
                "Authorization": "Bearer id-token",
                "x-external-organization-id": "org",
            },
            cache_namespace="oidc_id_token:test",
            revision="revision",
        )

    async def reject(
        self, target: PlatformCredentialTarget, **_: object
    ) -> PlatformCredentialResult:
        return PlatformCredentialRequired(reason="rejected")


@pytest.mark.asyncio
async def test_http_billing_gateway_signs_in_with_the_connection_user_token(
    respx_mock,
) -> None:
    """A signed-in connection reads billing with its own token and tenant."""
    route = respx_mock.get(f"{_API}/billing/subscription").mock(
        return_value=httpx.Response(200, json=_SUBSCRIPTION)
    )
    respx_mock.get(f"{_API}/billing/budget").mock(return_value=httpx.Response(403))

    read = await HttpBillingGateway().read(api_base=_API, credentials=_UserToken())

    assert read.subscription is not None
    sent = route.calls.last.request.headers
    assert sent["Authorization"] == "Bearer id-token"
    assert sent["x-external-organization-id"] == "org"


@pytest.mark.asyncio
async def test_http_billing_gateway_can_skip_the_budget(respx_mock) -> None:
    respx_mock.get(f"{_API}/billing/subscription").mock(
        return_value=httpx.Response(200, json=_SUBSCRIPTION)
    )
    budget = respx_mock.get(f"{_API}/billing/budget")

    read = await HttpBillingGateway().read(
        api_base=_API, credentials=_SECRET, with_budget=False
    )

    assert read.subscription is not None
    assert read.budget is None
    assert not budget.called


@pytest.mark.asyncio
async def test_http_billing_gateway_survives_a_network_failure(respx_mock) -> None:
    respx_mock.get(f"{_API}/billing/subscription").mock(
        side_effect=httpx.ConnectError("down")
    )
    respx_mock.get(f"{_API}/billing/budget").mock(
        side_effect=httpx.ConnectError("down")
    )

    assert (
        await HttpBillingGateway().read(api_base=_API, credentials=_SECRET)
        == BillingRead()
    )


def _controller(
    whoami: WhoAmIResult, billing: BillingRead, *, mistral_model: bool = True
) -> tuple[AccountController, FakeBillingGateway]:
    config = build_test_vibe_config()
    if not mistral_model:
        object.__setattr__(config, "is_active_model_mistral", lambda: False)
    host = MagicMock(
        config=config,
        config_orchestrator=MagicMock(config=config),
        apply_account_whoami=AsyncMock(),
    )
    gateway = MagicMock(read=AsyncMock(return_value=whoami))
    billing_gateway = FakeBillingGateway(billing)
    return AccountController(
        host, gateway, billing_gateway=billing_gateway
    ), billing_gateway


def _budget(usage: float, *, payg: bool | None = None, initial: float = 300.0):
    return BudgetResult(
        vibe_budget=BudgetDetail(
            usage_percentage=usage, initial_budget=initial, payg_enabled=payg
        )
    )


_CHAT_TEAM = WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name="TEAM")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subscription", "title"),
    [
        (None, "Team"),
        (SubscriptionResult(plan_name="INDIVIDUAL"), "Pro"),
        (SubscriptionResult(plan_name="EDU"), "Student"),
        (SubscriptionResult(plan_name="FREE"), "Free"),
        (SubscriptionResult(plan_name="FUTURE"), "Team"),
        # Pay-as-you-go reads in the footer's usage gauge, not in the title.
        (SubscriptionResult(plan_name="TEAM", vibe_payg_enabled=True), "Team"),
    ],
)
async def test_a_vibe_key_titles_its_plan_from_billing(
    subscription: SubscriptionResult | None, title: str
) -> None:
    controller, _ = _controller(_CHAT_TEAM, BillingRead(subscription=subscription))

    view = await controller.read()

    assert view.status is AccountStatus.READY
    assert view.plan is not None and view.plan.title == title


@pytest.mark.asyncio
async def test_a_vibe_key_carries_its_monthly_usage() -> None:
    controller, gateway = _controller(
        _CHAT_TEAM,
        BillingRead(
            subscription=SubscriptionResult(plan_name="TEAM", vibe_payg_enabled=True),
            budget=_budget(64.0),
        ),
    )

    view = await controller.read()

    assert view.budget == AccountBudgetView(usage_percentage=64.0, payg_enabled=True)
    [(api_base, credentials, with_budget)] = gateway.calls
    assert (api_base, with_budget) == ("https://api.mistral.ai/v1", True)
    assert await _sent_bearer(credentials, api_base) == "Bearer mock"


@pytest.mark.asyncio
async def test_another_model_keeps_the_plan_title_but_never_asks_for_the_budget() -> (
    None
):
    """The budget only counts usage on the Mistral key, so a session on another model skips it."""
    controller, gateway = _controller(
        _CHAT_TEAM,
        BillingRead(
            subscription=SubscriptionResult(plan_name="INDIVIDUAL"),
            budget=_budget(64.0),
        ),
        mistral_model=False,
    )

    view = await controller.read()

    assert view.status is AccountStatus.UNAVAILABLE
    assert view.plan is not None and view.plan.title == "Pro"
    assert view.budget is None
    assert [with_budget for *_, with_budget in gateway.calls] == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        (None, None),
        (BudgetResult(), None),
        (_budget(10.0, initial=0.0), None),
        (
            _budget(-3.0, payg=True),
            AccountBudgetView(usage_percentage=0.0, payg_enabled=True),
        ),
        (_budget(130.0, payg=False), AccountBudgetView(usage_percentage=130.0)),
    ],
)
async def test_a_budget_shows_only_when_vibe_has_an_allowance(
    budget: BudgetResult | None, expected: AccountBudgetView | None
) -> None:
    controller, _ = _controller(_CHAT_TEAM, BillingRead(budget=budget))

    assert (await controller.read()).budget == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("whoami", "title"),
    [
        (
            WhoAmIResult(plan_type=AccountPlanKind.API, plan_name="SCALE"),
            "[API] Scale plan",
        ),
        (
            WhoAmIResult(plan_type=AccountPlanKind.MISTRAL_CODE, plan_name="E"),
            "Mistral Code Enterprise",
        ),
    ],
)
async def test_other_keys_keep_their_whoami_plan_and_show_no_budget(
    whoami: WhoAmIResult, title: str
) -> None:
    """Billing describes the organization's chat plan, not a workspace or Codestral key."""
    controller, _ = _controller(
        whoami,
        BillingRead(
            subscription=SubscriptionResult(plan_name="FREE"), budget=_budget(50.0)
        ),
    )

    view = await controller.read()

    assert view.plan is not None and view.plan.title == title
    assert view.budget is None


class _HangingBillingGateway:
    """Never answers; records whether a read was left running."""

    def __init__(self) -> None:
        self.pending = 0

    async def read(
        self,
        *,
        api_base: str,
        credentials: PlatformCredentialProvider,
        with_budget: bool = True,
    ) -> BillingRead:
        self.pending += 1
        try:
            await asyncio.Event().wait()
        finally:
            self.pending -= 1
        raise AssertionError("unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "whoami",
    [
        WhoAmIResult(plan_type=AccountPlanKind.API, plan_name="SCALE"),
        WhoAmIResult(plan_type=AccountPlanKind.MISTRAL_CODE, plan_name="E"),
    ],
)
async def test_other_keys_never_wait_for_billing(whoami: WhoAmIResult) -> None:
    controller, _ = _controller(whoami, BillingRead())
    hanging = _HangingBillingGateway()
    controller._billing_gateway = hanging

    view = await asyncio.wait_for(controller.read(), 1.0)

    assert view.status is AccountStatus.READY
    assert hanging.pending == 0


@pytest.mark.asyncio
async def test_a_vibe_key_stops_waiting_for_a_billing_api_that_never_answers(
    monkeypatch,
) -> None:
    monkeypatch.setattr("vibe.app_server._account._BILLING_GRACE_SECONDS", 0.05)
    controller, _ = _controller(_CHAT_TEAM, BillingRead())
    hanging = _HangingBillingGateway()
    controller._billing_gateway = hanging

    view = await asyncio.wait_for(controller.read(), 1.0)

    assert view.plan is not None and view.plan.title == "Team"
    assert view.budget is None
    assert hanging.pending == 0


@pytest.mark.asyncio
async def test_a_failing_billing_gateway_leaves_the_account_read_intact() -> None:
    controller, gateway = _controller(_CHAT_TEAM, BillingRead())
    gateway.read = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    view = await controller.read()

    assert view.status is AccountStatus.READY
    assert view.plan is not None and view.plan.title == "Team"
    assert view.budget is None
