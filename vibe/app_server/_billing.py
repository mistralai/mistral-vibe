"""Read the plan and the Vibe Code budget from the public billing API.

``/v1/billing/subscription`` names the organization's plan and whether
pay-as-you-go is on; ``/v1/billing/budget`` reports how much of the monthly
Vibe Code allowance is used. Both are optional: self-hosted platforms may not
serve them and ``/budget`` needs the ``see_org_inference_usage`` permission, so
every failure reads as ``None`` and the account read carries on without them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError

from vibe.core.platform_auth import (
    MistralPlatformHTTPClient,
    PlatformCredentialProvider,
    PlatformCredentialUnavailable,
)
from vibe.observability.logging import logger

_SUBSCRIPTION_PATH = "billing/subscription"
_BUDGET_PATH = "billing/budget"
# The footer and plan title are not worth a long wait on a platform that never answers.
_TIMEOUT_SECONDS = 3.0


class SubscriptionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_name: str
    # Pay-as-you-go covers Vibe Code past its allowance; null reads as off.
    vibe_payg_enabled: bool | None = None


class BudgetDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    usage_percentage: float
    initial_budget: float
    reset_at: str | None = None
    payg_enabled: bool | None = None


class BudgetResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vibe_budget: BudgetDetail | None = None


@dataclass(frozen=True, slots=True)
class BillingRead:
    subscription: SubscriptionResult | None = None
    budget: BudgetResult | None = None


class BillingGateway(Protocol):
    async def read(
        self,
        *,
        api_base: str,
        credentials: PlatformCredentialProvider,
        with_budget: bool = True,
    ) -> BillingRead: ...


class HttpBillingGateway:
    async def read(
        self,
        *,
        api_base: str,
        credentials: PlatformCredentialProvider,
        with_budget: bool = True,
    ) -> BillingRead:
        try:
            client = MistralPlatformHTTPClient(
                base_url=api_base, credentials=credentials, timeout=_TIMEOUT_SECONDS
            )
        except ValueError:
            logger.info("Skipping billing reads: invalid platform base URL")
            return BillingRead()
        async with client:
            subscription, budget = await asyncio.gather(
                _get(client, _SUBSCRIPTION_PATH, SubscriptionResult),
                _get(client, _BUDGET_PATH, BudgetResult) if with_budget else _none(),
            )
        return BillingRead(subscription=subscription, budget=budget)


async def _none() -> None:
    return None


async def _get[T: BaseModel](
    client: MistralPlatformHTTPClient, path: str, model: type[T]
) -> T | None:
    try:
        response = await client.get(path)
    except (httpx2.RequestError, PlatformCredentialUnavailable, ValueError) as exc:
        logger.info("Billing read %s failed (%s)", path, type(exc).__name__)
        return None
    if not response.is_success:
        # 403 on /budget is a member without the usage permission; 404 a
        # platform that does not serve billing. Neither is an error here.
        logger.info("Billing read %s answered %s", path, response.status_code)
        return None
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        logger.warning("Invalid billing response for %s (%s)", path, exc)
        return None


# The process-wide default, like ``_completion_facts.catalogue_fetcher_factory``:
# the suite clears it so an account read never reaches the network unasked.
billing_gateway_factory: Callable[[], BillingGateway] | None = HttpBillingGateway


def default_billing_gateway() -> BillingGateway | None:
    return billing_gateway_factory() if billing_gateway_factory else None
