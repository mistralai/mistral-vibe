from __future__ import annotations

from vibe.app_server.models import AccountActionKind, AccountView


def plan_offer_cta(account: AccountView | None) -> str | None:
    if account is None or account.plan_offer is None:
        return None
    action = account.plan_offer
    if action.kind is AccountActionKind.SWITCH_API_KEY:
        return f"### Switch to your [Vibe Pro API key]({action.url})"
    return f"### Unlock more with Vibe - [Upgrade to Vibe Pro]({action.url})"


def plan_title(account: AccountView | None) -> str | None:
    if account is None or account.plan is None:
        return None
    return account.plan.title
