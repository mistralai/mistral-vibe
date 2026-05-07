from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType, WhoAmIResponse
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.types import Backend, LLMMessage, Role


def _chat_plan_gateway(*, prompt_switching_to_pro_plan: bool) -> FakeWhoAmIGateway:
    return FakeWhoAmIGateway(
        WhoAmIResponse(
            plan_type=WhoAmIPlanType.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=prompt_switching_to_pro_plan,
        )
    )


def _vibe_code_enabled_config() -> VibeConfig:
    return build_test_vibe_config(vibe_code_enabled=True)


async def _wait_until(pause, predicate, timeout: float = 2.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return
        await pause(0.02)
    raise AssertionError("Condition was not met within the timeout")


def _teleport_failed_events(
    telemetry_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        event
        for event in telemetry_events
        if event["event_name"] == "vibe.teleport_failed"
    ]


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
                "stage": "no_history",
                "error_class": "TeleportNoHistoryError",
                "push_required": False,
                "github_auth_required": False,
                "nb_session_messages": 0,
                "session_id": app.agent_loop.session_id,
            },
        }
    ]


@pytest.mark.asyncio
async def test_teleport_command_in_remote_session_sends_early_failure_telemetry(
    telemetry_events: list[dict[str, Any]],
) -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=False),
    )
    app.agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
    app.agent_loop.messages.append(LLMMessage(role=Role.assistant, content="hi"))

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        await app._remote_manager.attach(session_id="remote-session", config=app.config)
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/teleport")
        )
        await _wait_until(
            pilot.pause, lambda: len(_teleport_failed_events(telemetry_events)) == 1
        )
        await app._remote_manager.detach()

    assert _teleport_failed_events(telemetry_events) == [
        {
            "event_name": "vibe.teleport_failed",
            "properties": {
                "stage": "remote_session",
                "error_class": "TeleportRemoteSessionError",
                "push_required": False,
                "github_auth_required": False,
                "nb_session_messages": 2,
                "session_id": app.agent_loop.session_id,
            },
        }
    ]


@pytest.mark.asyncio
async def test_teleport_command_hidden_when_current_key_is_not_eligible() -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=True),
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.2)

        assert app.commands.get_command_name("/teleport") is None
        assert "/teleport" not in app.commands.get_help_text()
        input_widget = app.query_one(ChatInputContainer).input_widget
        assert input_widget is not None
        assert "&" not in input_widget.mode_characters


@pytest.mark.asyncio
async def test_hidden_teleport_command_falls_through_as_user_text() -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=True),
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.2)

        app._handle_teleport_command = AsyncMock()
        app._handle_user_message = AsyncMock()

        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/teleport")
        )

        app._handle_teleport_command.assert_not_awaited()
        app._handle_user_message.assert_awaited_once_with("/teleport")


@pytest.mark.asyncio
async def test_hidden_ampersand_teleport_shortcut_falls_through_as_user_text() -> None:
    app = build_test_vibe_app(
        config=_vibe_code_enabled_config(),
        plan_offer_gateway=_chat_plan_gateway(prompt_switching_to_pro_plan=True),
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.2)

        app._handle_teleport_command = AsyncMock()
        app._handle_user_message = AsyncMock()

        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("&continue")
        )

        app._handle_teleport_command.assert_not_awaited()
        app._handle_user_message.assert_awaited_once_with("&continue")


@pytest.mark.asyncio
async def test_teleport_command_hides_after_switching_to_non_mistral_model(
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
                api_base="https://api.openai.com/v1",
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

    async def fake_reload_with_initial_messages(*, base_config) -> None:
        app.agent_loop._base_config = base_config
        app.agent_loop.agent_manager.invalidate_config()

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        with (
            patch(
                "vibe.cli.textual_ui.app.VibeConfig.load",
                return_value=non_mistral_config,
            ),
            patch.object(
                app.agent_loop,
                "reload_with_initial_messages",
                new=AsyncMock(side_effect=fake_reload_with_initial_messages),
            ),
        ):
            await app._reload_config()

        await _wait_until(
            pilot.pause, lambda: app.commands.get_command_name("/teleport") is None
        )

        assert app.commands.get_command_name("/teleport") is None
        input_widget = app.query_one(ChatInputContainer).input_widget
        assert input_widget is not None
        assert "&" not in input_widget.mode_characters
