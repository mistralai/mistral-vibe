from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from textual.pilot import Pilot
from textual.widgets import Input

from tests.browser_sign_in.stubs import build_browser_sign_in_service_factory
from tests.conftest import build_test_vibe_config
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.config._settings import (
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
)
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.telemetry.build_metadata import build_entrypoint_metadata
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.types import Backend
from vibe.setup.auth import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInService,
    BrowserSignInStatus,
)
import vibe.setup.onboarding as onboarding_module
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen, persist_api_key
from vibe.setup.onboarding.screens.auth_method import AuthMethodScreen
from vibe.setup.onboarding.screens.browser_sign_in import (
    ERROR_HINT,
    PENDING_HINT,
    UNEXPECTED_ERROR_MESSAGE,
    BrowserSignInScreen,
)

CONSOLE_URL = "https://console.mistral.ai"
API_URL = "https://api.mistral.ai"


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
            raise AssertionError("Timed out waiting for condition.")


def _build_onboarding_config(
    *,
    provider_name: str = "mistral",
    model_provider: str | None = None,
    backend: Backend = Backend.MISTRAL,
    api_key_env_var: str = "MISTRAL_API_KEY",
    browser_auth_base_url: str | None = None,
    browser_auth_api_base_url: str | None = None,
    enable_experimental_browser_sign_in: bool = False,
) -> VibeConfig:
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
        providers=[provider],
        models=[model],
        enable_experimental_browser_sign_in=enable_experimental_browser_sign_in,
    )


def _build_browser_onboarding_app(
    *, browser_sign_in_service_factory: Callable[[], BrowserSignInService] | None = None
) -> OnboardingApp:
    return OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=API_URL,
            enable_experimental_browser_sign_in=True,
        ),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
    )


def _patch_failing_browser_sign_in_service(
    monkeypatch: pytest.MonkeyPatch, captured_base_urls: list[tuple[str, str]]
) -> None:
    class FakeGateway:
        def __init__(self, browser_base_url: str, api_base_url: str) -> None:
            captured_base_urls.append((browser_base_url, api_base_url))

    class FakeService:
        def __init__(self, gateway: FakeGateway) -> None:
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


def _saved_env_contents() -> str:
    return GLOBAL_ENV_FILE.path.read_text(encoding="utf-8")


def _build_unexpected_browser_sign_in_service_factory(
    outcomes: list[str],
    *,
    api_key: str = "sk-browser-onboarding-test-key",
    close_blocker: asyncio.Event | None = None,
    close_started: asyncio.Event | None = None,
    close_finished: asyncio.Event | None = None,
    close_cancelled: asyncio.Event | None = None,
) -> Callable[[], BrowserSignInService]:
    remaining_outcomes = list(outcomes)

    class UnexpectedBrowserSignInService:
        def __init__(self, outcome: str) -> None:
            self._outcome = outcome

        async def authenticate(
            self, status_callback: Callable[[BrowserSignInStatus], None] | None = None
        ) -> str:
            if self._outcome == "completed":
                if status_callback is not None:
                    status_callback(BrowserSignInStatus.COMPLETED)
                return api_key
            if self._outcome == "runtime_error":
                raise RuntimeError("boom")
            msg = f"Unsupported browser sign-in outcome: {self._outcome}"
            raise AssertionError(msg)

        async def aclose(self) -> None:
            try:
                if close_started is not None:
                    close_started.set()
                if close_blocker is not None:
                    await close_blocker.wait()
            except asyncio.CancelledError:
                if close_cancelled is not None:
                    close_cancelled.set()
                raise
            finally:
                if close_finished is not None:
                    close_finished.set()

            return None

    def build_service() -> BrowserSignInService:
        if not remaining_outcomes:
            msg = (
                "Unexpected browser sign-in service factory requires scripted outcomes."
            )
            raise AssertionError(msg)
        return cast(
            BrowserSignInService,
            UnexpectedBrowserSignInService(remaining_outcomes.pop(0)),
        )

    return build_service


async def _pass_welcome_screen(pilot: Pilot) -> None:
    welcome_screen = pilot.app.get_screen("welcome")
    await _wait_for(
        lambda: not welcome_screen.query_one("#enter-hint").has_class("hidden"), pilot
    )
    await pilot.press("enter")


async def _show_auth_method(pilot: Pilot) -> None:
    await _pass_welcome_screen(pilot)
    await _wait_for(lambda: isinstance(pilot.app.screen, AuthMethodScreen), pilot)


async def _show_browser_sign_in(pilot: Pilot) -> None:
    await _show_auth_method(pilot)
    await pilot.press("enter")
    await _wait_for(lambda: isinstance(pilot.app.screen, BrowserSignInScreen), pilot)


@pytest.mark.asyncio
async def test_ui_keeps_manual_flow_when_browser_sign_in_is_unsupported() -> None:
    app = OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url="",
            browser_auth_api_base_url="",
            enable_experimental_browser_sign_in=True,
        )
    )
    api_key_value = "sk-onboarding-test-key"

    async with app.run_test() as pilot:
        await _pass_welcome_screen(pilot)
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)
        input_widget = app.screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        assert input_widget.value == api_key_value
        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert api_key_value in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_hides_browser_sign_in_when_experimental_flag_is_disabled() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"]
    )
    app = OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url=CONSOLE_URL, browser_auth_api_base_url=API_URL
        ),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
    )

    assert app.supports_browser_sign_in is False

    async with app.run_test() as pilot:
        await _pass_welcome_screen(pilot)
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)


@pytest.mark.asyncio
async def test_ui_supports_browser_sign_in_when_experimental_flag_is_enabled() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"]
    )
    app = OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=API_URL,
            enable_experimental_browser_sign_in=True,
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
            browser_auth_api_base_url=API_URL,
            enable_experimental_browser_sign_in=True,
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
async def test_ui_completes_browser_sign_in_and_retries_after_failure() -> None:
    gateway, browser_sign_in_service_factory, created_services = (
        build_browser_sign_in_service_factory(outcomes=["expired", "completed"])
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "expired"
                in str(app.screen.query_one("#browser-sign-in-status").render())
            ),
            pilot,
        )
        await pilot.press("r")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert gateway.process_number == 2
    assert len(created_services) == 2
    assert created_services[0] is not created_services[1]
    assert app.return_value == "completed"
    assert "sk-browser-onboarding-test-key" in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_browser_sign_in_falls_back_to_mistral_env_var_when_missing() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"]
    )
    app = OnboardingApp(
        config=_build_onboarding_config(
            provider_name="custom-mistral",
            api_key_env_var="",
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=API_URL,
            enable_experimental_browser_sign_in=True,
        ),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    env_contents = _saved_env_contents()
    assert "MISTRAL_API_KEY" in env_contents
    assert "sk-browser-onboarding-test-key" in env_contents


@pytest.mark.asyncio
async def test_ui_shows_human_message_when_polling_fails() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["poll_failed"]
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "We couldn't complete sign-in. Please try again."
                in str(app.screen.query_one("#browser-sign-in-status").render())
            ),
            pilot,
        )


@pytest.mark.asyncio
async def test_ui_shows_retryable_error_when_browser_sign_in_fails_unexpectedly() -> (
    None
):
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory([
            "runtime_error"
        ])
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                UNEXPECTED_ERROR_MESSAGE
                in str(app.screen.query_one("#browser-sign-in-status").render())
            ),
            pilot,
        )

        assert isinstance(app.screen, BrowserSignInScreen)
        status_widget = app.screen.query_one("#browser-sign-in-status")
        assert status_widget.has_class("error")
        assert not status_widget.has_class("pending")
        assert ERROR_HINT in str(app.screen.query_one("#browser-sign-in-hint").render())
        assert app.return_value is None


@pytest.mark.asyncio
async def test_ui_retries_after_unexpected_browser_sign_in_failure() -> None:
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory([
            "runtime_error",
            "completed",
        ])
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                UNEXPECTED_ERROR_MESSAGE
                in str(app.screen.query_one("#browser-sign-in-status").render())
            ),
            pilot,
        )
        await pilot.press("r")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert "sk-browser-onboarding-test-key" in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_waits_for_browser_sign_in_cleanup_before_retrying() -> None:
    close_started = asyncio.Event()
    close_blocker = asyncio.Event()
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory(
            ["runtime_error", "completed"],
            close_blocker=close_blocker,
            close_started=close_started,
        )
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(close_started.is_set, pilot)

        status_widget = app.screen.query_one("#browser-sign-in-status")
        hint_widget = app.screen.query_one("#browser-sign-in-hint")
        await _wait_for(
            lambda: "Getting things ready..." in str(status_widget.render()), pilot
        )
        await _wait_for(lambda: PENDING_HINT in str(hint_widget.render()), pilot)

        await pilot.press("r")
        await _wait_for(
            lambda: "Getting things ready..." in str(status_widget.render()), pilot
        )
        await _wait_for(lambda: PENDING_HINT in str(hint_widget.render()), pilot)
        assert app.return_value is None

        close_blocker.set()
        await _wait_for(
            lambda: (
                UNEXPECTED_ERROR_MESSAGE
                in str(app.screen.query_one("#browser-sign-in-status").render())
            ),
            pilot,
        )

        await pilot.press("r")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert "sk-browser-onboarding-test-key" in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_switches_to_manual_path_without_cancelling_browser_sign_in_cleanup() -> (
    None
):
    close_started = asyncio.Event()
    close_blocker = asyncio.Event()
    close_finished = asyncio.Event()
    close_cancelled = asyncio.Event()
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory(
            ["runtime_error"],
            close_blocker=close_blocker,
            close_started=close_started,
            close_finished=close_finished,
            close_cancelled=close_cancelled,
        )
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(close_started.is_set, pilot)

        await pilot.press("m")
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)

        close_blocker.set()
        await _wait_for(close_finished.is_set, pilot)

    assert close_cancelled.is_set() is False


@pytest.mark.asyncio
async def test_ui_switches_to_manual_path_while_browser_sign_in_is_running() -> None:
    blocker = asyncio.Event()

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    gateway, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], sleep=wait_forever
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory
    )
    api_key_value = "sk-manual-after-browser-cancel"

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "Waiting for you to finish signing in..."
                in str(app.screen.query_one("#browser-sign-in-status").render())
            ),
            pilot,
        )
        status_widget = app.screen.query_one("#browser-sign-in-status")
        assert status_widget.has_class("pending")
        assert not status_widget.has_class("error")
        step_widgets = list(app.screen.query(".browser-sign-in-step"))
        assert len(step_widgets) == 3
        assert step_widgets[0].has_class("done")
        assert "Open your browser" in str(step_widgets[0].render())
        assert step_widgets[1].has_class("active")
        assert "Sign in and return here" in str(step_widgets[1].render())
        assert step_widgets[2].has_class("idle")
        assert "Finish setup" in str(step_widgets[2].render())
        await pilot.press("m")
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)
        await pilot.press(*api_key_value)
        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert gateway.closed is True
    assert gateway.exchange_requests == []
    env_contents = _saved_env_contents()
    assert api_key_value in env_contents
    assert "sk-browser-onboarding-test-key" not in env_contents


@pytest.mark.asyncio
async def test_ui_uses_default_mistral_browser_auth_urls_when_experiment_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_base_urls: list[tuple[str, str]] = []
    _patch_failing_browser_sign_in_service(monkeypatch, captured_base_urls)

    app = OnboardingApp(
        config=build_test_vibe_config(enable_experimental_browser_sign_in=True)
    )

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
async def test_ui_enables_browser_sign_in_from_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_ENABLE_EXPERIMENTAL_BROWSER_SIGN_IN", "true")
    captured_base_urls: list[tuple[str, str]] = []
    _patch_failing_browser_sign_in_service(monkeypatch, captured_base_urls)

    app = OnboardingApp()

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
async def test_ui_keeps_browser_sign_in_disabled_from_false_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_ENABLE_EXPERIMENTAL_BROWSER_SIGN_IN", "false")

    app = OnboardingApp()

    assert app.supports_browser_sign_in is False

    async with app.run_test() as pilot:
        await _pass_welcome_screen(pilot)
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)


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
            "enable_experimental_browser_sign_in = true",
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
            "enable_experimental_browser_sign_in = true",
            "",
            "[[providers]]",
            'name = "mistral"',
            'api_base = "https://api.mistral.ai/v1"',
            'api_key_env_var = "MISTRAL_API_KEY"',
            'browser_auth_base_url = "https://console.mistral.ai"',
            'browser_auth_api_base_url = "https://api.mistral.ai"',
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


def test_persist_api_key_sends_onboarding_telemetry_with_entrypoint_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_metadata: dict[str, str] = {}

    def capture(self: TelemetryClient) -> None:
        recorded_metadata.update(self.build_client_event_metadata())

    monkeypatch.setattr(TelemetryClient, "send_onboarding_api_key_added", capture)

    provider = ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    )

    result = persist_api_key(
        provider,
        "secret",
        entrypoint_metadata=build_entrypoint_metadata(
            agent_entrypoint="cli",
            agent_version="1.0.0",
            client_name="vibe_cli",
            client_version="1.0.0",
        ),
    )

    assert result == "completed"
    assert recorded_metadata["agent_entrypoint"] == "cli"
    assert recorded_metadata["agent_version"] == "1.0.0"
    assert recorded_metadata["client_name"] == "vibe_cli"
    assert recorded_metadata["client_version"] == "1.0.0"
    assert "session_id" not in recorded_metadata
