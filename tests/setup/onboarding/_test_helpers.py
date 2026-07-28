from __future__ import annotations

from collections.abc import Callable

from textual.pilot import Pilot

from tests.conftest import build_test_vibe_config
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.types import Backend
from vibe.setup.auth import BrowserSignInService
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen
from vibe.setup.onboarding.screens.auth_method import AuthMethodScreen
from vibe.setup.onboarding.screens.browser_sign_in import BrowserSignInScreen
from vibe.setup.onboarding.screens.theme_selection import ThemeSelectionScreen
from vibe.utils.io import read_safe

CONSOLE_URL = "https://console.mistral.ai"
BROWSER_AUTH_API_URL = "https://console.mistral.ai/api"


async def wait_for(
    condition: Callable[[], bool],
    pilot: Pilot,
    timeout: float = 5.0,
    interval: float = 0.05,
) -> None:
    elapsed = 0.0
    while not condition():
        await pilot.pause(interval)
        if (elapsed := elapsed + interval) >= timeout:
            raise AssertionError("Timed out waiting for condition.")


def build_onboarding_config(
    *,
    provider_name: str = "mistral",
    model_provider: str | None = None,
    backend: Backend = Backend.MISTRAL,
    api_key_env_var: str = "MISTRAL_API_KEY",
    browser_auth_base_url: str | None = None,
    browser_auth_api_base_url: str | None = None,
    vibe_base_url: str = "https://chat.mistral.ai",
) -> VibeConfigSchema:
    provider = ProviderConfig(
        name=provider_name,
        api_base="https://api.mistral.ai/v1",
        api_key_env_var=api_key_env_var,
        browser_auth_base_url=browser_auth_base_url,
        browser_auth_api_base_url=browser_auth_api_base_url,
        backend=backend,
    )
    model = ModelConfig(
        name="mistral-vibe-cli-latest",
        provider=model_provider or provider_name,
        alias="devstral-2",
    )
    return build_test_vibe_config(
        providers=[provider], models=[model], vibe_base_url=vibe_base_url
    )


def build_browser_onboarding_app(
    *,
    browser_sign_in_service_factory: Callable[[], BrowserSignInService] | None = None,
    browser_sign_in_success_delay: float = 0,
    browser_sign_in_url_help_delay: float = 0,
    copy_sign_in_url: Callable[[str], bool] | None = None,
) -> OnboardingApp:
    return OnboardingApp(
        config=build_onboarding_config(
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=BROWSER_AUTH_API_URL,
        ),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        browser_sign_in_success_delay=browser_sign_in_success_delay,
        browser_sign_in_url_help_delay=browser_sign_in_url_help_delay,
        copy_sign_in_url=copy_sign_in_url,
    )


def saved_env_contents() -> str:
    return read_safe(GLOBAL_ENV_FILE.path).text


async def pass_welcome_screen(pilot: Pilot) -> None:
    welcome_screen = pilot.app.get_screen("welcome")
    await wait_for(
        lambda: not welcome_screen.query_one("#enter-hint").has_class("hidden"), pilot
    )
    await pilot.press("enter")
    await wait_for(lambda: isinstance(pilot.app.screen, ThemeSelectionScreen), pilot)


async def pass_theme_selection_screen(pilot: Pilot) -> None:
    await pilot.press("enter")


async def show_auth_method(pilot: Pilot) -> None:
    await pass_welcome_screen(pilot)
    await pass_theme_selection_screen(pilot)
    await wait_for(lambda: isinstance(pilot.app.screen, AuthMethodScreen), pilot)


async def show_browser_sign_in(pilot: Pilot) -> None:
    await show_auth_method(pilot)
    await pilot.press("enter")
    await wait_for(lambda: isinstance(pilot.app.screen, BrowserSignInScreen), pilot)


async def show_manual_api_key_screen(pilot: Pilot) -> None:
    await show_auth_method(pilot)
    await pilot.press("down", "enter")
    await wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)
