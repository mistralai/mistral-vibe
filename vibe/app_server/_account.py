from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from vibe.app_server._billing import (
    BillingGateway,
    BillingRead,
    SubscriptionResult,
    default_billing_gateway,
)
from vibe.app_server.models import (
    AccountAction,
    AccountActionKind,
    AccountApiKeyView,
    AccountBudgetView,
    AccountPlanKind,
    AccountPlanView,
    AccountStatus,
    AccountView,
)
from vibe.core.config import DEFAULT_VIBE_BASE_URL, VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.platform_auth import (
    PlatformCredentialFactory,
    PlatformCredentialProvider,
    PlatformCredentialSnapshot,
)
from vibe.core.platform_auth._mistral import (
    MistralCredentials,
    mistral_credentials,
    resolve_snapshot,
)
from vibe.core.types import Backend
from vibe.observability.logging import logger
from vibe.setup.auth.api_key_persistence import (
    apply_provider_to_config,
    apply_vibe_base_url,
)
from vibe.setup.auth.whoami import (
    NO_PLAN_DATA,
    AccountGateway,
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    HttpAccountGateway,
    WhoAmIResult,
    _sanitize_tenant_url,
    credential_principal,
    derive_user_plan,
    fetch_whoami,
    store_cached_whoami,
)

# Re-exports for existing callers (server.py, _resources.py, tests) that used to
# import these names from ``vibe.app_server._account``. Keeping the alias avoids
# a bigger import churn; the canonical location is ``vibe.setup.auth.whoami``.
__all__ = [
    "AccountController",
    "AccountGateway",
    "AccountGatewayUnauthorized",
    "AccountGatewayUnavailable",
    "AccountHost",
    "HttpAccountGateway",
    "WhoAmIResult",
    "fetch_whoami",
    "reconcile_tenant_domains",
]

# How long an account read waits for billing once /whoami has answered.
_BILLING_GRACE_SECONDS = 2.0
# Vibe keys read the chat plan; billing names the same plans.
_CHAT_PLAN_TITLES = {
    "FREE": "Free",
    "INDIVIDUAL": "Pro",
    "EDU": "Student",
    "TEAM": "Team",
}
_RECONCILE_REASON = "tenant-domain-reconcile"
# /whoami answers the plan type from the key scope, so the mapping is one-to-one.
_KEY_SCOPES = {
    AccountPlanKind.API: "workspace",
    AccountPlanKind.CHAT: "vibe",
    AccountPlanKind.MISTRAL_CODE: "codestral",
}
_KEY_PREVIEW_SUFFIX_CHARS = 4
# Never reveal more than a quarter of the secret: no suffix on shorter keys.
_KEY_PREVIEW_MIN_KEY_LEN = 4 * _KEY_PREVIEW_SUFFIX_CHARS
_UPGRADE_PLANS_URL = (
    "https://admin.mistral.ai/subscriptions/upgrade/plans"
    "?return_to=https%3A%2F%2Fchat.mistral.ai"
)


@dataclass(frozen=True, slots=True)
class _TenantDomainUpdate:
    provider_name: str
    whoami: WhoAmIResult


async def reconcile_tenant_domains(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    whoami: WhoAmIResult,
    *,
    provider_name: str,
) -> None:
    """Heal config.toml with any tenant URLs from /whoami that differ from
    what's currently persisted. Safe to call every whoami fetch — no-ops when
    the response has no ``domains`` field or when values already match.

    Covers two cases:
    - Onboarding whoami failed → next startup fetches it and self-heals.
    - Tenant admin changes URLs → next runtime whoami picks up the drift.

    A provider with ``preserve_api_base = true`` opts out of the ``api_base``
    heal: its configured base is kept verbatim (split-horizon deployments
    where the CLI reaches the API through a proxy). The ``vibe_base`` heal is
    unaffected.
    """
    if whoami.api_base is None and whoami.vibe_base is None:
        return
    current = orchestrator.config

    provider = next(
        (
            candidate
            for candidate in current.providers
            if candidate.name == provider_name and candidate.backend is Backend.MISTRAL
        ),
        None,
    )
    if whoami.api_base and provider is not None and not provider.preserve_api_base:
        sanitized_api = _sanitize_tenant_url(whoami.api_base, field="api_base")
        if sanitized_api is not None:
            desired_api_base = f"{sanitized_api}/v1"
            if provider.api_base != desired_api_base:
                await apply_provider_to_config(
                    orchestrator,
                    provider.model_copy(update={"api_base": desired_api_base}),
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
    has_paid_vibe_entitlement: bool

    @classmethod
    def from_result(cls, result: WhoAmIResult) -> _Plan:
        return cls(
            kind=result.plan_type,
            name=result.plan_name.strip(),
            prompt_switching_to_pro_plan=result.prompt_switching_to_pro_plan,
            has_paid_vibe_entitlement=result.has_paid_vibe_entitlement,
        )

    @property
    def normalized_name(self) -> str:
        return self.name.upper()

    @property
    def user_plan(self) -> str | None:
        return derive_user_plan(
            WhoAmIResult(
                plan_type=self.kind,
                plan_name=self.name,
                prompt_switching_to_pro_plan=self.prompt_switching_to_pro_plan,
            )
        )

    def title(self, subscription: SubscriptionResult | None = None) -> str | None:
        name = self.normalized_name
        match self.kind:
            case AccountPlanKind.CHAT:
                return _chat_title(name, subscription)
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
        if self.has_paid_vibe_entitlement:
            return False
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


class AccountHost(Protocol):
    """What ``AccountController`` needs of its session. ``AgentLoop`` satisfies
    it as it stands, so the legacy construction is unchanged and the Unified
    backend reaches this same controller through a small shim instead of a
    second copy of the status ladder.

    ``config`` and ``config_orchestrator`` are declared read-only because
    ``AgentLoop`` exposes them as properties; a mutable-attribute Protocol would
    reject it. The Protocol is enforced rather than cast: reaching for a further
    ``AgentLoop`` member inside the controller has to fail the type check, not
    the Unified path at runtime.
    """

    @property
    def config(self) -> VibeConfigSchema: ...

    @property
    def config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]: ...

    def set_user_plan(self, user_plan: str | None) -> None: ...

    async def apply_account_whoami(
        self, *, console_base_url: str, principal: str | None, whoami: WhoAmIResult
    ) -> None: ...

    async def clear_account_whoami(self, *, principal: str | None) -> None: ...


class AccountController:
    def __init__(
        self,
        agent_loop: AccountHost,
        gateway: AccountGateway | None = None,
        *,
        billing_gateway: BillingGateway | None = None,
        credentials: PlatformCredentialFactory | None = None,
    ) -> None:
        self._agent_loop = agent_loop
        self._gateway = gateway or HttpAccountGateway()
        self._billing_gateway = billing_gateway or default_billing_gateway()
        self._credentials = credentials
        self._lock = asyncio.Lock()

    async def read(self) -> AccountView:
        async with self._lock:
            tenant_update, view = await self._read()
        # Reconcile outside the lock: config writes hit the filesystem and can
        # block, and they don't need the account lock's mutual exclusion.
        if tenant_update is not None:
            await reconcile_tenant_domains(
                self._agent_loop.config_orchestrator,
                tenant_update.whoami,
                provider_name=tenant_update.provider_name,
            )
        return view

    async def _read(self) -> tuple[_TenantDomainUpdate | None, AccountView]:
        runtime_config = self._agent_loop.config
        vibe_base_url = runtime_config.vibe_base_url
        upgrade = _account_action(AccountActionKind.UPGRADE_TO_PRO, vibe_base_url)
        # Resolve a Mistral credential (prefers the active provider, else the
        # first configured Mistral provider). Runs in a thread because key
        # resolution can touch the keyring / filesystem.
        mistral = await asyncio.to_thread(
            mistral_credentials, runtime_config, self._credentials
        )
        if mistral is None:
            if runtime_config.get_mistral_provider() is None:
                # No Mistral provider at all — not our user. Expected absence:
                # stamp NO_PLAN_DATA (distinct from the null "tried but failed"
                # case) and show no account UI.
                self._agent_loop.set_user_plan(NO_PLAN_DATA)
                return None, AccountView(
                    status=AccountStatus.UNAVAILABLE, teleport_action=upgrade
                )
            # A Mistral provider is configured but its key is missing. Do NOT
            # clobber a user_plan the experiments path may have set; null
            # already signals the failure. Surface MISSING_KEY only when the
            # active model is Mistral — otherwise there is no account UI.
            return None, AccountView(
                status=(
                    AccountStatus.MISSING_KEY
                    if self._agent_loop.config.is_active_model_mistral()
                    else AccountStatus.UNAVAILABLE
                ),
                teleport_action=upgrade,
            )

        # Do NOT reset user_plan to None before the fetch. The experiments
        # init path may have already set it from a successful /whoami, and a
        # slow or failed account fetch here would clobber that value —
        # leaking null into telemetry for the rest of the session. Only the
        # success path below overwrites it.
        console_base_url = runtime_config.console_base_url
        principal = await credential_principal(mistral.credentials, console_base_url)
        api_key = await _api_key(mistral.credentials, console_base_url)
        # Billing runs beside /whoami so the plan title and budget add no round trip.
        # The budget only counts usage on the Mistral key: skip it for other models.
        with_budget = self._agent_loop.config.is_active_model_mistral()
        billing = asyncio.create_task(
            self._read_billing(
                mistral.provider.api_base, mistral.credentials, with_budget=with_budget
            )
        )
        try:
            return await self._read_plan(mistral, principal, api_key, billing, upgrade)
        finally:
            billing.cancel()
            await asyncio.gather(billing, return_exceptions=True)

    async def _read_plan(
        self,
        mistral: MistralCredentials,
        principal: str | None,
        api_key: str | None,
        billing: asyncio.Task[BillingRead],
        upgrade: AccountAction,
    ) -> tuple[_TenantDomainUpdate | None, AccountView]:
        vibe_base_url = self._agent_loop.config.vibe_base_url
        console_base_url = self._agent_loop.config.console_base_url
        try:
            result = await self._gateway.read(
                base_url=console_base_url, credentials=mistral.credentials
            )
        except AccountGatewayUnauthorized:
            # The credential was rejected: drop any cached plan so telemetry
            # reports null ("lookup failed"), not a stale cached plan for the TTL.
            await self._agent_loop.clear_account_whoami(principal=principal)
            # Suppress the account UI entirely for a non-Mistral active model;
            # otherwise surface the usual upgrade prompts.
            return None, (
                AccountView(status=AccountStatus.UNAVAILABLE, teleport_action=upgrade)
                if not self._agent_loop.config.is_active_model_mistral()
                else AccountView(
                    status=AccountStatus.UNAUTHORIZED,
                    plan_offer=_upgrade_offer(vibe_base_url),
                    rate_limit_action=upgrade,
                    teleport_action=upgrade,
                )
            )
        except AccountGatewayUnavailable as exc:
            logger.warning(
                "Failed to fetch account status (%s)", type(exc).__name__, exc_info=exc
            )
            return None, AccountView(
                status=AccountStatus.UNAVAILABLE, teleport_action=upgrade
            )

        # Warm the cross-session cache, and reconcile telemetry's plan fields
        # with this live result so user_plan and experiment_attributes never
        # diverge (the experiments path may have used a stale disk-cache hit).
        if principal is not None:
            store_cached_whoami(principal, result)
        await self._agent_loop.apply_account_whoami(
            console_base_url=console_base_url, principal=principal, whoami=result
        )

        plan = _Plan.from_result(result)
        # Only a Vibe key's chat plan is the one billing describes.
        reads = (
            await _settled(billing)
            if plan.kind is AccountPlanKind.CHAT
            else BillingRead()
        )
        plan_view = AccountPlanView(
            kind=plan.kind, name=plan.name, title=plan.title(reads.subscription)
        )
        api_key_view = (
            None
            if api_key is None
            else AccountApiKeyView(
                preview=_key_preview(api_key),
                scope=_KEY_SCOPES[plan.kind],
                access_scope=(
                    "personal_and_shared"
                    if result.can_access_private_resources
                    else "shared_only"
                )
                if result.can_access_private_resources is not None
                else None,
            )
        )
        # The OIDC registration, not account-context, decides which hosts a
        # user token may reach: a dedicated api_base or vibe_base written to
        # config.toml would fail every request until the user edits it out.
        tenant_update = (
            None
            if api_key is None
            else _TenantDomainUpdate(provider_name=mistral.provider.name, whoami=result)
        )

        if not self._agent_loop.config.is_active_model_mistral():
            return tenant_update, AccountView(
                status=AccountStatus.UNAVAILABLE,
                plan=plan_view,
                teleport_action=upgrade,
                api_key=api_key_view,
            )

        switch_key = _account_action(AccountActionKind.SWITCH_API_KEY, vibe_base_url)
        plan_offer: AccountAction | None = None
        if plan.prompt_switching_to_pro_plan:
            plan_offer = switch_key
        elif plan.offers_upgrade:
            plan_offer = _upgrade_offer(vibe_base_url)

        teleport_eligible = plan.kind is not AccountPlanKind.MISTRAL_CODE
        return tenant_update, AccountView(
            status=AccountStatus.READY,
            plan=plan_view,
            plan_offer=plan_offer,
            rate_limit_action=(upgrade if plan.rate_limit_upgrade_available else None),
            teleport_eligible=teleport_eligible,
            teleport_action=None if teleport_eligible else switch_key,
            api_key=api_key_view,
            budget=_budget_view(reads),
        )

    async def _read_billing(
        self,
        api_base: str,
        credentials: PlatformCredentialProvider,
        *,
        with_budget: bool,
    ) -> BillingRead:
        if self._billing_gateway is None:
            return BillingRead()
        try:
            return await self._billing_gateway.read(
                api_base=api_base, credentials=credentials, with_budget=with_budget
            )
        except Exception as exc:
            logger.warning("Billing read failed (%s)", type(exc).__name__, exc_info=exc)
            return BillingRead()


async def _settled(billing: asyncio.Task[BillingRead]) -> BillingRead:
    """The billing reads, given a short grace once /whoami has answered."""
    try:
        return await asyncio.wait_for(asyncio.shield(billing), _BILLING_GRACE_SECONDS)
    except TimeoutError:
        logger.info("Billing reads still pending; the account read goes without them")
        return BillingRead()


def _chat_title(name: str, subscription: SubscriptionResult | None) -> str | None:
    """The plan billing names, else the one /whoami names."""
    billed = subscription.plan_name.strip().upper() if subscription else ""
    return _CHAT_PLAN_TITLES.get(billed) or _CHAT_PLAN_TITLES.get(name)


def _budget_view(reads: BillingRead) -> AccountBudgetView | None:
    detail = reads.budget.vibe_budget if reads.budget else None
    if detail is None or detail.initial_budget <= 0:
        return None
    payg = detail.payg_enabled
    if payg is None and reads.subscription is not None:
        payg = reads.subscription.vibe_payg_enabled
    return AccountBudgetView(
        usage_percentage=max(0.0, detail.usage_percentage),
        reset_at=detail.reset_at,
        payg_enabled=bool(payg),
    )


async def _api_key(credentials: PlatformCredentialProvider, url: str) -> str | None:
    """The API key the account view previews; ``None`` for a user token."""
    snapshot = await resolve_snapshot(credentials, url)
    if not isinstance(snapshot, PlatformCredentialSnapshot):
        return None
    value = snapshot.headers.get("Authorization", "")
    if snapshot.kind != "api_key" or not value.startswith("Bearer "):
        return None
    return value.removeprefix("Bearer ")


def _key_preview(api_key: str) -> str:
    # Match Studio: mask everything but the last few characters.
    suffix = (
        api_key[-_KEY_PREVIEW_SUFFIX_CHARS:]
        if len(api_key) >= _KEY_PREVIEW_MIN_KEY_LEN
        else ""
    )
    return f"****{suffix}"


def _account_action(kind: AccountActionKind, vibe_base_url: str) -> AccountAction:
    return AccountAction(
        kind=kind, url=f"{vibe_base_url.rstrip('/')}/code/extensions?focus=key"
    )


def _upgrade_offer(vibe_base_url: str) -> AccountAction:
    # The plans page only sells Mistral's own Vibe: tenants keep their key page.
    if vibe_base_url.rstrip("/") != DEFAULT_VIBE_BASE_URL:
        return _account_action(AccountActionKind.UPGRADE_TO_PRO, vibe_base_url)
    return AccountAction(kind=AccountActionKind.UPGRADE_TO_PRO, url=_UPGRADE_PLANS_URL)
