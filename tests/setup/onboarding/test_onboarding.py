from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.events import Resize
from textual.geometry import Size
from textual.widgets import Input, Link, Static

from tests.conftest import build_test_vibe_config
from tests.setup.onboarding._test_helpers import (
    BROWSER_AUTH_API_URL,
    CONSOLE_URL,
    build_browser_onboarding_app as _build_browser_onboarding_app,
    build_onboarding_config as _build_onboarding_config,
    pass_theme_selection_screen as _pass_theme_selection_screen,
    pass_welcome_screen as _pass_welcome_screen,
    saved_env_contents as _saved_env_contents,
    show_auth_method as _show_auth_method,
    show_browser_sign_in as _show_browser_sign_in,
    show_manual_api_key_screen as _show_manual_api_key_screen,
    wait_for as _wait_for,
)
from tests.stubs.fake_browser_sign_in_gateway import (
    build_browser_sign_in_service_factory,
    build_completed_poll_script,
)
from vibe.core.config import (
    AUTO_THEME,
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    ProviderConfig,
)
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import TerminalEmulator
from vibe.core.types import Backend
from vibe.setup.auth import BrowserSignInError, BrowserSignInErrorCode
from vibe.setup.auth.api_key_persistence import persist_api_key
import vibe.setup.onboarding as onboarding_module
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen
from vibe.setup.onboarding.screens.auth_method import AuthMethodScreen
from vibe.setup.onboarding.screens.theme_selection import THEMES, ThemeSelectionScreen
from vibe.setup.onboarding.screens.welcome import HIGHLIGHT_END, WelcomeScreen


def _patch_failing_browser_sign_in_service(
    monkeypatch: pytest.MonkeyPatch, captured_base_urls: list[tuple[str, str]]
) -> None:
    class FakeGateway:
        def __init__(self, browser_base_url: str, api_base_url: str) -> None:
            captured_base_urls.append((browser_base_url, api_base_url))

    class FakeService:
        def __init__(
            self, gateway: FakeGateway, *, raise_on_browser_open_failure: bool = True
        ) -> None:
            if raise_on_browser_open_failure:
                raise AssertionError(
                    "Onboarding must tolerate browser launch failures."
                )
            self._gateway = gateway

        async def authenticate(self, *args, **kwargs) -> str:
            raise BrowserSignInError(
                "Browser sign-in polling failed.",
                code=BrowserSignInErrorCode.POLL_FAILED,
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(onboarding_module, "HttpBrowserSignInGateway", FakeGateway)
    monkeypatch.setattr(onboarding_module, "BrowserSignInService", FakeService)


def test_welcome_gradient_animation_skips_relayout() -> None:
    screen = WelcomeScreen()
    screen._char_index = HIGHLIGHT_END
    welcome_text = MagicMock(spec=Static)
    screen._welcome_text = welcome_text

    screen._animate_gradient()

    welcome_text.update.assert_called_once()
    assert welcome_text.update.call_args.kwargs == {"layout": False}


@pytest.mark.asyncio
async def test_ui_keeps_manual_flow_when_browser_sign_in_is_unsupported() -> None:
    app = OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url="", browser_auth_api_base_url=""
        )
    )
    api_key_value = "sk-onboarding-test-key"

    async with app.run_test() as pilot:
        await _pass_welcome_screen(pilot)
        await _pass_theme_selection_screen(pilot)
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)
        input_widget = app.screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        assert input_widget.value == api_key_value
        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert api_key_value in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_supports_browser_sign_in_when_provider_supports_it() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()]
    )
    app = OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=BROWSER_AUTH_API_URL,
        ),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
    )

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_auth_method(pilot)


@pytest.mark.asyncio
async def test_ui_offers_browser_sign_in_for_renamed_mistral_provider() -> None:
    app = OnboardingApp(
        config=_build_onboarding_config(
            provider_name="customer-mistral",
            backend=Backend.MISTRAL,
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=BROWSER_AUTH_API_URL,
        )
    )

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_auth_method(pilot)


@pytest.mark.asyncio
async def test_ui_allows_manual_path_when_browser_sign_in_is_supported() -> None:
    app = _build_browser_onboarding_app()
    api_key_value = "sk-manual-onboarding-test-key"

    async with app.run_test() as pilot:
        await _show_auth_method(pilot)
        await pilot.press("down", "enter")
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)
        input_widget = app.screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)
        assert input_widget.value == api_key_value

    assert app.return_value == "completed"
    assert api_key_value in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_uses_default_mistral_browser_auth_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_base_urls: list[tuple[str, str]] = []
    _patch_failing_browser_sign_in_service(monkeypatch, captured_base_urls)

    app = OnboardingApp(config=build_test_vibe_config())

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(lambda: bool(captured_base_urls), pilot)

    assert captured_base_urls == [
        (
            DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
            DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
        )
    ]


@pytest.mark.asyncio
async def test_ui_preserves_custom_browser_auth_urls_when_api_key_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "\n".join([
            'active_model = "devstral-2"',
            "[[providers]]",
            'name = "mistral"',
            'api_base = "https://api.mistral.ai/v1"',
            'api_key_env_var = "MISTRAL_API_KEY"',
            'browser_auth_base_url = "http://127.0.0.1:8787"',
            'browser_auth_api_base_url = "http://127.0.0.1:8787"',
            'backend = "mistral"',
            "",
            "[[models]]",
            'name = "mistral-vibe-cli-latest"',
            'provider = "mistral"',
            'alias = "devstral-2"',
            "",
        ]),
        encoding="utf-8",
    )
    reset_harness_files_manager()
    init_harness_files_manager("user")
    captured_base_urls: list[tuple[str, str]] = []
    _patch_failing_browser_sign_in_service(monkeypatch, captured_base_urls)

    app = OnboardingApp()

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(lambda: bool(captured_base_urls), pilot)

    assert captured_base_urls == [("http://127.0.0.1:8787", "http://127.0.0.1:8787")]


@pytest.mark.asyncio
async def test_ui_falls_back_to_default_onboarding_context_with_invalid_active_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "\n".join([
            'active_model = "does-not-exist"',
            "",
            "[[providers]]",
            'name = "mistral"',
            'api_base = "https://api.mistral.ai/v1"',
            'api_key_env_var = "MISTRAL_API_KEY"',
            'browser_auth_base_url = "https://console.mistral.ai"',
            'browser_auth_api_base_url = "https://console.mistral.ai/api"',
            'backend = "mistral"',
            "",
            "[[models]]",
            'name = "mistral-vibe-cli-latest"',
            'provider = "mistral"',
            'alias = "devstral-2"',
            "",
        ]),
        encoding="utf-8",
    )
    reset_harness_files_manager()
    init_harness_files_manager("user")

    app = OnboardingApp()

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_auth_method(pilot)


@pytest.mark.asyncio
async def test_ui_preserves_auto_theme_when_selection_is_unchanged() -> None:
    app = OnboardingApp()

    async with app.run_test() as pilot:
        await _pass_welcome_screen(pilot)

        theme_screen = app.screen
        assert isinstance(theme_screen, ThemeSelectionScreen)
        assert theme_screen.selected_theme == AUTO_THEME

        await pilot.press("enter")
        await _wait_for(lambda: isinstance(app.screen, AuthMethodScreen), pilot)

    assert app.selected_theme == AUTO_THEME


@pytest.mark.asyncio
async def test_ui_can_pick_a_theme_and_saves_selection() -> None:
    app = OnboardingApp()

    async with app.run_test() as pilot:
        await _pass_welcome_screen(pilot)

        theme_screen = app.screen
        assert isinstance(theme_screen, ThemeSelectionScreen)
        app.post_message(Resize(Size(40, 10), Size(40, 10)))
        preview = theme_screen.query_one("#preview")
        assert preview.styles.max_height is not None

        target_theme = "gruvbox"
        assert target_theme in THEMES
        start_index = theme_screen._theme_index
        target_index = THEMES.index(target_theme)
        steps_down = (target_index - start_index) % len(THEMES)
        await pilot.press(*["down"] * steps_down)
        assert app.theme == target_theme

        await pilot.press("enter")
        await _wait_for(lambda: isinstance(app.screen, AuthMethodScreen), pilot)

    assert app.theme == target_theme


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
        "vibe.setup.auth.api_key_persistence._load_onboarding_provider",
        lambda: ProviderConfig(
            name="llamacpp", api_base="http://127.0.0.1:8080/v1", api_key_env_var=""
        ),
    )

    screen = ApiKeyScreen()

    assert screen.provider.name == "mistral"
    assert screen.provider.api_key_env_var == "MISTRAL_API_KEY"


@pytest.mark.asyncio
async def test_ui_manual_api_key_screen_uses_configured_vibe_url() -> None:
    app = OnboardingApp(
        config=_build_onboarding_config(vibe_base_url="https://vibe.example.com/")
    )

    async with app.run_test() as pilot:
        await _show_manual_api_key_screen(pilot)

        provider_link = app.screen.query_one("#api-key-provider-link", Link)

    assert provider_link.url == "https://vibe.example.com/code/extensions?focus=key"


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


def test_persist_api_key_sends_onboarding_telemetry_with_launch_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_metadata: dict[str, str] = {}

    def capture(self: TelemetryClient) -> None:
        recorded_metadata.update(self.build_client_event_metadata())

    monkeypatch.setattr(TelemetryClient, "send_onboarding_api_key_added", capture)

    provider = ProviderConfig(
        name="mistral",
        api_base="https://inference.mistral.test/v1",
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    )

    result = persist_api_key(
        provider,
        "secret",
        launch_context=build_launch_context(
            agent_entrypoint="cli",
            agent_version="1.0.0",
            client_name="vibe_cli",
            client_version="1.0.0",
            terminal_emulator=TerminalEmulator.APPLE_TERMINAL,
        ),
    )

    assert result == "completed"
    assert recorded_metadata["agent_entrypoint"] == "cli"
    assert recorded_metadata["agent_version"] == "1.0.0"
    assert recorded_metadata["client_name"] == "vibe_cli"
    assert recorded_metadata["client_version"] == "1.0.0"
    assert recorded_metadata["terminal_emulator"] == "apple_terminal"
    assert "session_id" not in recorded_metadata
