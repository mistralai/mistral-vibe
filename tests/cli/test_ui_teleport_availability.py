from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import build_test_vibe_app, build_test_vibe_config, set_agent_config
from tests.constants import OPENAI_BASE_URL
from vibe import __version__
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType, WhoAmIResponse
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import ErrorMessage
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.types import Backend
from vibe.core.utils import get_platform_id, get_platform_version


def _chat_plan_gateway(*, prompt_switching_to_pro_plan: bool) -> FakeWhoAmIGateway:
    return FakeWhoAmIGateway(
        WhoAmIResponse(
            plan_type=WhoAmIPlanType.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=prompt_switching_to_pro_plan,
        )
    )


def _vibe_code_enabled_config() -> VibeConfigSchema:
    return build_test_vibe_config(vibe_code_enabled=True)


async def _wait_until(pause, predicate, timeout: float = 2.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return
        await pause(0.02)
    raise AssertionError("Condition was not met within the timeout")


def _expected_system_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {"os": get_platform_id(), "version": __version__}
    if os_version := get_platform_version():
        metadata["os_version"] = os_version
    return metadata


def _teleport_failed_events(
    telemetry_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        event
        for event in telemetry_events
        if event["event_name"] == "vibe.teleport_failed"
    ]


def _error_messages(app) -> list[str]:
    return [error._error for error in app.query(ErrorMessage)]


@pytest.mark.asyncio
async def test_teleport_command_visible_for_paid_chat_users() -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=False),
    )

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        assert app.commands.get_command_name("/teleport") == "teleport"
        assert "/teleport" in app.commands.get_help_text()
        input_widget = app.query_one(ChatInputContainer).input_widget
        assert input_widget is not None
        assert "&" in input_widget.mode_characters


@pytest.mark.asyncio
async def test_plan_resolution_updates_subscription_banner() -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=False),
    )

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: (
                "[Subscription] Pro"
                in str(app.query_one("#banner-user-plan", NoMarkupStatic).content)
            ),
        )
        assert app.agent_loop.user_plan == "Pro"


@pytest.mark.asyncio
async def test_teleport_command_without_history_sends_early_failure_telemetry(
    telemetry_events: list[dict[str, Any]],
) -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=False),
    )

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/teleport")
        )

    assert _teleport_failed_events(telemetry_events) == [
        {
            "event_name": "vibe.teleport_failed",
            "properties": {
                **_expected_system_metadata(),
                "user_plan": "Pro",
                "stage": "no_history",
                "error_class": "TeleportNoHistoryError",
                "push_required": False,
                "nb_session_messages": 0,
                "context_summary": "skipped",
                "context_summary_chars": None,
                "session_id": app.agent_loop.session_id,
            },
        }
    ]


@pytest.mark.asyncio
async def test_teleport_command_visible_but_errors_when_key_not_eligible(
    telemetry_events: list[dict[str, Any]],
) -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=True),
    )

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        assert "/teleport" in app.commands.get_help_text()
        input_widget = app.query_one(ChatInputContainer).input_widget
        assert input_widget is not None
        assert "&" in input_widget.mode_characters

        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/teleport")
        )
        await _wait_until(
            pilot.pause,
            lambda: any("Vibe Pro API key" in error for error in _error_messages(app)),
        )

    assert _teleport_failed_events(telemetry_events) == [
        {
            "event_name": "vibe.teleport_failed",
            "properties": {
                **_expected_system_metadata(),
                "user_plan": "Pro",
                "stage": "ineligible",
                "error_class": "TeleportIneligibleError",
                "push_required": False,
                "nb_session_messages": 0,
                "context_summary": "skipped",
                "context_summary_chars": None,
                "session_id": app.agent_loop.session_id,
            },
        }
    ]


@pytest.mark.asyncio
async def test_teleport_command_errors_instead_of_user_text_when_not_eligible() -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=True),
    )

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        app._handle_user_message = AsyncMock()

        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/teleport")
        )
        await _wait_until(
            pilot.pause,
            lambda: any("Vibe Pro API key" in error for error in _error_messages(app)),
        )

        app._handle_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_ampersand_teleport_shortcut_errors_when_not_eligible() -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=True),
    )

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        app._handle_user_message = AsyncMock()

        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("&continue")
        )
        await _wait_until(
            pilot.pause,
            lambda: any("Vibe Pro API key" in error for error in _error_messages(app)),
        )

        app._handle_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_teleport_command_errors_after_switching_to_non_mistral_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "mock-openai-key")
    config = build_test_vibe_config(
        vibe_code_enabled=True,
        providers=[
            ProviderConfig(
                name="mistral",
                api_base="https://api.mistral.ai/v1",
                api_key_env_var="MISTRAL_API_KEY",
                backend=Backend.MISTRAL,
            ),
            ProviderConfig(
                name="openai",
                api_base=f"{OPENAI_BASE_URL}/v1",
                api_key_env_var="OPENAI_API_KEY",
                backend=Backend.GENERIC,
            ),
        ],
        models=[
            ModelConfig(
                name="mistral-vibe-cli-latest", provider="mistral", alias="devstral"
            ),
            ModelConfig(name="gpt-4.1", provider="openai", alias="gpt"),
        ],
        active_model="devstral",
    )
    app = build_test_vibe_app(
        config=config,
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=False),
    )
    non_mistral_config = build_test_vibe_config(
        vibe_code_enabled=True,
        providers=config.providers,
        models=config.models,
        active_model="gpt",
    )

    async def fake_reload_with_initial_messages() -> None:
        set_agent_config(app.agent_loop, non_mistral_config)

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        with patch.object(
            app.agent_loop,
            "reload_with_initial_messages",
            new=AsyncMock(side_effect=fake_reload_with_initial_messages),
        ):
            await app._reload_config()

        await _wait_until(pilot.pause, lambda: not app.config.is_active_model_mistral())
        assert app.commands.get_command_name("/teleport") == "teleport"
        input_widget = app.query_one(ChatInputContainer).input_widget
        assert input_widget is not None
        assert "&" in input_widget.mode_characters

        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/teleport")
        )
        await _wait_until(
            pilot.pause,
            lambda: any(
                "active Mistral model" in error for error in _error_messages(app)
            ),
        )
