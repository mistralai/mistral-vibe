from __future__ import annotations

from collections.abc import Generator
import logging
from os import environ

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from vibe.cli.plan_offer.decide_plan_offer import (
    PlanOfferAction,
    PlanType,
    decide_plan_offer,
    resolve_api_key_for_plan,
)
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIResponse
from vibe.core.config import Backend, ProviderConfig


@pytest.fixture
def mistral_api_key_env() -> Generator[str, None, None]:
    original_value = environ.get("MISTRAL_API_KEY")
    test_api_key = "test_mistral_api_key"
    environ["MISTRAL_API_KEY"] = test_api_key
    yield test_api_key

    if original_value is not None:
        environ["MISTRAL_API_KEY"] = original_value
    else:
        del environ["MISTRAL_API_KEY"]


@pytest.mark.asyncio
async def test_proposes_upgrade_without_call_when_api_key_is_empty() -> None:
    gateway = FakeWhoAmIGateway(
        WhoAmIResponse(
            is_pro_plan=False,
            advertise_pro_plan=False,
            prompt_switching_to_pro_plan=False,
        )
    )
    action, plan_type = await decide_plan_offer("", gateway)

    assert action is PlanOfferAction.UPGRADE
    assert plan_type is PlanType.FREE
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("response", "expected_action", "expected_plan_type"),
    [
        (
            WhoAmIResponse(
                is_pro_plan=True,
                advertise_pro_plan=False,
                prompt_switching_to_pro_plan=False,
            ),
            PlanOfferAction.NONE,
            PlanType.PRO,
        ),
        (
            WhoAmIResponse(
                is_pro_plan=False,
                advertise_pro_plan=True,
                prompt_switching_to_pro_plan=False,
            ),
            PlanOfferAction.UPGRADE,
            PlanType.FREE,
        ),
        (
            WhoAmIResponse(
                is_pro_plan=False,
                advertise_pro_plan=False,
                prompt_switching_to_pro_plan=True,
            ),
            PlanOfferAction.SWITCH_TO_PRO_KEY,
            PlanType.PRO,
        ),
    ],
    ids=["with-a-pro-plan", "without-a-pro-plan", "with-a-non-pro-key"],
)
@pytest.mark.asyncio
async def test_proposes_an_action_based_on_current_plan_status(
    response: WhoAmIResponse,
    expected_action: PlanOfferAction,
    expected_plan_type: PlanType,
) -> None:
    gateway = FakeWhoAmIGateway(response)
    action, plan_type = await decide_plan_offer("api-key", gateway)

    assert action is expected_action
    assert plan_type is expected_plan_type
    assert gateway.calls == ["api-key"]


@pytest.mark.asyncio
async def test_proposes_nothing_when_nothing_is_suggested() -> None:
    gateway = FakeWhoAmIGateway(
        WhoAmIResponse(
            is_pro_plan=False,
            advertise_pro_plan=False,
            prompt_switching_to_pro_plan=False,
        )
    )

    action, plan_type = await decide_plan_offer("api-key", gateway)

    assert action is PlanOfferAction.NONE
    assert plan_type is PlanType.UNKNOWN
    assert gateway.calls == ["api-key"]


@pytest.mark.asyncio
async def test_proposes_upgrade_when_api_key_is_unauthorized() -> None:
    gateway = FakeWhoAmIGateway(unauthorized=True)
    action, plan_type = await decide_plan_offer("bad-key", gateway)

    assert action is PlanOfferAction.UPGRADE
    assert plan_type is PlanType.FREE
    assert gateway.calls == ["bad-key"]


@pytest.mark.asyncio
async def test_proposes_none_and_logs_warning_when_gateway_error_occurs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = FakeWhoAmIGateway(error=True)
    with caplog.at_level(logging.WARNING):
        action, plan_type = await decide_plan_offer("api-key", gateway)

    assert action is PlanOfferAction.NONE
    assert plan_type is PlanType.UNKNOWN
    assert gateway.calls == ["api-key"]
    assert "Failed to fetch plan status." in caplog.text


def test_resolve_api_key_for_plan_with_mistral_backend(
    mistral_api_key_env: str,
) -> None:
    test_api_key = mistral_api_key_env

    provider = ProviderConfig(
        name="test_mistral",
        api_base="https://api.mistral.ai",
        backend=Backend.MISTRAL,
        api_key_env_var="MISTRAL_API_KEY",
    )

    result = resolve_api_key_for_plan(provider)
    assert result == test_api_key


def test_resolve_api_key_for_plan_with_non_mistral_backend(
    mistral_api_key_env: str,
) -> None:
    provider = ProviderConfig(
        name="test_generic",
        api_base="https://api.generic.ai",
        backend=Backend.GENERIC,
        api_key_env_var="GENERIC_API_KEY",
    )

    result = resolve_api_key_for_plan(provider)
    assert result == mistral_api_key_env


def test_resolve_api_key_for_plan_with_missing_env_var() -> None:
    previous_api_key = environ["MISTRAL_API_KEY"]
    del environ["MISTRAL_API_KEY"]

    provider = ProviderConfig(
        name="test_mistral",
        api_base="https://api.mistral.ai",
        backend=Backend.MISTRAL,
        api_key_env_var="MISTRAL_API_KEY",
    )

    result = resolve_api_key_for_plan(provider)
    assert result is None

    if previous_api_key is not None:
        environ["MISTRAL_API_KEY"] = previous_api_key
