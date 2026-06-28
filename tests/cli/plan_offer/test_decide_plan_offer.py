from __future__ import annotations

from collections.abc import Generator
import logging
from os import environ

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from vibe.cli.plan_offer.decide_plan_offer import (
    PlanInfo,
    WhoAmIPlanType,
    check_teleport_eligibility,
    decide_plan_offer,
    plan_offer_cta,
    plan_title,
    resolve_api_key_for_plan,
)
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIResponse
from vibe.core.config import ProviderConfig
from vibe.core.types import Backend


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
async def test_returns_unknown_plan_when_api_key_is_empty() -> None:
    gateway = FakeWhoAmIGateway(
        WhoAmIResponse(
            plan_type=WhoAmIPlanType.API,
            plan_name="Free plan",
            prompt_switching_to_pro_plan=False,
        )
    )
    plan_info = await decide_plan_offer("", gateway)

    assert plan_info.plan_type is WhoAmIPlanType.UNKNOWN
    assert plan_info.plan_name == ""
    assert plan_info.prompt_switching_to_pro_plan is False
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("response", "expected_plan_type", "expected_plan_name", "expected_switch_flag"),
    [
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.API,
                plan_name="Free Plan",
                prompt_switching_to_pro_plan=False,
            ),
            WhoAmIPlanType.API,
            "Free Plan",
            False,
        ),
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="Pro Plan",
                prompt_switching_to_pro_plan=False,
            ),
            WhoAmIPlanType.CHAT,
            "Pro Plan",
            False,
        ),
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="Pro Plan",
                prompt_switching_to_pro_plan=True,
            ),
            WhoAmIPlanType.CHAT,
            "Pro Plan",
            True,
        ),
    ],
    ids=["api-plan", "chat-plan", "chat-plan-with-prompt"],
)
@pytest.mark.asyncio
async def test_returns_plan_info_and_proposes_an_action_based_on_current_plan_status(
    response: WhoAmIResponse,
    expected_plan_type: WhoAmIPlanType,
    expected_plan_name: str,
    expected_switch_flag: bool,
) -> None:
    gateway = FakeWhoAmIGateway(response)
    plan_info = await decide_plan_offer("api-key", gateway)

    assert plan_info.plan_type is expected_plan_type
    assert plan_info.plan_name == expected_plan_name
    assert plan_info.prompt_switching_to_pro_plan is expected_switch_flag
    assert gateway.calls == ["api-key"]


@pytest.mark.asyncio
async def test_returns_unauthorized_plan_when_api_key_is_unauthorized() -> None:
    gateway = FakeWhoAmIGateway(unauthorized=True)
    plan_info = await decide_plan_offer("bad-key", gateway)

    assert plan_info.plan_type is WhoAmIPlanType.UNAUTHORIZED
    assert plan_info.plan_name == ""
    assert plan_info.prompt_switching_to_pro_plan is False
    assert gateway.calls == ["bad-key"]


@pytest.mark.asyncio
async def test_returns_unknown_plan_and_logs_warning_when_gateway_error_occurs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = FakeWhoAmIGateway(error=True)
    with caplog.at_level(logging.WARNING):
        plan_info = await decide_plan_offer("api-key", gateway)

    assert plan_info.plan_type is WhoAmIPlanType.UNKNOWN
    assert plan_info.plan_name == ""
    assert plan_info.prompt_switching_to_pro_plan is False
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


def test_resolve_api_key_for_plan_falls_back_to_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    keyring_api_key = "keyring_mistral_api_key"
    monkeypatch.setattr(
        "vibe.core.utils.keyring.keyring.get_password",
        lambda service, username: keyring_api_key,
    )

    provider = ProviderConfig(
        name="test_mistral",
        api_base="https://api.mistral.ai",
        backend=Backend.MISTRAL,
        api_key_env_var="MISTRAL_API_KEY",
    )

    result = resolve_api_key_for_plan(provider)
    assert result == keyring_api_key


@pytest.mark.parametrize(
    ("plan_info", "expected_cta"),
    [
        (
            PlanInfo(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="INDIVIDUAL",
                prompt_switching_to_pro_plan=True,
            ),
            "### Switch to your [Vibe Pro API key](https://chat.mistral.ai/code/extensions?focus=key)",
        ),
        (
            PlanInfo(
                plan_type=WhoAmIPlanType.API,
                plan_name="FREE",
                prompt_switching_to_pro_plan=False,
            ),
            "### Unlock more with Vibe - [Upgrade to Vibe Pro](https://chat.mistral.ai/code/extensions?focus=key)",
        ),
        (
            PlanInfo(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="FREE",
                prompt_switching_to_pro_plan=False,
            ),
            "### Unlock more with Vibe - [Upgrade to Vibe Pro](https://chat.mistral.ai/code/extensions?focus=key)",
        ),
    ],
    ids=[
        "switch-to-vibe-pro-key",
        "upgrade-api-to-vibe-pro",
        "upgrade-free-vibe-to-pro",
    ],
)
def test_plan_offer_cta_routes_users_to_vibe_api_key_extensions(
    plan_info: PlanInfo, expected_cta: str
) -> None:
    assert plan_offer_cta(plan_info) == expected_cta


def test_plan_offer_cta_uses_configured_vibe_url() -> None:
    plan_info = PlanInfo(
        plan_type=WhoAmIPlanType.CHAT,
        plan_name="INDIVIDUAL",
        prompt_switching_to_pro_plan=True,
    )

    assert (
        plan_offer_cta(plan_info, vibe_base_url="https://vibe.example.com/")
        == "### Switch to your [Vibe Pro API key](https://vibe.example.com/code/extensions?focus=key)"
    )


def test_check_teleport_eligibility_returns_none_for_eligible_key() -> None:
    plan_info = PlanInfo(
        plan_type=WhoAmIPlanType.CHAT,
        plan_name="INDIVIDUAL",
        prompt_switching_to_pro_plan=False,
    )

    assert check_teleport_eligibility(plan_info) is None


@pytest.mark.parametrize(
    "plan_info",
    [
        PlanInfo(
            plan_type=WhoAmIPlanType.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=True,
        ),
        PlanInfo(
            plan_type=WhoAmIPlanType.API,
            plan_name="FREE",
            prompt_switching_to_pro_plan=False,
        ),
        PlanInfo(
            plan_type=WhoAmIPlanType.CHAT,
            plan_name="FREE",
            prompt_switching_to_pro_plan=False,
        ),
        None,
    ],
    ids=["pro-plan-wrong-key", "api-free", "chat-free", "unresolved"],
)
def test_check_teleport_eligibility_points_ineligible_keys_to_api_key_url(
    plan_info: PlanInfo | None,
) -> None:
    message = check_teleport_eligibility(plan_info)

    assert message is not None
    assert "https://chat.mistral.ai/code/extensions?focus=key" in message


def test_check_teleport_eligibility_uses_configured_vibe_url() -> None:
    plan_info = PlanInfo(
        plan_type=WhoAmIPlanType.CHAT,
        plan_name="INDIVIDUAL",
        prompt_switching_to_pro_plan=True,
    )

    message = check_teleport_eligibility(
        plan_info, vibe_base_url="https://vibe.example.com/"
    )

    assert message is not None
    assert "https://vibe.example.com/code/extensions?focus=key" in message


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="INDIVIDUAL",
                prompt_switching_to_pro_plan=False,
            ),
            True,
        ),
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="INDIVIDUAL",
                prompt_switching_to_pro_plan=True,
            ),
            False,
        ),
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="FREE",
                prompt_switching_to_pro_plan=False,
            ),
            False,
        ),
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="UNKNOWN",
                prompt_switching_to_pro_plan=False,
            ),
            False,
        ),
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.API,
                plan_name="FREE",
                prompt_switching_to_pro_plan=False,
            ),
            False,
        ),
        (
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.MISTRAL_CODE,
                plan_name="E",
                prompt_switching_to_pro_plan=False,
            ),
            False,
        ),
    ],
    ids=[
        "chat-plan-is-eligible",
        "chat-plan-requiring-key-switch-is-ineligible",
        "free-vibe-plan-is-ineligible",
        "unknown-chat-plan-is-ineligible",
        "api-plan-is-ineligible",
        "mistral-code-enterprise-is-ineligible",
    ],
)
def test_teleport_eligibility_depends_on_chat_plan_and_current_key(
    response: WhoAmIResponse, expected: bool
) -> None:
    assert PlanInfo.from_response(response).is_teleport_eligible() is expected


@pytest.mark.parametrize(
    ("payload", "expected_title"),
    [
        (PlanInfo(plan_type=WhoAmIPlanType.API, plan_name="FREE"), "Free"),
        (PlanInfo(plan_type=WhoAmIPlanType.CHAT, plan_name="FREE"), "Free"),
        (
            PlanInfo(plan_type=WhoAmIPlanType.CHAT, plan_name="INDIVIDUAL"),
            "[Subscription] Pro",
        ),
        (PlanInfo(plan_type=WhoAmIPlanType.CHAT, plan_name="UNKNOWN"), None),
        (
            PlanInfo(plan_type=WhoAmIPlanType.API, plan_name="Scale plan"),
            "[API] Scale plan",
        ),
    ],
    ids=[
        "free-api-plan",
        "free-vibe-plan",
        "chat-pro-plan",
        "unknown-chat-plan",
        "paid-api-plan",
    ],
)
def test_plan_title_uses_current_plan_labels(
    payload: PlanInfo, expected_title: str
) -> None:
    assert plan_title(payload) == expected_title
