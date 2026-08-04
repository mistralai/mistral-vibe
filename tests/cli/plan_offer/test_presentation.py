from __future__ import annotations

import pytest

from vibe.app_server.models import (
    AccountAction,
    AccountActionKind,
    AccountPlanKind,
    AccountPlanView,
    AccountStatus,
    AccountView,
)
from vibe.cli.plan_offer.presentation import plan_offer_cta, plan_title

_ACCOUNT_URL = "https://chat.mistral.ai/code/extensions?focus=key"


def _plan(kind: AccountPlanKind, name: str) -> AccountPlanView:
    return AccountPlanView(kind=kind, name=name)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            AccountActionKind.SWITCH_API_KEY,
            f"### Switch to your [Vibe Pro API key]({_ACCOUNT_URL})",
        ),
        (
            AccountActionKind.UPGRADE_TO_PRO,
            f"### Unlock more with Vibe - [Upgrade to Vibe Pro]({_ACCOUNT_URL})",
        ),
    ],
)
def test_plan_offer_cta_renders_server_action(
    kind: AccountActionKind, expected: str
) -> None:
    account = AccountView(
        status=AccountStatus.READY,
        plan=_plan(AccountPlanKind.API, "FREE"),
        plan_offer=AccountAction(kind=kind, url=_ACCOUNT_URL),
        teleport_action=AccountAction(kind=kind, url=_ACCOUNT_URL),
    )

    assert plan_offer_cta(account) == expected


def test_plan_offer_cta_is_absent_without_action() -> None:
    account = AccountView(
        status=AccountStatus.UNAVAILABLE,
        teleport_action=AccountAction(
            kind=AccountActionKind.UPGRADE_TO_PRO, url=_ACCOUNT_URL
        ),
    )

    assert plan_offer_cta(account) is None


def test_plan_title_uses_public_projection() -> None:
    account = AccountView(
        status=AccountStatus.READY,
        plan=AccountPlanView(
            kind=AccountPlanKind.CHAT, name="INDIVIDUAL", title="[Subscription] Pro"
        ),
    )

    assert plan_title(account) == "[Subscription] Pro"
