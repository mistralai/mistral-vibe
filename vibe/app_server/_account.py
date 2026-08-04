from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from vibe.app_server.models import (
    AccountAction,
    AccountActionKind,
    AccountPlanKind,
    AccountPlanView,
    AccountStatus,
    AccountView,
)
from vibe.core.agent_loop import AgentLoop
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

_WHOAMI_PATH = "/api/vibe/whoami"
_PAID_CHAT_PLANS = {"INDIVIDUAL", "EDU", "TEAM"}


class WhoAmIResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    plan_type: AccountPlanKind
    plan_name: str
    prompt_switching_to_pro_plan: bool = False

    @field_validator("plan_type", mode="before")
    @classmethod
    def parse_plan_type(cls, value: object) -> AccountPlanKind:
        if not isinstance(value, str):
            raise ValueError("plan_type must be a string")
        try:
            return AccountPlanKind(value.strip().lower())
        except ValueError as exc:
            raise ValueError(f"Unsupported plan_type: {value}") from exc


class AccountGatewayUnauthorized(Exception):
    pass


class AccountGatewayUnavailable(Exception):
    pass


class AccountGateway(Protocol):
    async def read(self, *, base_url: str, api_key: str) -> WhoAmIResult: ...


class HttpAccountGateway:
    async def read(self, *, base_url: str, api_key: str) -> WhoAmIResult:
        url = f"{base_url.rstrip('/')}{_WHOAMI_PATH}"
        try:
            async with VibeAsyncHTTPClient(verify=build_ssl_context()) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {api_key}"}
                )
        except httpx.RequestError as exc:
            raise AccountGatewayUnavailable() from exc

        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise AccountGatewayUnauthorized()
        if not response.is_success:
            raise AccountGatewayUnavailable(
                f"Unexpected account response status {response.status_code}"
            )

        try:
            return WhoAmIResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AccountGatewayUnavailable("Invalid account response") from exc


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
            return await self._read()

    async def _read(self) -> AccountView:
        runtime_config = self._agent_loop.config
        base_config = self._agent_loop.base_config
        vibe_base_url = base_config.vibe_base_url
        console_base_url = base_config.console_base_url
        self._agent_loop.set_user_plan(None)
        upgrade = _account_action(AccountActionKind.UPGRADE_TO_PRO, vibe_base_url)

        if not runtime_config.is_active_model_mistral():
            return AccountView(
                status=AccountStatus.UNAVAILABLE, teleport_action=upgrade
            )

        try:
            provider = runtime_config.get_active_provider()
        except ValueError:
            return AccountView(
                status=AccountStatus.UNAVAILABLE, teleport_action=upgrade
            )

        api_key = await asyncio.to_thread(resolve_api_key, provider.api_key_env_var)
        if not api_key:
            return AccountView(
                status=AccountStatus.MISSING_KEY, teleport_action=upgrade
            )

        try:
            result = await self._gateway.read(
                base_url=console_base_url, api_key=api_key
            )
        except AccountGatewayUnauthorized:
            return AccountView(
                status=AccountStatus.UNAUTHORIZED,
                plan_offer=upgrade,
                rate_limit_action=upgrade,
                teleport_action=upgrade,
            )
        except AccountGatewayUnavailable as exc:
            logger.warning(
                "Failed to fetch account status (%s)", type(exc).__name__, exc_info=exc
            )
            return AccountView(
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

        return AccountView(
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
