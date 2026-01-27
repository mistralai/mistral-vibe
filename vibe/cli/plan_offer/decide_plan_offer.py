from __future__ import annotations

from enum import StrEnum
import logging

from vibe.cli.plan_offer.ports.whoami_gateway import (
    WhoAmIGateway,
    WhoAmIGatewayError,
    WhoAmIGatewayUnauthorized,
    WhoAmIResponse,
)

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


async def decide_plan_offer(
    api_key: str | None, gateway: WhoAmIGateway
) -> PlanOfferAction:
    if not api_key:
        return PlanOfferAction.UPGRADE
    try:
        response = await gateway.whoami(api_key)
    except WhoAmIGatewayUnauthorized:
        return PlanOfferAction.UPGRADE
    except WhoAmIGatewayError:
        logger.warning("Failed to fetch plan status.", exc_info=True)
        return PlanOfferAction.NONE
    return _action_from_response(response)


def _action_from_response(response: WhoAmIResponse) -> PlanOfferAction:
    match response:
        case WhoAmIResponse(is_pro_plan=True):
            return PlanOfferAction.NONE
        case WhoAmIResponse(prompt_switching_to_pro_plan=True):
            return PlanOfferAction.SWITCH_TO_PRO_KEY
        case WhoAmIResponse(advertise_pro_plan=True):
            return PlanOfferAction.UPGRADE
        case _:
            return PlanOfferAction.NONE
