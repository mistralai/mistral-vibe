from __future__ import annotations

from enum import StrEnum
import logging

from vibe.cli.plan_offer.ports.whoami_gateway import (
    WhoAmIGateway,
    WhoAmIGatewayError,
    WhoAmIGatewayUnauthorized,
    WhoAmIPlanType,
    WhoAmIResponse,
)
from vibe.core.config import (
    DEFAULT_MISTRAL_API_ENV_KEY,
    DEFAULT_VIBE_BASE_URL,
    ProviderConfig,
    resolve_api_key,
)
from vibe.core.types import Backend

logger = logging.getLogger(__name__)


class MistralCodePlanName(StrEnum):
    FREE = "F"
    ENTERPRISE = "E"

    @classmethod
    def from_string(cls, value: str) -> MistralCodePlanName | None:
        try:
            return cls(value.strip().upper())
        except ValueError:
            return None


class ChatPlanName(StrEnum):
    FREE = "FREE"
    INDIVIDUAL = "INDIVIDUAL"
    EDU = "EDU"
    TEAM = "TEAM"

    @classmethod
    def from_string(cls, value: str) -> ChatPlanName | None:
        try:
            return cls(value.strip().upper())
        except ValueError:
            return None


_CHAT_PLAN_TO_USER_PLAN = {
    ChatPlanName.FREE: "Free",
    ChatPlanName.INDIVIDUAL: "Pro",
    ChatPlanName.EDU: "Student",
    ChatPlanName.TEAM: "Team",
}

_MISTRAL_CODE_PLAN_TO_USER_PLAN = {
    MistralCodePlanName.FREE: "Free Codestral",
    MistralCodePlanName.ENTERPRISE: "Code Enterprise",
}


class PlanInfo:
    plan_type: WhoAmIPlanType
    plan_name: str
    prompt_switching_to_pro_plan: bool
    user_plan: str | None

    def __init__(
        self,
        plan_type: WhoAmIPlanType,
        plan_name: str = "",
        prompt_switching_to_pro_plan: bool = False,
    ) -> None:
        self.plan_type = plan_type
        self.plan_name = plan_name
        self.prompt_switching_to_pro_plan = prompt_switching_to_pro_plan
        self.user_plan = user_plan_for(plan_type=plan_type, plan_name=plan_name)

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

    def is_free_chat_plan(self) -> bool:
        return (
            self.plan_type == WhoAmIPlanType.CHAT
            and self.plan_name.upper() == ChatPlanName.FREE
        )

    def is_chat_pro_plan(self) -> bool:
        return self.plan_type == WhoAmIPlanType.CHAT and self.plan_name.upper() in {
            ChatPlanName.INDIVIDUAL,
            ChatPlanName.EDU,
            ChatPlanName.TEAM,
        }

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


def user_plan_for(*, plan_type: WhoAmIPlanType, plan_name: str) -> str | None:
    plan_name_upper = plan_name.upper()
    if plan_type == WhoAmIPlanType.CHAT:
        plan = ChatPlanName.from_string(plan_name_upper)
        return None if plan is None else _CHAT_PLAN_TO_USER_PLAN[plan]
    if plan_type == WhoAmIPlanType.API:
        if "FREE" in plan_name_upper:
            return "Free API"
        if plan_name_upper:
            return "PAYG API"
    if plan_type == WhoAmIPlanType.MISTRAL_CODE:
        plan = MistralCodePlanName.from_string(plan_name_upper)
        return None if plan is None else _MISTRAL_CODE_PLAN_TO_USER_PLAN[plan]
    return None


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

    return resolve_api_key(api_env_key)


def vibe_api_key_url(vibe_base_url: str = DEFAULT_VIBE_BASE_URL) -> str:
    return f"{vibe_base_url.rstrip('/')}/code/extensions?focus=key"


def plan_offer_cta(
    payload: PlanInfo | None, *, vibe_base_url: str = DEFAULT_VIBE_BASE_URL
) -> str | None:
    if not payload:
        return
    key_url = vibe_api_key_url(vibe_base_url)
    if payload.prompt_switching_to_pro_plan:
        return f"### Switch to your [Vibe Pro API key]({key_url})"
    if (
        payload.plan_type in {WhoAmIPlanType.API, WhoAmIPlanType.UNAUTHORIZED}
        or payload.is_free_chat_plan()
        or payload.is_free_mistral_code_plan()
    ):
        return f"### Unlock more with Vibe - [Upgrade to Vibe Pro]({key_url})"


def check_teleport_eligibility(
    payload: PlanInfo | None, *, vibe_base_url: str = DEFAULT_VIBE_BASE_URL
) -> str | None:
    if payload is not None and payload.is_teleport_eligible():
        return None
    key_url = vibe_api_key_url(vibe_base_url)
    if payload is not None and payload.prompt_switching_to_pro_plan:
        return (
            "Teleport requires a Vibe Pro API key, but the current key is on a "
            f"different plan. Switch to your Vibe Pro API key: {key_url}"
        )
    return (
        "Teleport requires a Vibe Pro subscription. Your current API key isn't "
        f"eligible. Upgrade to Vibe Pro: {key_url}"
    )


def plan_title(payload: PlanInfo | None) -> str | None:  # noqa: PLR0911
    if not payload:
        return None
    if payload.is_free_chat_plan():
        return "Free"
    if payload.is_chat_pro_plan():
        return "[Subscription] Pro"
    if payload.is_free_api_plan():
        return "Free"
    if payload.is_paid_api_plan():
        return "[API] Scale plan"
    if payload.is_free_mistral_code_plan():
        return "Mistral Code Free"
    if payload.is_mistral_code_enterprise_plan():
        return "Mistral Code Enterprise"
    return None
