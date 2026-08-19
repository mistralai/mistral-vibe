from __future__ import annotations

import asyncio
from dataclasses import dataclass

from vibe.app_server.models import (
    AccountAction,
    AccountActionKind,
    AccountPlanKind,
    AccountPlanView,
    AccountStatus,
    AccountView,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.observability.logging import logger
from vibe.setup.auth.api_key_persistence import (
    apply_provider_to_config,
    apply_vibe_base_url,
)
from vibe.setup.auth.whoami import (
    AccountGateway,
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    HttpAccountGateway,
    WhoAmIResult,
    _sanitize_tenant_url,
    fetch_whoami,
)
from vibe.utils.api_keys import resolve_api_key

# Re-exports for existing callers (server.py, _resources.py, tests) that used to
# import these names from ``vibe.app_server._account``. Keeping the alias avoids
# a bigger import churn; the canonical location is ``vibe.setup.auth.whoami``.
__all__ = [
    "AccountController",
    "AccountGateway",
    "AccountGatewayUnauthorized",
    "AccountGatewayUnavailable",
    "HttpAccountGateway",
    "WhoAmIResult",
    "fetch_whoami",
    "reconcile_tenant_domains",
]

_PAID_CHAT_PLANS = {"INDIVIDUAL", "EDU", "TEAM"}
_RECONCILE_REASON = "tenant-domain-reconcile"


async def reconcile_tenant_domains(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], whoami: WhoAmIResult
) -> None:
    """Heal config.toml with any tenant URLs from /whoami that differ from
    what's currently persisted. Safe to call every whoami fetch — no-ops when
    the response has no ``domains`` field or when values already match.

    Covers two cases:
    - Onboarding whoami failed → next startup fetches it and self-heals.
    - Tenant admin changes URLs → next runtime whoami picks up the drift.
    """
    if whoami.api_base is None and whoami.vibe_base is None:
        return
    current = orchestrator.config

    if whoami.api_base:
        sanitized_api = _sanitize_tenant_url(whoami.api_base, field="api_base")
        if sanitized_api is not None:
            desired_api_base = f"{sanitized_api}/v1"
            try:
                active_provider = current.get_active_provider()
            except ValueError:
                # No active provider means we can't patch api_base, but that has
                # no bearing on the top-level vibe_base_url — fall through to the
                # vibe_base branch instead of returning.
                active_provider = None
            if (
                active_provider is not None
                and active_provider.api_base != desired_api_base
            ):
                await apply_provider_to_config(
                    orchestrator,
                    active_provider.model_copy(update={"api_base": desired_api_base}),
                    reason=_RECONCILE_REASON,
                )

    if whoami.vibe_base:
        sanitized_chat = _sanitize_tenant_url(whoami.vibe_base, field="vibe_base")
        if sanitized_chat is not None and current.vibe_base_url != sanitized_chat:
            await apply_vibe_base_url(
                orchestrator, sanitized_chat, reason=_RECONCILE_REASON
            )


@dataclass(frozen=True, slots=True)
class _Plan:
    kind: AccountPlanKind
    name: str
    prompt_switching_to_pro_plan: bool

    @classmethod
    def from_result(cls, result: WhoAmIResult) -> _Plan:
        return cls(
            kind=result.plan_type,
            name=result.plan_name.strip(),
            prompt_switching_to_pro_plan=result.prompt_switching_to_pro_plan,
        )

    @property
    def normalized_name(self) -> str:
        return self.name.upper()

    @property
    def user_plan(self) -> str | None:
        name = self.normalized_name
        match self.kind:
            case AccountPlanKind.CHAT:
                return {
                    "FREE": "Free",
                    "INDIVIDUAL": "Pro",
                    "EDU": "Student",
                    "TEAM": "Team",
                }.get(name)
            case AccountPlanKind.API:
                if not name:
                    return None
                return "Free API" if "FREE" in name else "PAYG API"
            case AccountPlanKind.MISTRAL_CODE:
                return {"F": "Free Codestral", "E": "Code Enterprise"}.get(name)
            case _:
                return None

    @property
    def title(self) -> str | None:
        name = self.normalized_name
        match self.kind:
            case AccountPlanKind.CHAT:
                if name == "FREE":
                    return "Free"
                return "[Subscription] Pro" if name in _PAID_CHAT_PLANS else None
            case AccountPlanKind.API:
                return "Free" if "FREE" in name else "[API] Scale plan"
            case AccountPlanKind.MISTRAL_CODE:
                return {"F": "Mistral Code Free", "E": "Mistral Code Enterprise"}.get(
                    name
                )
            case _:
                return None

    @property
    def offers_upgrade(self) -> bool:
        return (
            self.kind is AccountPlanKind.API
            or (self.kind is AccountPlanKind.CHAT and self.normalized_name == "FREE")
            or (
                self.kind is AccountPlanKind.MISTRAL_CODE
                and self.normalized_name == "F"
            )
        )

    @property
    def rate_limit_upgrade_available(self) -> bool:
        return self.kind is AccountPlanKind.API or (
            self.kind is AccountPlanKind.MISTRAL_CODE and self.normalized_name == "F"
        )

    @property
    def teleport_eligible(self) -> bool:
        return (
            self.kind is AccountPlanKind.CHAT
            and self.normalized_name in _PAID_CHAT_PLANS
            and not self.prompt_switching_to_pro_plan
        )


class AccountController:
    def __init__(
        self, agent_loop: AgentLoop, gateway: AccountGateway | None = None
    ) -> None:
        self._agent_loop = agent_loop
        self._gateway = gateway or HttpAccountGateway()
        self._lock = asyncio.Lock()

    async def read(self) -> AccountView:
        async with self._lock:
            fetched, view = await self._read()
        # Reconcile outside the lock: config writes hit the filesystem and can
        # block, and they don't need the account lock's mutual exclusion.
        if fetched is not None:
            await reconcile_tenant_domains(
                self._agent_loop.config_orchestrator, fetched
            )
        return view

    async def _read(self) -> tuple[WhoAmIResult | None, AccountView]:
        runtime_config = self._agent_loop.config
        vibe_base_url = runtime_config.vibe_base_url
        console_base_url = runtime_config.console_base_url
        self._agent_loop.set_user_plan(None)
        upgrade = _account_action(AccountActionKind.UPGRADE_TO_PRO, vibe_base_url)

        if not runtime_config.is_active_model_mistral():
            return None, AccountView(
                status=AccountStatus.UNAVAILABLE, teleport_action=upgrade
            )

        try:
            provider = runtime_config.get_active_provider()
        except ValueError:
            return None, AccountView(
                status=AccountStatus.UNAVAILABLE, teleport_action=upgrade
            )

        api_key = await asyncio.to_thread(resolve_api_key, provider.api_key_env_var)
        if not api_key:
            return None, AccountView(
                status=AccountStatus.MISSING_KEY, teleport_action=upgrade
            )

        try:
            result = await self._gateway.read(
                base_url=console_base_url, api_key=api_key
            )
        except AccountGatewayUnauthorized:
            return None, AccountView(
                status=AccountStatus.UNAUTHORIZED,
                plan_offer=upgrade,
                rate_limit_action=upgrade,
                teleport_action=upgrade,
            )
        except AccountGatewayUnavailable as exc:
            logger.warning(
                "Failed to fetch account status (%s)", type(exc).__name__, exc_info=exc
            )
            return None, AccountView(
                status=AccountStatus.UNAVAILABLE, teleport_action=upgrade
            )

        plan = _Plan.from_result(result)
        self._agent_loop.set_user_plan(plan.user_plan)
        switch_key = _account_action(AccountActionKind.SWITCH_API_KEY, vibe_base_url)
        plan_offer: AccountAction | None = None
        if plan.prompt_switching_to_pro_plan:
            plan_offer = switch_key
        elif plan.offers_upgrade:
            plan_offer = upgrade

        teleport_action: AccountAction | None = None
        if not plan.teleport_eligible:
            teleport_action = (
                switch_key if plan.prompt_switching_to_pro_plan else upgrade
            )

        return result, AccountView(
            status=AccountStatus.READY,
            plan=AccountPlanView(kind=plan.kind, name=plan.name, title=plan.title),
            plan_offer=plan_offer,
            rate_limit_action=(upgrade if plan.rate_limit_upgrade_available else None),
            teleport_eligible=plan.teleport_eligible,
            teleport_action=teleport_action,
        )


def _account_action(kind: AccountActionKind, vibe_base_url: str) -> AccountAction:
    return AccountAction(
        kind=kind, url=f"{vibe_base_url.rstrip('/')}/code/extensions?focus=key"
    )
