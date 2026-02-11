from __future__ import annotations

from enum import StrEnum
import logging
from os import getenv

from vibe.cli.plan_offer.ports.whoami_gateway import (
    WhoAmIGateway,
    WhoAmIGatewayError,
    WhoAmIGatewayUnauthorized,
    WhoAmIResponse,
)
from vibe.core.config import DEFAULT_MISTRAL_API_ENV_KEY, Backend, ProviderConfig

logger = logging.getLogger(__name__)

CONSOLE_CLI_URL = "https://console.mistral.ai/codestral/cli"
UPGRADE_URL = CONSOLE_CLI_URL
SWITCH_TO_PRO_KEY_URL = CONSOLE_CLI_URL


class PlanOfferAction(StrEnum):
    NONE = "none"
    UPGRADE = "upgrade"
    SWITCH_TO_PRO_KEY = "switch_to_pro_key"


ACTION_TO_URL: dict[PlanOfferAction, str] = {
    PlanOfferAction.UPGRADE: UPGRADE_URL,
    PlanOfferAction.SWITCH_TO_PRO_KEY: SWITCH_TO_PRO_KEY_URL,
}


class PlanType(StrEnum):
    FREE = "free"
    PRO = "pro"
    UNKNOWN = "unknown"


async def decide_plan_offer(
    api_key: str | None, gateway: WhoAmIGateway
) -> tuple[PlanOfferAction, PlanType]:
    if not api_key:
        return PlanOfferAction.UPGRADE, PlanType.FREE
    try:
        response = await gateway.whoami(api_key)
    except WhoAmIGatewayUnauthorized:
        return PlanOfferAction.UPGRADE, PlanType.FREE
    except WhoAmIGatewayError:
        logger.warning("Failed to fetch plan status.", exc_info=True)
        return PlanOfferAction.NONE, PlanType.UNKNOWN
    return _action_and_plan_from_response(response)


def _action_and_plan_from_response(
    response: WhoAmIResponse,
) -> tuple[PlanOfferAction, PlanType]:
    match response:
        case WhoAmIResponse(is_pro_plan=True):
            return PlanOfferAction.NONE, PlanType.PRO
        case WhoAmIResponse(prompt_switching_to_pro_plan=True):
            return PlanOfferAction.SWITCH_TO_PRO_KEY, PlanType.PRO
        case WhoAmIResponse(advertise_pro_plan=True):
            return PlanOfferAction.UPGRADE, PlanType.FREE
        case _:
            return PlanOfferAction.NONE, PlanType.UNKNOWN


def resolve_api_key_for_plan(provider: ProviderConfig) -> str | None:
    api_env_key = DEFAULT_MISTRAL_API_ENV_KEY

    if provider.backend == Backend.MISTRAL:
        api_env_key = provider.api_key_env_var

    return getenv(api_env_key)


def plan_offer_cta(action: PlanOfferAction) -> str | None:
    if action is PlanOfferAction.NONE:
        return
    url = ACTION_TO_URL[action]
    match action:
        case PlanOfferAction.UPGRADE:
            text = f"### Unlock more with Vibe - [Upgrade to Le Chat Pro]({url})"
        case PlanOfferAction.SWITCH_TO_PRO_KEY:
            text = f"### Switch to your [Le Chat Pro API key]({url})"
    return text
