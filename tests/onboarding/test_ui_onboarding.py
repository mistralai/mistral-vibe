from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from textual.pilot import Pilot
from textual.widgets import Input

from vibe.core.config import ProviderConfig
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen, persist_api_key


async def _wait_for(
    condition: Callable[[], bool],
    pilot: Pilot,
    timeout: float = 5.0,
    interval: float = 0.05,
) -> None:
    elapsed = 0.0
    while not condition():
        await pilot.pause(interval)
        if (elapsed := elapsed + interval) >= timeout:
            msg = "Timed out waiting for condition."
            raise AssertionError(msg)


async def pass_welcome_screen(pilot: Pilot) -> None:
    welcome_screen = pilot.app.get_screen("welcome")
    await _wait_for(
        lambda: not welcome_screen.query_one("#enter-hint").has_class("hidden"), pilot
    )
    await pilot.press("enter")
    await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)


@pytest.mark.asyncio
async def test_ui_gets_through_the_onboarding_successfully() -> None:
    app = OnboardingApp()
    api_key_value = "sk-onboarding-test-key"

    async with app.run_test() as pilot:
        await pass_welcome_screen(pilot)
        api_screen = app.screen
        input_widget = api_screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        assert input_widget.value == api_key_value

        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"

    assert GLOBAL_ENV_FILE.path.is_file()
    env_contents = GLOBAL_ENV_FILE.path.read_text(encoding="utf-8")
    assert "MISTRAL_API_KEY" in env_contents
    assert api_key_value in env_contents


def test_api_key_screen_falls_back_to_mistral_for_provider_without_env_key() -> None:
    screen = ApiKeyScreen(
        provider=ProviderConfig(
            name="llamacpp", api_base="http://127.0.0.1:8080/v1", api_key_env_var=""
        )
    )

    assert screen.provider.name == "mistral"
    assert screen.provider.api_key_env_var == "MISTRAL_API_KEY"


def test_api_key_screen_keeps_provider_with_explicit_env_key() -> None:
    provider = ProviderConfig(
        name="custom",
        api_base="https://custom.example/v1",
        api_key_env_var="CUSTOM_API_KEY",
    )

    screen = ApiKeyScreen(provider=provider)

    assert screen.provider == provider


def test_api_key_screen_uses_mistral_fallback_for_context_without_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.setup.onboarding.screens.api_key.OnboardingContext.load",
        lambda: SimpleNamespace(
            provider=ProviderConfig(
                name="llamacpp", api_base="http://127.0.0.1:8080/v1", api_key_env_var=""
            )
        ),
    )

    screen = ApiKeyScreen()

    assert screen.provider.name == "mistral"
    assert screen.provider.api_key_env_var == "MISTRAL_API_KEY"


def test_persist_api_key_returns_save_error_for_invalid_env_var_name() -> None:
    provider = ProviderConfig(
        name="custom", api_base="https://custom.example/v1", api_key_env_var="BAD=NAME"
    )

    result = persist_api_key(provider, "secret")

    assert result == "env_var_error:BAD=NAME"


def test_persist_api_key_returns_env_var_error_for_empty_env_var_name() -> None:
    provider = ProviderConfig(
        name="custom", api_base="https://custom.example/v1", api_key_env_var=""
    )

    result = persist_api_key(provider, "secret")

    assert result == "env_var_error:<empty>"
