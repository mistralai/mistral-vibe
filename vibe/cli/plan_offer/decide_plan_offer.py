from __future__ import annotations

from enum import StrEnum
import logging
from os import getenv

from vibe.cli.plan_offer.ports.whoami_gateway import (
    WhoAmIGateway,
    WhoAmIGatewayError,
    WhoAmIGatewayUnauthorized,
    WhoAmIPlanType,
    WhoAmIResponse,
)
from vibe.core.config import (
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_MISTRAL_API_ENV_KEY,
    ProviderConfig,
)
from vibe.core.types import Backend

logger = logging.getLogger(__name__)


class MistralCodePlanName(StrEnum):
    FREE = "F"
    ENTERPRISE = "E"


class PlanInfo:
    plan_type: WhoAmIPlanType
    plan_name: str
    prompt_switching_to_pro_plan: bool

    def __init__(
        self,
        plan_type: WhoAmIPlanType,
        plan_name: str = "",
        prompt_switching_to_pro_plan: bool = False,
    ) -> None:
        self.plan_type = plan_type
        self.plan_name = plan_name
        self.prompt_switching_to_pro_plan = prompt_switching_to_pro_plan

    @classmethod
    def from_response(cls, response: WhoAmIResponse) -> PlanInfo:
        return cls(
            plan_type=response.plan_type,
            plan_name=response.plan_name,
            prompt_switching_to_pro_plan=response.prompt_switching_to_pro_plan,
        )

    def is_paid_api_plan(self) -> bool:
        return self.plan_type == WhoAmIPlanType.API and not self.is_free_api_plan()

    def is_free_api_plan(self) -> bool:
        return self.plan_type == WhoAmIPlanType.API and "FREE" in self.plan_name.upper()

    def is_chat_pro_plan(self) -> bool:
        return self.plan_type == WhoAmIPlanType.CHAT

    def is_teleport_eligible(self) -> bool:
        return self.is_chat_pro_plan() and not self.prompt_switching_to_pro_plan

    def is_free_mistral_code_plan(self) -> bool:
        return (
            self.plan_type == WhoAmIPlanType.MISTRAL_CODE
            and self.plan_name.upper() == MistralCodePlanName.FREE
        )

    def is_mistral_code_enterprise_plan(self) -> bool:
        return (
            self.plan_type == WhoAmIPlanType.MISTRAL_CODE
            and self.plan_name.upper() == MistralCodePlanName.ENTERPRISE
        )


async def decide_plan_offer(api_key: str | None, gateway: WhoAmIGateway) -> PlanInfo:
    if not api_key:
        return PlanInfo(WhoAmIPlanType.UNKNOWN)
    try:
        response = await gateway.whoami(api_key)
        return PlanInfo.from_response(response)
    except WhoAmIGatewayUnauthorized:
        return PlanInfo(WhoAmIPlanType.UNAUTHORIZED)
    except WhoAmIGatewayError:
        logger.warning("Failed to fetch plan status.", exc_info=True)
    return PlanInfo(WhoAmIPlanType.UNKNOWN)


def resolve_api_key_for_plan(provider: ProviderConfig) -> str | None:
    api_env_key = DEFAULT_MISTRAL_API_ENV_KEY

    if provider.backend == Backend.MISTRAL:
        api_env_key = provider.api_key_env_var

    return getenv(api_env_key)


def plan_offer_cta(
    payload: PlanInfo | None, console_base_url: str = DEFAULT_CONSOLE_BASE_URL
) -> str | None:
    if not payload:
        return
    console_cli_url = f"{console_base_url.rstrip('/')}/codestral/cli"
    switch_to_pro_key_url = console_cli_url
    upgrade_url = console_cli_url
    if payload.prompt_switching_to_pro_plan:
        return f"### Switch to your [Le Chat Pro API key]({switch_to_pro_key_url})"
    if (
        payload.plan_type in {WhoAmIPlanType.API, WhoAmIPlanType.UNAUTHORIZED}
        or payload.is_free_mistral_code_plan()
    ):
        return f"### Unlock more with Vibe - [Upgrade to Le Chat Pro]({upgrade_url})"


def plan_title(payload: PlanInfo | None) -> str | None:  # noqa: PLR0911
    if not payload:
        return None
    if payload.is_chat_pro_plan():
        return "[Subscription] Pro"
    if payload.is_free_api_plan():
        return "[API] Experiment plan"
    if payload.is_paid_api_plan():
        return "[API] Scale plan"
    if payload.is_free_mistral_code_plan():
        return "Mistral Code Free"
    if payload.is_mistral_code_enterprise_plan():
        return "Mistral Code Enterprise"
    return None
