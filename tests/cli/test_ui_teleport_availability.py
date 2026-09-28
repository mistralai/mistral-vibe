from __future__ import annotations

import platform
import time
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from tests.constants import OPENAI_BASE_URL
from tests.stubs.fake_account_gateway import FakeAccountGateway
from vibe import __version__
from vibe.app_server._account import WhoAmIResult
from vibe.app_server.models import AccountPlanKind
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import ErrorMessage
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.types import Backend
from vibe.core.utils import get_platform_id, get_platform_version


def _chat_account_gateway(*, plan_name: str = "INDIVIDUAL") -> FakeAccountGateway:
    return FakeAccountGateway(
        WhoAmIResult(plan_type=AccountPlanKind.CHAT, plan_name=plan_name)
    )


async def _wait_until(pause, predicate, timeout: float = 2.0) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return
        await pause(0.02)
    raise AssertionError("Condition was not met within the timeout")


def _expected_system_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "os": get_platform_id(),
        "arch": platform.machine().lower(),
        "version": __version__,
        "harness_backend": "legacy",
    }
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
        config=build_test_vibe_config(), account_gateway=_chat_account_gateway()
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
async def test_teleport_command_uses_effective_harness_backend() -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )
        runtime = app.app_server.resources.runtime

        with patch.object(
            type(runtime),
            "experimental_harness",
            new_callable=PropertyMock,
            return_value=True,
        ):
            app._refresh_command_registry()

        assert app.commands.get_command_name("/teleport") == "teleport"


@pytest.mark.asyncio
async def test_account_read_updates_subscription_banner() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(
        config=config, agent_loop=agent_loop, account_gateway=_chat_account_gateway()
    )

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: (
                "[Subscription] Pro"
                in str(app.query_one("#banner-user-plan", NoMarkupStatic).content)
            ),
        )
        assert agent_loop.user_plan == "Pro"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plan_type", "plan_name", "has_chat_subscription", "user_plan"),
    [
        (AccountPlanKind.CHAT, "INDIVIDUAL", False, "Pro"),
        (AccountPlanKind.API, "FREE", True, "Free API"),
        (AccountPlanKind.API, "PAY_AS_YOU_GO", True, "PAYG API"),
    ],
)
async def test_teleport_command_without_history_sends_early_failure_telemetry(
    telemetry_events: list[dict[str, Any]],
    plan_type: AccountPlanKind,
    plan_name: str,
    has_chat_subscription: bool,
    user_plan: str,
) -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(),
        account_gateway=FakeAccountGateway(
            WhoAmIResult(
                plan_type=plan_type,
                plan_name=plan_name,
                prompt_switching_to_pro_plan=has_chat_subscription,
            )
        ),
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
                "user_plan": user_plan,
                "stage": "no_history",
                "error_class": "TeleportNoHistoryError",
                "push_required": False,
                "nb_session_messages": 0,
                "context_summary": "skipped",
                "context_summary_chars": None,
                "session_id": app.app_server.session_id,
            },
        }
    ]


@pytest.mark.asyncio
async def test_teleport_command_allowed_for_free_chat_users(
    telemetry_events: list[dict[str, Any]],
) -> None:
    # Any Mistral API key is teleport-eligible now, including a free chat key.
    # With no history the command reaches the no-history stage, not "ineligible".
    app = build_test_vibe_app(
        config=build_test_vibe_config(),
        account_gateway=_chat_account_gateway(plan_name="FREE"),
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
            pilot.pause, lambda: bool(_teleport_failed_events(telemetry_events))
        )

    assert _teleport_failed_events(telemetry_events) == [
        {
            "event_name": "vibe.teleport_failed",
            "properties": {
                **_expected_system_metadata(),
                "user_plan": "Free",
                "stage": "no_history",
                "error_class": "TeleportNoHistoryError",
                "push_required": False,
                "nb_session_messages": 0,
                "context_summary": "skipped",
                "context_summary_chars": None,
                "session_id": app.app_server.session_id,
            },
        }
    ]


@pytest.mark.asyncio
async def test_teleport_command_errors_after_switching_to_non_mistral_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "mock-openai-key")
    config = build_test_vibe_config(
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
    app = build_test_vibe_app(config=config, account_gateway=_chat_account_gateway())

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: app.commands.get_command_name("/teleport") == "teleport",
        )

        await app.app_server.resources.config.update({"active_model": "gpt"})
        await app._reload_config()

        await _wait_until(pilot.pause, lambda: app.config.active_model.alias == "gpt")
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
